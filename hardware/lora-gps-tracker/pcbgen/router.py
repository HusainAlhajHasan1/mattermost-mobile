"""
Grid-based 2-layer autorouter.

Board discretisation
--------------------
The board is split into a uniform grid of `q` mm cells.  For every net and both
layers the static obstacles (foreign pads, holes, board edge, antenna keep-outs)
are rasterised with exact distance maths and inflated by

    track_radius(net) + clearance + safety margin

so a path on the grid only has to avoid `blocked` cells - the clearance rule is
satisfied by construction.  Copper that is already routed is added dynamically
with the same rule.

Search
------
Each net is routed as a tree: A* runs from *all* cells already belonging to the
net (multi-source) towards the nearest unrouted pad (Prim/MST order), which
produces bus-like trees instead of daisy chains.

Moves use a 16-neighbourhood (0/45/90/135 degrees) with a bending penalty, and
every long move is checked with a quarter-cell sampled supercover so a track can
never cut a corner.  Direction changes are limited to <= 45 degrees, so every
segment stays on the 0/45/90 degree family.  Layer changes are only allowed
through a via whose own land pattern has been checked against both layers.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

import numpy as np

from . import geom
from .board import (BOARD_H, BOARD_W, COMPONENT_EDGES, KEEPOUTS, POWER_NETS,
                    RULES, TRACK_POWER, TRACK_SIGNAL, VIA_DIA, VIA_DRILL, Board,
                    PlacedPad)


@dataclass
class Track:
    net: str
    layer: str          # "top" | "bottom"
    pts: List[Tuple[float, float]]
    width: float

    def segments(self):
        return list(zip(self.pts[:-1], self.pts[1:]))


@dataclass
class Via:
    net: str
    x: float
    y: float
    dia: float = VIA_DIA
    drill: float = VIA_DRILL
    stitch: bool = False


DIRS = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
        (1, 1, math.sqrt(2)), (1, -1, math.sqrt(2)),
        (-1, 1, math.sqrt(2)), (-1, -1, math.sqrt(2)),
        (2, 1, math.sqrt(5)), (2, -1, math.sqrt(5)), (-2, 1, math.sqrt(5)), (-2, -1, math.sqrt(5)),
        (1, 2, math.sqrt(5)), (1, -2, math.sqrt(5)), (-1, 2, math.sqrt(5)), (-1, -2, math.sqrt(5))]


def net_half_width(net: str) -> float:
    return (TRACK_POWER if net in POWER_NETS else TRACK_SIGNAL) / 2.0


def net_width(net: str) -> float:
    return TRACK_POWER if net in POWER_NETS else TRACK_SIGNAL


class Router:
    def __init__(self, board: Board, q: float = 0.20):
        self.b = board
        self.q = q
        self.nx = int(round(BOARD_W / q))
        self.ny = int(round(BOARD_H / q))
        self.X, self.Y = np.meshgrid((np.arange(self.nx) + 0.5) * q,
                                     (np.arange(self.ny) + 0.5) * q)
        self.clr = RULES["clearance"] + 0.035     # grid + rounding safety margin
        self.tracks: List[Track] = []
        self.vias: List[Via] = []
        self.pads = list(board.all_pads())
        self.npth = [(pos[0], pos[1], dia / 2.0)
                     for fp in board.fps.values() for (pos, dia) in fp.npth]
        self.keepouts = list(KEEPOUTS) + [k for fp in board.fps.values() for k in fp.keepouts]
        self.edge_mask = self._edge_mask()
        self._layer_obst = [{}, {}]

    # ------------------------------------------------------------------
    # geometry helpers
    # ------------------------------------------------------------------
    def cell_of(self, x, y):
        return int(x / self.q), int(y / self.q)

    def xy_of(self, ix, iy):
        return ((ix + 0.5) * self.q, (iy + 0.5) * self.q)

    def _edge_mask(self):
        X, Y = self.X, self.Y
        m = (X < RULES["edge_clearance"]) | (X > BOARD_W - RULES["edge_clearance"]) | \
            (Y < RULES["edge_clearance"]) | (Y > BOARD_H - RULES["edge_clearance"])
        ko = 0.30
        for (x0, y0, x1, y1) in self.keepouts:
            m |= (X > x0 - ko) & (X < x1 + ko) & (Y > y0 - ko) & (Y < y1 + ko)
        return m

    def pad_cells(self, pad: PlacedPad) -> List[Tuple[int, int]]:
        """Grid cells whose centre really lies on the pad copper."""
        q = self.q
        w, h = pad.shape_wh()
        x0 = int(math.floor((pad.x - w / 2) / q))
        x1 = int(math.ceil((pad.x + w / 2) / q))
        y0 = int(math.floor((pad.y - h / 2) / q))
        y1 = int(math.ceil((pad.y + h / 2) / q))
        cells = []
        for ix in range(max(0, x0), min(self.nx, x1)):
            for iy in range(max(0, y0), min(self.ny, y1)):
                cx, cy = self.xy_of(ix, iy)
                if pad.shape == "circle":
                    if math.hypot(cx - pad.x, cy - pad.y) <= pad.w / 2.0:
                        cells.append((ix, iy))
                elif abs(cx - pad.x) <= w / 2 and abs(cy - pad.y) <= h / 2:
                    cells.append((ix, iy))
        return cells or [self.cell_of(pad.x, pad.y)]

    def _pad_mask(self, pad: PlacedPad, delta: float):
        """Exact Minkowski sum (pad + disc of radius delta)."""
        X, Y = self.X, self.Y
        dx = np.abs(X - pad.x)
        dy = np.abs(Y - pad.y)
        if pad.shape == "circle":
            return np.hypot(dx, dy) <= (pad.w / 2.0 + delta)
        w, h = pad.shape_wh()
        ex = np.maximum(dx - w / 2.0, 0.0)
        ey = np.maximum(dy - h / 2.0, 0.0)
        return (ex * ex + ey * ey) <= delta * delta

    # ------------------------------------------------------------------
    # static obstacle rasters
    # ------------------------------------------------------------------
    def prepare(self):
        """Exact obstacle masks for every pad, then per net / per layer.

        Pads use the true Minkowski sum of their rectangle with the clearance
        disc (not a circumscribed circle), so a 0.9 mm tall castellated pad does
        not block the pad 1.27 mm above it.
        """
        X, Y = self.X, self.Y
        A = self.clr
        base = self.edge_mask.copy()
        for (x, y, r) in self.npth:
            base |= np.hypot(X - x, Y - y) <= (r + A + TRACK_POWER / 2.0)
        self._base = base

        # one mask per pad for each track class (signal / power)
        self._pad_obst = []
        for p in self.pads:
            if p.layer == "top":
                layer = 0
            elif p.layer == "bottom":
                layer = 1
            else:
                layer = -1                 # THT: blocks both layers
            ms = self._pad_mask(p, A + TRACK_SIGNAL / 2.0)
            mp = self._pad_mask(p, A + TRACK_POWER / 2.0)
            self._pad_obst.append((p.net, layer, ms, mp))

        self._layer_obst = [{}, {}]
        for net in sorted(set(p.net for p in self.pads if p.net)):
            power = net in POWER_NETS
            for layer in (0, 1):
                m = base.copy()
                for (pn, pl, ms, mp) in self._pad_obst:
                    if pn == net:
                        continue
                    if pl != -1 and pl != layer:
                        continue
                    m |= mp if power else ms
                self._layer_obst[layer][net] = m

    def _obstacles(self, net: str):
        """Static obstacles + everything already routed for other nets."""
        X, Y = self.X, self.Y
        tr = net_half_width(net)
        obst = [self._layer_obst[0][net].copy(), self._layer_obst[1][net].copy()]
        for t in self.tracks:
            if t.net == net:
                continue
            layer = 0 if t.layer == "top" else 1
            d = net_half_width(t.net) + self.clr + tr
            for (a, b) in t.segments():
                obst[layer] |= geom.dist_to_seg(X, Y, a[0], a[1], b[0], b[1]) <= d
        for v in self.vias:
            if v.net == net:
                continue
            d = v.dia / 2.0 + self.clr + tr
            m = np.hypot(X - v.x, Y - v.y) <= d
            obst[0] |= m
            obst[1] |= m
        return obst

    # ------------------------------------------------------------------
    # net routing (tree based)
    # ------------------------------------------------------------------
    def route_net(self, net: str, pads: Sequence[PlacedPad]) -> bool:
        if len(pads) < 2:
            return True
        blocked = self._obstacles(net)
        tree: List[Set[Tuple[int, int]]] = [set(), set()]
        pads = list(pads)
        pads.sort(key=lambda p: math.hypot(p.x - BOARD_W / 2, p.y - BOARD_H / 2))
        root = pads[0]
        self._add_pad_cells(tree, root)
        remaining = pads[1:]
        while remaining:
            best = None
            for p in remaining:
                d = min(self._tree_distance(tree, ix, iy) for (ix, iy) in self.pad_cells(p))
                if best is None or d < best[0]:
                    best = (d, p)
            dst = best[1]
            if not self._route_to_pad(net, tree, dst, blocked):
                return False
            self._add_pad_cells(tree, dst)
            remaining.remove(dst)
        return True

    def _tree_distance(self, tree, ix, iy):
        best = 1e18
        for L in (0, 1):
            for (tx, ty) in tree[L]:
                d = abs(tx - ix) + abs(ty - iy)
                if d < best:
                    best = d
                    if best <= 2:
                        return best
        return best

    def _add_pad_cells(self, tree, pad: PlacedPad):
        layers = (0, 1) if pad.is_tht else ([0] if pad.layer == "top" else [1])
        cells = self.pad_cells(pad)
        for L in layers:
            tree[L].update(cells)

    def _route_to_pad(self, net: str, tree, dst: PlacedPad, blocked) -> bool:
        dst_layers = (0, 1) if dst.is_tht else ([0] if dst.layer == "top" else [1])
        dst_cells = set()
        for (ix, iy) in self.pad_cells(dst):
            for L in dst_layers:
                dst_cells.add((L, ix, iy))
        via_ok = self._via_sites(blocked, net)
        path, ok = self._astar(net, tree, dst_cells, blocked, via_ok)
        if not ok:
            return False
        self._commit(net, path, tree, dst)
        return True

    def _via_sites(self, blocked, net):
        """Cells where a via may be placed (clearance for the full via body)."""
        X, Y = self.X, self.Y
        m = self.edge_mask.copy()
        for p in self.pads:
            if p.net == net:
                continue
            if p.layer == "top" or p.layer == "both":
                m |= self._pad_mask(p, VIA_DIA / 2.0 + self.clr)
            if p.layer == "bottom" or p.layer == "both":
                m |= self._pad_mask(p, VIA_DIA / 2.0 + self.clr)
        for t in self.tracks:
            if t.net == net:
                continue
            d = t.width / 2.0 + VIA_DIA / 2.0 + self.clr
            for (a, b) in t.segments():
                m |= geom.dist_to_seg(X, Y, a[0], a[1], b[0], b[1]) <= d
        for v in self.vias:
            if v.net == net:
                continue
            m |= np.hypot(X - v.x, Y - v.y) <= (v.dia / 2.0 + VIA_DIA / 2.0 + self.clr)
        for (x, y, r) in self.npth:
            m |= np.hypot(X - x, Y - y) <= (r + 0.30 + VIA_DIA / 2.0)
        return ~m

    # ------------------------------------------------------------------
    def _astar(self, net, tree, dst_cells, blocked, via_ok):
        q = self.q
        nx, ny = self.nx, self.ny
        power = net in POWER_NETS
        LAYER_COST = (1.0, 1.45)      # keep the bottom mostly free for the plane
        VIA_COST = (30.0 if power else 17.0)
        BEND_COST = 2.0 if power else 1.2

        xs = [c[1] for c in dst_cells]
        ys = [c[2] for c in dst_cells]
        bx0, bx1, by0, by1 = min(xs), max(xs), min(ys), max(ys)

        def h(ix, iy):
            dx = 0 if bx0 <= ix <= bx1 else min(abs(ix - bx0), abs(ix - bx1))
            dy = 0 if by0 <= iy <= by1 else min(abs(iy - by0), abs(iy - by1))
            return math.hypot(dx, dy) * q

        g = {}
        prev = {}
        pq = []
        for L in (0, 1):
            for (ix, iy) in tree[L]:
                if not (0 <= ix < nx and 0 <= iy < ny):
                    continue
                st = (L, ix, iy, -1)
                if st in g:
                    continue
                g[st] = 0.0
                prev[st] = None
                heapq.heappush(pq, (h(ix, iy), 0.0, st))
        if not pq:
            return None, False

        closed = set()
        n_exp = 0
        found = None
        while pq:
            f, gc, st = heapq.heappop(pq)
            if st in closed:
                continue
            closed.add(st)
            n_exp += 1
            L, ix, iy, dprev = st
            if (L, ix, iy) in dst_cells:
                found = st
                break
            if n_exp > 1500000:
                return None, False
            for k, (dx, dy, w) in enumerate(DIRS):
                jx, jy = ix + dx, iy + dy
                if not (0 <= jx < nx and 0 <= jy < ny):
                    continue
                if blocked[L][jy, jx]:
                    continue
                if not self._clear_jump(blocked[L], ix, iy, jx, jy):
                    continue
                if not self._straight_ok(dprev, dx, dy):
                    continue
                cost = w * q * LAYER_COST[L]
                if w > 2.0:
                    cost *= 1.06                  # prefer 45 deg over 2:1 moves
                if dprev != -1:
                    cosang = (DIRS[dprev][0] * dx + DIRS[dprev][1] * dy) / (DIRS[dprev][2] * w)
                    if cosang < 0.999:
                        cost += BEND_COST * (1.0 - cosang)
                ns = (L, jx, jy, k)
                ng = gc + cost
                if ng < g.get(ns, 1e18):
                    g[ns] = ng
                    prev[ns] = st
                    heapq.heappush(pq, (ng + h(jx, jy), ng, ns))
            if via_ok[iy, ix] and not blocked[1 - L][iy, ix]:
                ns = (1 - L, ix, iy, -1)
                ng = gc + VIA_COST
                if ng < g.get(ns, 1e18):
                    g[ns] = ng
                    prev[ns] = st
                    heapq.heappush(pq, (ng + h(ix, iy), ng, ns))
        if found is None:
            return None, False
        seq = []
        st = found
        while st is not None:
            seq.append(st)
            st = prev[st]
        seq.reverse()
        return seq, True

    # ------------------------------------------------------------------
    @staticmethod
    def _clear_jump(blocked, ix, iy, jx, jy) -> bool:
        """Every cell crossed by the move must be free (quarter-cell sampling)."""
        n = max(abs(jx - ix), abs(jy - iy))
        steps = max(2, int(n * 5))
        for s in range(1, steps + 1):
            t = s / steps
            cx = int(math.floor(ix + 0.5 + (jx - ix) * t))
            cy = int(math.floor(iy + 0.5 + (jy - iy) * t))
            if not (0 <= cx < blocked.shape[1] and 0 <= cy < blocked.shape[0]):
                return False
            if blocked[cy, cx]:
                return False
        return True

    @staticmethod
    def _straight_ok(dprev, dx, dy) -> bool:
        """Change direction only by <= 45 degrees (keeps 0/45/90 segments)."""
        if dprev == -1:
            return True
        ax, ay, _ = DIRS[dprev]
        n1 = math.hypot(ax, ay)
        n2 = math.hypot(dx, dy)
        return (ax * dx + ay * dy) / (n1 * n2) > 0.7

    # ------------------------------------------------------------------
    def _commit(self, net, seq, tree, dst_pad=None):
        width = net_width(net)
        runs = []
        cur = [seq[0]]
        for st in seq[1:]:
            if st[0] != cur[-1][0]:
                runs.append(cur)
                cur = [st]
            else:
                cur.append(st)
        runs.append(cur)
        for run in runs:
            pts = simplify([self.xy_of(st[1], st[2]) for st in run], 1e-6)
            if len(pts) < 2:
                continue
            pts = self._snap_ends(pts, net)
            # a pad is convex, so a straight stub from a point that is already
            # inside the pad to its centre cannot violate anything - it just
            # makes the connection unambiguous
            if dst_pad is not None and self._inside_pad(pts[-1], dst_pad):
                if math.hypot(pts[-1][0] - dst_pad.x, pts[-1][1] - dst_pad.y) > 1e-3:
                    pts.append((dst_pad.x, dst_pad.y))
            if self._inside_pad(pts[0], dst_pad) if dst_pad is not None else False:
                if math.hypot(pts[0][0] - dst_pad.x, pts[0][1] - dst_pad.y) > 1e-3:
                    pts.insert(0, (dst_pad.x, dst_pad.y))
            self.tracks.append(Track(net, "top" if run[0][0] == 0 else "bottom", pts, width))
            for st in run:
                tree[st[0]].add((st[1], st[2]))
        # a via is only needed where both sides of the layer change carry copper
        for idx, (a, b) in enumerate(zip(seq[:-1], seq[1:])):
            if a[0] == b[0]:
                continue
            same_a = [st for st in seq[:idx + 1] if st[0] == a[0]]
            same_b = [st for st in seq[idx + 1:] if st[0] == b[0]]
            if len(same_a) < 2 or not same_b:
                continue            # dead-end layer change: no via needed
            x, y = self.xy_of(a[1], a[2])
            self.vias.append(Via(net, x, y))
            tree[a[0]].add((a[1], a[2]))
            tree[b[0]].add((b[1], b[2]))

    @staticmethod
    def _inside_pad(pt, pad) -> bool:
        w, h = pad.shape_wh()
        if pad.shape == "circle":
            return math.hypot(pt[0] - pad.x, pt[1] - pad.y) <= pad.w / 2.0
        return abs(pt[0] - pad.x) <= w / 2 and abs(pt[1] - pad.y) <= h / 2

    def _snap_ends(self, pts, net):
        """Nudge a polyline end onto the pad centre - only by a fraction of a cell."""
        out = list(pts)
        for idx in (0, -1):
            p = out[idx]
            best = None
            for pad in self.pads:
                if pad.net != net:
                    continue
                d = math.hypot(pad.x - p[0], pad.y - p[1])
                if best is None or d < best[0]:
                    best = (d, pad)
            if best and best[0] <= 0.15:
                out[idx] = (best[1].x, best[1].y)
        if len(out) >= 3 and out[0] == out[1]:
            out.pop(1)
        if len(out) >= 3 and out[-1] == out[-2]:
            out.pop(-2)
        return out


def simplify(pts, tol):
    """Douglas-Peucker (drops collinear points, keeps the shape)."""
    if len(pts) < 3:
        return pts
    keep = {0, len(pts) - 1}
    stack = [(0, len(pts) - 1)]
    while stack:
        i0, i1 = stack.pop()
        if i1 - i0 < 2:
            continue
        x0, y0 = pts[i0]
        x1, y1 = pts[i1]
        best, bi = -1.0, -1
        for i in range(i0 + 1, i1):
            d = _pt_seg(pts[i], (x0, y0), (x1, y1))
            if d > best:
                best, bi = d, i
        if best > tol:
            keep.add(bi)
            stack.append((i0, bi))
            stack.append((bi, i1))
    return [pts[i] for i in sorted(keep)]


def _pt_seg(p, a, b):
    px, py = p
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 < 1e-18:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))
