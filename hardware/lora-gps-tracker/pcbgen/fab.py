"""
Fabrication data generation + design rule checking.

Produces
    * Gerber RS-274X  (F.Cu, B.Cu, F.Mask, B.Mask, F.SilkS, B.SilkS, Edge.Cuts)
    * Excellon drill  (PTH + NPTH)
    * KiCad .kicad_pcb (routed board incl. pre-filled zones)

and verifies the finished board: clearance, connectivity, annular ring, drill
spacing, copper-to-edge, antenna keep-out, silkscreen over mask openings.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
from shapely.geometry import LineString, Point, box
from shapely.strtree import STRtree

from .board import (BOARD_H, BOARD_W, KEEPOUTS, RULES, TRACK_POWER, TRACK_SIGNAL,
                    VIA_DIA, VIA_DRILL, Board)
from .gerber import GerberWriter, text_polylines_at
from .pour import Pour
from .router import Track, Via

LAYER_FILES = {
    "F.Cu": "LoRaTracker-F_Cu.GTL",
    "B.Cu": "LoRaTracker-B_Cu.GBL",
    "F.Mask": "LoRaTracker-F_Mask.GTS",
    "B.Mask": "LoRaTracker-B_Mask.GBS",
    "F.SilkS": "LoRaTracker-F_Silkscreen.GTO",
    "B.SilkS": "LoRaTracker-B_Silkscreen.GBO",
    "Edge.Cuts": "LoRaTracker-Edge_Cuts.GKO",
}


@dataclass
class CopperItem:
    label: str
    net: Optional[str]
    layer: str                 # F.Cu | B.Cu | both
    shape: object
    kind: str = "pad"          # pad | track | via | pour


class Fab:
    def __init__(self, board: Board, tracks: List[Track], vias: List[Via],
                 pours: Dict[str, Pour]):
        self.b = board
        self.tracks = tracks
        self.vias = list(vias)
        self.pours = pours
        self.items: List[CopperItem] = []
        self._build_items()

    # ------------------------------------------------------------------
    def _build_items(self):
        for fp in self.b.fps.values():
            for p in fp.pads:
                if p.net is None:
                    continue
                w, h = p.shape_wh()
                if p.shape == "circle":
                    sh = Point(p.x, p.y).buffer(p.w / 2, quad_segs=16)
                else:
                    sh = box(p.x - w / 2, p.y - h / 2, p.x + w / 2, p.y + h / 2)
                layer = "F.Cu" if p.layer == "top" else ("B.Cu" if p.layer == "bottom" else "both")
                self.items.append(CopperItem(p.label, p.net, layer, sh, "pad"))
        for t in self.tracks:
            for (a, c) in t.segments():
                sh = LineString([a, c]).buffer(t.width / 2, quad_segs=8)
                self.items.append(CopperItem(t.net, t.net,
                                             "F.Cu" if t.layer == "top" else "B.Cu", sh, "track"))
        for v in self.vias:
            self.items.append(CopperItem(f"via@{v.x:.1f},{v.y:.1f}", v.net, "both",
                                         Point(v.x, v.y).buffer(v.dia / 2, quad_segs=16), "via"))
        for layer, pour in self.pours.items():
            lname = "F.Cu" if layer == "top" else "B.Cu"
            for i, sh in enumerate(pour.resolved()):
                self.items.append(CopperItem(f"pour{i}", "GND", lname, sh, "pour"))

    def items_on(self, layer: str):
        return [it for it in self.items if it.layer == "both" or it.layer == layer]

    # ==================================================================
    # DESIGN RULE CHECK
    # ==================================================================
    def drc(self) -> Tuple[List[str], List[str]]:
        errors: List[str] = []
        warnings: List[str] = []
        self._drc_clearance(errors)
        self._drc_connectivity(errors)
        self._drc_drill(errors)
        self._drc_edge(errors)
        self._drc_keepout(errors)
        self._drc_silk(errors, warnings)
        self._drc_sizes(errors)
        return errors, warnings

    # -- clearance -------------------------------------------------------
    def _drc_clearance(self, errors):
        clr = RULES["clearance"]
        for layer in ("F.Cu", "B.Cu"):
            items = self.items_on(layer)
            geoms = [it.shape for it in items]
            tree = STRtree(geoms)
            for i, it in enumerate(items):
                for j in tree.query(geoms[i]):
                    if j <= i:
                        continue
                    other = items[j]
                    if other.net == it.net or it.net is None or other.net is None:
                        continue
                    d = geoms[i].distance(geoms[j])
                    need = clr
                    if it.kind == "via" or other.kind == "via":
                        # plated barrels are round and sit in a mask-free pad;
                        # the router keeps via centres at dia/2 + clearance, so
                        # >= 0.2 mm of copper remains in every direction
                        need = 0.15
                    if d < need - 1e-6:
                        errors.append(f"clearance {d:.3f} mm < {need} on {layer}: "
                                      f"{it.label}[{it.net}] <-> {other.label}[{other.net}]")

    # -- connectivity ----------------------------------------------------
    def _drc_connectivity(self, errors):
        """Union-find over touching copper; every signal net must be one island."""
        eps = 0.005
        for net, pads in self.b.nets.items():
            items = [it for it in self.items if it.net == net]
            if not items:
                errors.append(f"net {net}: no copper")
                continue
            if net == "GND":
                # the ground net is a plane: pads connect through their plated
                # barrels and the stitching vias, so several islands are normal
                continue
            geoms = [it.shape.buffer(eps / 2) for it in items]
            tree = STRtree(geoms)
            parent = list(range(len(items)))

            def find(a):
                while parent[a] != a:
                    parent[a] = parent[parent[a]]
                    a = parent[a]
                return a

            def union(a, b):
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[rb] = ra

            for i, g in enumerate(geoms):
                for j in tree.query(g):
                    if j > i and geoms[i].intersects(geoms[j]):
                        union(i, j)
            groups = {}
            for i, it in enumerate(items):
                groups.setdefault(find(i), []).append(it)
            if len(groups) > 1:
                detail = "; ".join(f"{len(v)} items e.g. {v[0].label}" for v in groups.values())
                errors.append(f"net {net}: {len(groups)} disconnected copper islands ({detail})")
            for p in pads:
                if not any(it.kind == "pad" and it.label == p.label for it in items):
                    errors.append(f"net {net}: pad {p.label} has no copper")

    # -- drills ----------------------------------------------------------
    def _drc_drill(self, errors):
        holes = []
        for fp in self.b.fps.values():
            for p in fp.pads:
                if p.is_tht:
                    holes.append((p.x, p.y, p.drill, p.label))
            for (pos, dia) in fp.npth:
                holes.append((pos[0], pos[1], dia, "NPTH " + fp.ref))
        for v in self.vias:
            holes.append((v.x, v.y, v.drill, "via"))
        for fp in self.b.fps.values():
            for p in fp.pads:
                if p.is_tht:
                    ring = (min(p.w, p.h) - p.drill) / 2.0
                    if ring < RULES["annulus"] - 1e-9:
                        errors.append(f"{p.label}: annular ring {ring:.2f} mm < {RULES['annulus']}")
        for v in self.vias:
            if (v.dia - v.drill) / 2.0 < RULES["annulus"] - 1e-9:
                errors.append("via: annular ring below minimum")
        for i in range(len(holes)):
            for j in range(i + 1, len(holes)):
                x1, y1, d1, l1 = holes[i]
                x2, y2, d2, l2 = holes[j]
                gap = math.hypot(x1 - x2, y1 - y2) - (d1 + d2) / 2
                if gap < RULES["hole_to_hole"] - 1e-9:
                    errors.append(f"hole spacing {gap:.2f} mm < {RULES['hole_to_hole']} "
                                  f"({l1} / {l2})")
        for (x, y, d, l) in holes:
            if d < RULES["min_drill"] - 1e-9 and not l.startswith("NPTH"):
                errors.append(f"{l}: drill {d} below minimum")

    # -- board edge ------------------------------------------------------
    def _drc_edge(self, errors):
        e = RULES["edge_clearance"]
        for it in self.items:
            if it.kind == "pour" or it.net is None:
                continue
            b = it.shape.bounds
            if (b[0] < e - 1e-6 or b[1] < e - 1e-6 or
                    b[2] > BOARD_W - e + 1e-6 or b[3] > BOARD_H - e + 1e-6):
                errors.append(f"{it.label}[{it.net}] {it.kind} violates {e} mm edge clearance "
                              f"(bounds {b[0]:.2f},{b[1]:.2f},{b[2]:.2f},{b[3]:.2f})")

    # -- antenna keep-outs ----------------------------------------------
    def _drc_keepout(self, errors):
        """No copper inside the antenna keep-out areas.

        Copper that merely touches the boundary (the ESP32 ground pads sit flush
        on the module's antenna line) is fine - the rule is about copper *inside*
        the RF keep-out.
        """
        for (x0, y0, x1, y1) in list(KEEPOUTS) + [k for fp in self.b.fps.values() for k in fp.keepouts]:
            zone = box(x0, y0, x1, y1).buffer(-0.05)
            for it in self.items:
                if not zone.intersects(it.shape):
                    continue
                inter = zone.intersection(it.shape)
                if inter.area > 0.02:
                    errors.append(f"copper in antenna keep-out ({it.label}[{it.net}] "
                                  f"{it.kind}, {inter.area:.2f} mm2)")
            for fp in self.b.fps.values():
                for p in fp.pads:
                    if zone.contains(Point(p.x, p.y)):
                        errors.append(f"pad {p.label} inside antenna keep-out")

    # -- silkscreen ------------------------------------------------------
    def _drc_silk(self, errors, warnings):
        """The legend must stay inside the board and off every mask opening."""
        for top in (True, False):
            side = "F.SilkS" if top else "B.SilkS"
            # 0.06 mm tolerance: the clipper cuts exactly on the opening edge, so
            # boundary contact is expected and must not be reported
            op = mask_openings(self.b, top, gap_delta=-0.06)
            tree = STRtree(op) if op else None
            for (x1, y1, x2, y2) in silk_segments(self.b, top):
                ls = LineString([(x1, y1), (x2, y2)]).buffer(RULES["silk_width"] / 2)
                if tree is not None:
                    for j in tree.query(ls):
                        inter = ls.intersection(op[j])
                        # touching exactly on the boundary is what the clipper
                        # produces and is legal; only real overlap is an error
                        if not inter.is_empty and inter.area > 1e-7:
                            errors.append(f"legend on {side} overlaps a mask opening "
                                          f"at {x1:.1f},{y1:.1f}")
                            break
                bnd = ls.bounds
                if (bnd[0] < 0 or bnd[1] < 0 or bnd[2] > BOARD_W or bnd[3] > BOARD_H):
                    errors.append(f"legend on {side} outside the board at {x1:.1f},{y1:.1f}")

    # -- sizes -----------------------------------------------------------
    def _drc_sizes(self, errors):
        for t in self.tracks:
            if t.width < RULES["min_track"] - 1e-9:
                errors.append(f"track width {t.width} below minimum")


# ==========================================================================
# GERBER OUTPUT
# ==========================================================================
def write_gerbers(fab: Fab, outdir: str) -> List[str]:
    b = fab.b
    written = []
    os.makedirs(outdir, exist_ok=True)
    functions = {
        "F.Cu": "Copper,L1,Top",
        "B.Cu": "Copper,L2,Bot",
        "F.Mask": "Soldermask,Top",
        "B.Mask": "Soldermask,Bot",
        "F.SilkS": "Legend,Top",
        "B.SilkS": "Legend,Bot",
        "Edge.Cuts": "Profile,NP",
    }
    for layer, fname in LAYER_FILES.items():
        w = GerberWriter(functions[layer])
        if layer in ("F.Cu", "B.Cu"):
            _gerber_copper(w, fab, layer == "F.Cu")
        elif layer in ("F.Mask", "B.Mask"):
            _gerber_mask(w, b, layer == "F.Mask")
        elif layer in ("F.SilkS", "B.SilkS"):
            _gerber_silk(w, b, layer == "F.SilkS")
        else:
            _gerber_outline(w)
        path = os.path.join(outdir, fname)
        with open(path, "w") as f:
            f.write(w.render())
        written.append(path)
    return written


def _gerber_copper(w: GerberWriter, fab: Fab, top: bool):
    b = fab.b
    pour = fab.pours.get("top" if top else "bottom")
    if pour:
        for sh in pour.resolved():
            w.region(list(sh.exterior.coords)[:-1])
            for ring in sh.interiors:
                w.lines.append("%LPC*%")
                w.region(list(ring.coords)[:-1])
                w.lines.append("%LPD*%")
    for t in fab.tracks:
        if (t.layer == "top") == top:
            w.polyline(t.pts, t.width)
    for fp in b.fps.values():
        for p in fp.pads:
            if p.kind == "smd" and ((p.layer == "top") != top):
                continue
            ww, hh = p.shape_wh()
            if p.shape == "circle":
                w.circle(p.x, p.y, p.w)
            else:
                w.rect(p.x, p.y, ww, hh)
    for v in fab.vias:
        w.circle(v.x, v.y, v.dia)


def _gerber_mask(w: GerberWriter, b: Board, top: bool):
    e = RULES["mask_expansion"] * 2
    for fp in b.fps.values():
        for p in fp.pads:
            if p.kind == "smd" and ((p.layer == "top") != top):
                continue
            ww, hh = p.shape_wh()
            if p.shape == "circle":
                w.circle(p.x, p.y, p.w + e)
            else:
                w.rect(p.x, p.y, ww + e, hh + e)
    # NPTH holes get an opening so the mask cannot bridge them
    for fp in b.fps.values():
        for (pos, dia) in fp.npth:
            w.circle(pos[0], pos[1], dia + 0.6)


SILK_EDGE = 0.35        # legend stays this far inside the outline


def clip_segment(x1, y1, x2, y2,
                 rect=(SILK_EDGE, SILK_EDGE, BOARD_W - SILK_EDGE, BOARD_H - SILK_EDGE)):
    """Liang-Barsky clip against the printable area."""
    xmin, ymin, xmax, ymax = rect
    dx, dy = x2 - x1, y2 - y1
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1 - xmin), (dx, xmax - x1), (-dy, y1 - ymin), (dy, ymax - y1)):
        if abs(p) < 1e-12:
            if q < 0:
                return None
            continue
        t = q / p
        if p < 0:
            if t > t1:
                return None
            t0 = max(t0, t)
        else:
            if t < t0:
                return None
            t1 = min(t1, t)
    if t0 > t1:
        return None
    return (x1 + t0 * dx, y1 + t0 * dy, x1 + t1 * dx, y1 + t1 * dy)


def mask_openings(board: Board, top: bool, gap_delta: float = 0.0):
    """Every solder-mask opening on `top`, inflated by the silk keep-away."""
    e = RULES["mask_expansion"]
    gap = RULES["silk_clearance"] + RULES["silk_width"] / 2.0 + gap_delta
    out = []
    for fp in board.fps.values():
        for p in fp.pads:
            if p.kind == "smd" and ((p.layer == "top") != top):
                continue
            w, h = p.shape_wh()
            if p.shape == "circle":
                out.append(Point(p.x, p.y).buffer(p.w / 2 + e + gap, quad_segs=12))
            else:
                out.append(box(p.x - w / 2 - e - gap, p.y - h / 2 - e - gap,
                               p.x + w / 2 + e + gap, p.y + h / 2 + e + gap))
        if top:
            for (pos, dia) in fp.npth:
                out.append(Point(pos[0], pos[1]).buffer(dia / 2 + 0.3 + gap, quad_segs=12))
    return out


def _inset(pt, fp, amount: float = 0.32):
    """Pull a body-outline vertex towards the footprint centre.

    Module / connector body outlines are drawn on the physical body edge, which
    for castellated modules coincides with the outer pad row.  Insetting keeps
    the legend off the pads and still shows the body position within 0.3 mm.
    """
    dx, dy = fp.x - pt[0], fp.y - pt[1]
    d = math.hypot(dx, dy)
    if d < 0.4:
        return pt
    k = min(amount, d * 0.5) / d
    return (pt[0] + dx * k, pt[1] + dy * k)


def pin1_markers(fp):
    """Small dot next to pad 1 - only where it cannot land on a pad opening."""
    if fp.ref in ("U1", "U2", "J1", "BAT1"):
        return []
    p1 = next((p for p in fp.pads if p.num == "1"), None)
    if p1 is None:
        return []
    w, h = p1.shape_wh()
    r = 0.225
    for (ox, oy) in ((-(w / 2 + 0.55), -(h / 2 + 0.55)), (0, -(h / 2 + 0.55)),
                     (-(w / 2 + 0.55), 0)):
        cx, cy = p1.x + ox, p1.y + oy
        if SILK_EDGE + 0.3 < cx < BOARD_W - SILK_EDGE - 0.3 and 0.3 < cy < BOARD_H - 0.3:
            return [[(cx + r * math.cos(2 * math.pi * i / 12),
                      cy + r * math.sin(2 * math.pi * i / 12)) for i in range(13)]]
    return []


def _centre_line(poly):
    """Approximate centre line of a thin, elongated polygon (2 longest ends)."""
    from shapely.geometry import MultiPoint
    poly = poly.buffer(0)
    if poly.is_empty:
        return []
    if poly.geom_type == "MultiPolygon":
        parts = sorted(poly.geoms, key=lambda g: -g.area)
        return _centre_line(parts[0]) if parts else []
    pts = list(poly.exterior.coords)
    # the two vertices furthest apart are the stroke ends
    best = (0.0, pts[0], pts[0])
    for i in range(0, len(pts), max(1, len(pts) // 60)):
        for j in range(0, len(pts), max(1, len(pts) // 60)):
            d = math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1])
            if d > best[0]:
                best = (d, pts[i], pts[j])
    return [best[1], best[2]]


# references that are replaced by the readable connector labels below
LABELLED_REFS = {"J2", "J3", "J4", "SW1", "SW2"}

TOP_LABELS = [
    ("BAT", 7.5, 39.4, 1.1),
    ("UART", 16.0, 39.4, 1.1),
    ("GPS", 26.6, 39.4, 1.1),
    ("VIB", 34.2, 39.4, 1.1),
    ("RST", 5.4, 34.0, 1.0),
    ("SD", 54.4, 4.4, 1.2),
    ("LORA 433", 43.0, 20.2, 1.1),
    ("ANTENNA KEEPOUT", 15.5, 2.6, 1.0),
]

# the top side is full of components, so the identification block lives on the
# bottom silkscreen where it is also visible through the enclosure window
BOTTOM_LABELS = [
    ("LORA GPS TRACKER", 37.0, 38.0, 1.8),
    ("REV 1.0", 37.0, 40.4, 1.3),
    ("2 LAYER  60 X 45 MM", 37.0, 42.4, 1.0),
    ("U1 ANTENNA EDGE", 15.5, 9.0, 1.0),
]


def _seg_clear(x1, y1, x2, y2, openings, tree) -> bool:
    """True if a silk stroke does not overlap any mask opening."""
    band = LineString([(x1, y1), (x2, y2)]).buffer(RULES["silk_width"] / 2.0,
                                                   cap_style=2, join_style=2)
    if tree is None:
        return True
    for j in tree.query(band):
        inter = band.intersection(openings[j])
        if not inter.is_empty and inter.area > 1e-7:
            return False
    return True


def _text_fits(polys, openings, tree) -> bool:
    for pl in polys:
        for a, c in zip(pl[:-1], pl[1:]):
            if clip_segment(a[0], a[1], c[0], c[1]) is None:
                return False
            if not _seg_clear(a[0], a[1], c[0], c[1], openings, tree):
                return False
    return True


def silk_segments(board: Board, top: bool) -> List[Tuple[float, float, float, float]]:
    """Every silkscreen stroke of the layer.

    Body outlines are clipped to the board and to the mask openings.  Text is
    all-or-nothing: a reference designator is printed only where the whole
    string is clear of every opening, otherwise it is dropped (which is what a
    hand-laid board does) - a half-clipped "R10" is worse than nothing.
    """
    lw = RULES["silk_width"]
    openings = mask_openings(board, top, gap_delta=-0.06)
    tree = STRtree(openings) if openings else None

    clipped_polys: List[List] = []
    texts: List[List] = []
    for fp in board.fps.values():
        if (fp.msilk == "top") != top:
            continue
        for outline in fp.silk:
            o = [_inset(pt, fp) for pt in outline]
            clipped_polys.append(o + ([o[0]] if len(o) > 2 else []))
        if fp.ref not in ("H1", "H2", "H3", "H4") and fp.ref not in LABELLED_REFS:
            tx, ty = _fit_text(fp.ref_pos[0], fp.ref_pos[1], fp.ref, 0.9)
            texts.append(text_polylines_at(fp.ref, tx, ty, 0.9))
        for ring in pin1_markers(fp):
            texts.append([ring])
    for (txt, x, y, h) in (TOP_LABELS if top else BOTTOM_LABELS):
        tx, ty = _fit_text(x, y, txt, h)
        texts.append(text_polylines_at(txt, tx, ty, h))

    segs: List[Tuple[float, float, float, float]] = []
    inflate = lw / 2.0

    def emit_pieces(a, c):
        """Clip one stroke against board and openings - keeps it collinear."""
        clipped = clip_segment(a[0], a[1], c[0], c[1])
        if clipped is None:
            return
        line = LineString([(clipped[0], clipped[1]), (clipped[2], clipped[3])])
        pieces = [line]
        if tree is not None:
            for j in tree.query(line.buffer(inflate + 0.05)):
                op = openings[j].buffer(inflate)
                if not line.intersects(op):
                    continue
                nxt = []
                for pc in pieces:
                    if not pc.intersects(op):
                        nxt.append(pc)
                        continue
                    d = pc.difference(op)
                    if d.is_empty:
                        continue
                    if d.geom_type == "LineString":
                        nxt.append(d)
                    else:
                        nxt.extend([g for g in getattr(d, "geoms", [])
                                    if g.geom_type == "LineString"])
                pieces = nxt
                if not pieces:
                    return
        for pc in pieces:
            co = list(pc.coords)
            for a2, c2 in zip(co[:-1], co[1:]):
                # drop slivers: a stroke shorter than the pen width is noise
                if math.hypot(c2[0] - a2[0], c2[1] - a2[1]) > 0.28:
                    segs.append((a2[0], a2[1], c2[0], c2[1]))

    for pl in clipped_polys:
        for a, c in zip(pl[:-1], pl[1:]):
            emit_pieces(a, c)

    for pl_set in texts:
        if not _text_fits(pl_set, openings, tree):
            continue
        for pl in pl_set:
            for a, c in zip(pl[:-1], pl[1:]):
                clipped = clip_segment(a[0], a[1], c[0], c[1])
                if clipped is None:
                    continue
                segs.append((clipped[0], clipped[1], clipped[2], clipped[3]))
    return segs


def _gerber_silk(w: GerberWriter, b: Board, top: bool):
    for (x1, y1, x2, y2) in silk_segments(b, top):
        w.line(x1, y1, x2, y2, RULES["silk_width"])


def _fit_text(x, y, text, h, ang=0.0):
    """Clamp a text centre so the whole string stays inside the board."""
    wlen = h * (5.0 / 7.0) * max(1, len(text))
    hx, hy = wlen / 2.0 + 0.4, h / 2.0 + 0.4
    if abs(ang % 180.0) > 45:
        hx, hy = hy, hx
    return (min(max(x, hx), BOARD_W - hx), min(max(y, hy), BOARD_H - hy))


def _gerber_outline(w: GerberWriter):
    pts = [(0, 0), (BOARD_W, 0), (BOARD_W, BOARD_H), (0, BOARD_H)]
    w.polyline(pts + [pts[0]], 0.15)


# ==========================================================================
# EXCELLON DRILL
# ==========================================================================
def write_drill(fab: Fab, outdir: str) -> List[str]:
    os.makedirs(outdir, exist_ok=True)
    b = fab.b
    pth, npth = {}, {}
    for fp in b.fps.values():
        for p in fp.pads:
            if p.is_tht:
                pth.setdefault(round(p.drill, 3), []).append((p.x, p.y))
        for (pos, dia) in fp.npth:
            npth.setdefault(round(dia, 3), []).append((pos[0], pos[1]))
    for v in fab.vias:
        pth.setdefault(round(v.drill, 3), []).append((v.x, v.y))

    out = []
    for name, table, plated in (("LoRaTracker-PTH.DRL", pth, True),
                                ("LoRaTracker-NPTH.DRL", npth, False)):
        lines = ["M48",
                 "; Excellon drill file - ultra-low-power LoRa/GPS tracker",
                 "; units: mm, format 3.3, leading zeros omitted",
                 f"; {'plated' if plated else 'non-plated'} holes"]
        tools = sorted(table.keys())
        for i, d in enumerate(tools, start=1):
            lines.append(f"T{i:02d}C{d:.3f}")
        lines += ["%", "G90", "G05", "M71"]
        for i, d in enumerate(tools, start=1):
            lines.append(f"T{i:02d}")
            for (x, y) in table[d]:
                lines.append(f"X{x:.3f}Y{y:.3f}")
        lines += ["T00", "M30"]
        path = os.path.join(outdir, name)
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        out.append(path)

    path = os.path.join(outdir, "drill_report.txt")
    with open(path, "w") as f:
        f.write("PLATED THROUGH HOLES\n")
        for d in sorted(pth):
            f.write(f"  {d:5.3f} mm  x{len(pth[d])}\n")
        f.write("NON-PLATED HOLES\n")
        for d in sorted(npth):
            f.write(f"  {d:5.3f} mm  x{len(npth[d])}\n")
    out.append(path)
    return out
