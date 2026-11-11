"""
Ground pour generation.

The pour is computed on the same 0.2 mm grid as the router:

    allowed = board interior (inset by the edge clearance rule)
              - antenna / IPEX keep-outs   (plus a safety ring)
              - every non-GND pad, track, via and drill (clearance + margin)
              - a thermal-relief ring around every GND pad
              + exactly four thermal spokes per GND pad, themselves clipped by
                the obstacles above so a spoke can never short to a track

The mask is converted to polygons by merging horizontal cell runs with shapely,
which gives exact, valid outlines.  Only islands that really connect to the
ground net (a GND pad or a stitching via) are kept, mirroring "remove islands"
in a CAD tool.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np
from scipy.ndimage import label

from . import geom
from .board import (BOARD_H, BOARD_W, KEEPOUTS, POWER_NETS, RULES, TRACK_POWER,
                    TRACK_SIGNAL, Board)
from .router import Track, Via

THERMAL_GAP = 0.30
THERMAL_SPOKE_W = 0.40
STITCH_SPACING = 2.4


@dataclass
class PourPolygon:
    outer: List[Tuple[float, float]]
    holes: List[List[Tuple[float, float]]] = field(default_factory=list)


@dataclass
class Pour:
    layer: str
    polys: List[PourPolygon]
    area: float

    def resolved(self):
        """Valid shapely polygons (exterior + interior rings)."""
        from shapely.geometry import Polygon
        from shapely.ops import unary_union
        outers = []
        for pp in self.polys:
            if len(pp.outer) < 3:
                continue
            g = Polygon(pp.outer)
            if not g.is_valid:
                g = g.buffer(0)
            if not g.is_empty:
                outers.append(g)
        if not outers:
            return []
        uni = unary_union(outers)
        holes = []
        for pp in self.polys:
            for h in pp.holes:
                if len(h) < 3:
                    continue
                g = Polygon(h)
                if not g.is_valid:
                    g = g.buffer(0)
                if not g.is_empty:
                    holes.append(g)
        if holes:
            uni = uni.difference(unary_union(holes))
        if uni.is_empty:
            return []
        geoms = list(uni.geoms) if uni.geom_type == "MultiPolygon" else [uni]
        return [g for g in geoms if g.area > 0.05]


# --------------------------------------------------------------------------
def keepout_rects(board: Board):
    return list(KEEPOUTS) + [k for fp in board.fps.values() for k in fp.keepouts]


def build_mask(board: Board, tracks: List[Track], vias: List[Via],
               stitch_vias: List[Via], q: float, layer: str,
               margin: Optional[float] = None, thermal=True, extra_r: float = 0.0):
    """Boolean grid: True where ground copper is allowed.

    `extra_r` inflates every obstacle by an extra radius - used when the caller
    wants to place something with a physical size (a stitching via) rather than
    plain copper.
    """
    nx, ny = int(round(BOARD_W / q)), int(round(BOARD_H / q))
    X, Y = np.meshgrid((np.arange(nx) + 0.5) * q, (np.arange(ny) + 0.5) * q)
    if margin is None:
        margin = 0.5 * q + 0.05
    clr = RULES["clearance"]

    allowed = ((X > RULES["edge_clearance"] + margin) &
               (X < BOARD_W - RULES["edge_clearance"] - margin) &
               (Y > RULES["edge_clearance"] + margin) &
               (Y < BOARD_H - RULES["edge_clearance"] - margin))

    hard = np.ones_like(allowed)
    ko = 0.30 + margin
    for (x0, y0, x1, y1) in keepout_rects(board):
        hard &= ~((X > x0 - ko) & (X < x1 + ko) & (Y > y0 - ko) & (Y < y1 + ko))

    for p in board.all_pads():
        if p.net == "GND":
            continue
        w, h = p.shape_wh()
        dx = np.maximum(np.abs(X - p.x) - w / 2.0, 0.0)
        dy = np.maximum(np.abs(Y - p.y) - h / 2.0, 0.0)
        hard &= np.sqrt(dx * dx + dy * dy) > (clr + margin + extra_r)

    for t in tracks:
        if t.net == "GND":
            continue
        d = t.width / 2.0 + clr + margin + extra_r
        for a, b in zip(t.pts[:-1], t.pts[1:]):
            hard &= geom.dist_to_seg(X, Y, a[0], a[1], b[0], b[1]) > d

    for v in vias:
        if v.net == "GND":
            continue
        hard &= np.hypot(X - v.x, Y - v.y) > (v.dia / 2.0 + clr + margin + extra_r)

    for fp in board.fps.values():
        for (pos, dia) in fp.npth:
            hard &= np.hypot(X - pos[0], Y - pos[1]) > (dia / 2.0 + 0.5 + margin + extra_r)

    mask = allowed & hard

    if thermal:
        spokes = np.zeros_like(mask)
        ring = np.ones_like(mask)
        for p in board.all_pads():
            if p.net != "GND":
                continue
            w, h = p.shape_wh()
            dx = np.maximum(np.abs(X - p.x) - w / 2.0, 0.0)
            dy = np.maximum(np.abs(Y - p.y) - h / 2.0, 0.0)
            ring &= np.sqrt(dx * dx + dy * dy) > (THERMAL_GAP + margin)
            L = max(w, h) * 0.8 + THERMAL_GAP + 0.25
            hw = THERMAL_SPOKE_W / 2.0
            for ddx, ddy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                if ddx:
                    x0, x1 = sorted([p.x, p.x + ddx * L])
                    y0, y1 = p.y - hw, p.y + hw
                else:
                    x0, x1 = p.x - hw, p.x + hw
                    y0, y1 = sorted([p.y, p.y + ddy * L])
                spokes |= (X >= x0) & (X <= x1) & (Y >= y0) & (Y <= y1)
        mask = (mask & ring) | (spokes & ring & hard)
    return mask


# --------------------------------------------------------------------------
def mask_to_polygons(mask: np.ndarray, q: float, tol: float = 0.02):
    """Boolean grid -> list of shapely Polygons (row-run boxes, unioned)."""
    from shapely.geometry import box
    from shapely.ops import unary_union
    boxes = []
    ny, nx = mask.shape
    for j in range(ny):
        idx = np.flatnonzero(mask[j])
        if idx.size == 0:
            continue
        breaks = np.flatnonzero(np.diff(idx) > 1)
        starts = np.concatenate(([idx[0]], idx[breaks + 1]))
        ends = np.concatenate((idx[breaks], [idx[-1]]))
        for s, e in zip(starts, ends):
            boxes.append(box(s * q, j * q, (e + 1) * q, (j + 1) * q))
    if not boxes:
        return []
    uni = unary_union(boxes)
    if uni.is_empty:
        return []
    geoms = list(uni.geoms) if uni.geom_type == "MultiPolygon" else [uni]
    out = []
    for g in geoms:
        g = g.simplify(tol)
        if g.is_empty or g.area <= 0:
            continue
        if g.geom_type == "Polygon":
            out.append(g)
        else:
            out.extend(list(g.geoms))
    return out


def keep_connected_islands(mask, board, stitch_vias, q):
    """Delete copper that does not reach the ground net."""
    lab, n = label(mask)
    if n == 0:
        return mask
    anchors = [(p.x, p.y) for p in board.all_pads() if p.net == "GND"]
    anchors += [(v.x, v.y) for v in stitch_vias]
    keep = set()
    for (x, y) in anchors:
        ix, iy = int(x / q), int(y / q)
        if not (0 <= ix < lab.shape[1] and 0 <= iy < lab.shape[0]):
            continue
        lid = lab[iy, ix]
        if lid:
            keep.add(lid)
            continue
        for r in range(1, 6):
            sub = lab[max(0, iy - r):iy + r + 1, max(0, ix - r):ix + r + 1]
            vals = set(sub[sub > 0].tolist())
            if vals:
                keep |= vals
                break
    return np.isin(lab, list(keep)) if keep else np.zeros_like(mask)


def build_pour(board: Board, tracks: List[Track], vias: List[Via],
               stitch_vias: List[Via], q: float = 0.20, layer: str = "top") -> Pour:
    mask = build_mask(board, tracks, vias, stitch_vias, q, layer)
    mask = keep_connected_islands(mask, board, stitch_vias, q)
    polys = mask_to_polygons(mask, q)
    out = []
    for g in polys:
        out.append(PourPolygon([tuple(p) for p in list(g.exterior.coords)[:-1]],
                               [[tuple(p) for p in list(r.coords)[:-1]] for r in g.interiors]))
    return Pour(layer, out, sum(g.area for g in polys))


# --------------------------------------------------------------------------
def place_stitching_vias(board: Board, tracks: List[Track], vias: List[Via],
                         q: float = 0.20) -> List[Via]:
    """Ground stitching vias.

    A stitching via is only useful where *both* layers carry ground copper, so
    the candidate sites are the intersection of the two pour masks (each inflated
    by the via radius).  Sites are chosen on a lattice, which maximises the number
    of plane islands that end up tied together.
    """
    m_top = build_mask(board, tracks, vias, [], q, "top", thermal=False, extra_r=0.30)
    m_bot = build_mask(board, tracks, vias, [], q, "bottom", thermal=False, extra_r=0.30)
    overlap = m_top & m_bot
    nx, ny = overlap.shape[1], overlap.shape[0]
    X, Y = np.meshgrid((np.arange(nx) + 0.5) * q, (np.arange(ny) + 0.5) * q)

    # keep clear of every pad and hole (hole-to-hole and mask rules)
    for p in board.all_pads():
        if p.is_tht:
            overlap &= np.hypot(X - p.x, Y - p.y) > (p.drill / 2.0 + 0.15 + 0.30 + 0.15)
        else:
            w, h = p.shape_wh()
            overlap &= np.hypot(X - p.x, Y - p.y) > (max(w, h) / 2.0 + 0.30 + 0.20)
    for fp in board.fps.values():
        for (pos, dia) in fp.npth:
            overlap &= np.hypot(X - pos[0], Y - pos[1]) > (dia / 2.0 + 0.30 + 0.30)

    inset = int(round((0.30 + 0.30) / q)) + 1
    overlap[:inset, :] = False
    overlap[-inset:, :] = False
    overlap[:, :inset] = False
    overlap[:, -inset:] = False

    out: List[Via] = []
    step = max(1, int(round(STITCH_SPACING / q)))
    min_d = STITCH_SPACING * 0.85
    for iy in range(step // 2, ny, step):
        for ix in range(step // 2, nx, step):
            if not overlap[iy, ix]:
                continue
            x, y = (ix + 0.5) * q, (iy + 0.5) * q
            if any(math.hypot(x - v.x, y - v.y) < min_d for v in out):
                continue
            if any(math.hypot(x - v.x, y - v.y) < min_d for v in vias):
                continue
            out.append(Via("GND", x, y, 0.60, 0.30, stitch=True))
    return out
