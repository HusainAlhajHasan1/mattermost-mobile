"""
Small 2-D geometry kernel used by the PCB generator.

Everything is expressed in millimetres in a y-down board frame whose origin is
the top-left corner of the board outline (x grows right, y grows down).

The kernel answers three kinds of questions:

    * rasterise a shape into a covering set of discs  -> coarse previews
    * exact (vectorised) distance masks              -> router and copper pour
    * polygon scan conversion                        -> filled polygons
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

import numpy as np

try:  # optional - only needed for "true offset" helpers
    from shapely.geometry import box as _box
    from shapely.geometry import Point as _Point
    HAS_SHAPELY = True
except Exception:  # pragma: no cover
    HAS_SHAPELY = False


# --------------------------------------------------------------------------
# primitive shapes
# --------------------------------------------------------------------------
class Shape:
    def raster(self, q: float) -> List[Tuple[float, float, float]]:
        raise NotImplementedError

    @property
    def bounds(self) -> Tuple[float, float, float, float]:
        raise NotImplementedError


def _cover_radius(r: float, q: float) -> float:
    """Radius of discs that are guaranteed to stay inside a shape of radius r."""
    return max(r - q * 0.75, q * 0.05)


def _steps(span: float, q: float) -> int:
    return max(1, int(math.ceil(span / (q * 0.8))))


@dataclass
class Circle(Shape):
    x: float
    y: float
    r: float

    def raster(self, q):
        return [(self.x, self.y, _cover_radius(self.r, q))]

    @property
    def bounds(self):
        return (self.x - self.r, self.y - self.r, self.x + self.r, self.y + self.r)


@dataclass
class Segment(Shape):
    """Stadium: line from (x1,y1) to (x2,y2) with radius r."""
    x1: float
    y1: float
    x2: float
    y2: float
    r: float

    def _len(self):
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)

    def raster(self, q):
        rr = _cover_radius(self.r, q)
        n = _steps(self._len(), q)
        return [(self.x1 + (self.x2 - self.x1) * i / n,
                 self.y1 + (self.y2 - self.y1) * i / n, rr) for i in range(n + 1)]

    @property
    def bounds(self):
        return (min(self.x1, self.x2) - self.r, min(self.y1, self.y2) - self.r,
                max(self.x1, self.x2) + self.r, max(self.y1, self.y2) + self.r)


@dataclass
class Poly(Shape):
    """Filled polygon, optionally stroked with radius r (Minkowski sum)."""
    pts: Sequence[Tuple[float, float]]
    r: float = 0.0

    def _edges(self):
        pts = list(self.pts)
        for i in range(len(pts)):
            yield pts[i], pts[(i + 1) % len(pts)]

    def raster(self, q):
        out = []
        if self.r > 0:
            for a, b in self._edges():
                out += Segment(a[0], a[1], b[0], b[1], self.r).raster(q)
        return out

    @property
    def bounds(self):
        xs = [p[0] for p in self.pts]
        ys = [p[1] for p in self.pts]
        return (min(xs) - self.r, min(ys) - self.r, max(xs) + self.r, max(ys) + self.r)


def rounded_rect(cx: float, cy: float, w: float, h: float, rad: float) -> Poly:
    hw, hh = w / 2.0, h / 2.0
    rad = max(0.0, min(rad, hw, hh))
    if rad <= 1e-9:
        return Poly([(cx - hw, cy - hh), (cx + hw, cy - hh),
                     (cx + hw, cy + hh), (cx - hw, cy + hh)], 0.0)
    n = max(4, int(math.ceil(rad / 0.12)))
    pts = []
    for (ox, oy, a0) in ((cx + hw - rad, cy + hh - rad, 0.0),
                         (cx - hw + rad, cy + hh - rad, 90.0),
                         (cx - hw + rad, cy - hh + rad, 180.0),
                         (cx + hw - rad, cy - hh + rad, 270.0)):
        for i in range(n + 1):
            a = math.radians(a0 + 90.0 * i / n)
            pts.append((ox + rad * math.cos(a), oy + rad * math.sin(a)))
    return Poly(pts, 0.0)


def rotate(pts: Iterable[Tuple[float, float]], cx: float, cy: float,
           deg: float) -> List[Tuple[float, float]]:
    if abs(deg) < 1e-12:
        return [(x, y) for x, y in pts]
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    return [(cx + (x - cx) * ca - (y - cy) * sa,
             cy + (x - cx) * sa + (y - cy) * ca) for x, y in pts]


def rect(cx: float, cy: float, w: float, h: float, ang: float = 0.0) -> Poly:
    pts = [(cx - w / 2, cy - h / 2), (cx + w / 2, cy - h / 2),
           (cx + w / 2, cy + h / 2), (cx - w / 2, cy + h / 2)]
    return Poly(rotate(pts, cx, cy, ang), 0.0)


# --------------------------------------------------------------------------
# exact distance masks (used by the router and the copper pour)
# --------------------------------------------------------------------------
def dist_to_seg(X, Y, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    if L2 <= 1e-18:
        return np.hypot(X - x1, Y - y1)
    t = np.clip(((X - x1) * dx + (Y - y1) * dy) / L2, 0.0, 1.0)
    return np.hypot(X - (x1 + t * dx), Y - (y1 + t * dy))


def poly_mask(X, Y, pts):
    """Vectorised even-odd point-in-polygon."""
    inside = np.zeros(X.shape, dtype=bool)
    n = len(pts)
    for i in range(n):
        xa, ya = pts[i]
        xb, yb = pts[(i + 1) % n]
        if ya == yb:
            continue
        cond = ((ya <= Y) & (Y < yb)) | ((yb <= Y) & (Y < ya))
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = xa + (Y - ya) * (xb - xa) / (yb - ya)
        inside ^= cond & (X > xint)
    return inside


def shape_mask(shape, X, Y, delta: float = 0.0):
    """Boolean mask of cells whose centre is inside `shape` inflated by delta."""
    if isinstance(shape, Circle):
        return np.hypot(X - shape.x, Y - shape.y) <= (shape.r + delta) + 1e-12
    if isinstance(shape, Segment):
        return dist_to_seg(X, Y, shape.x1, shape.y1, shape.x2, shape.y2) <= (shape.r + delta) + 1e-12
    if isinstance(shape, Poly):
        m = poly_mask(X, Y, shape.pts)
        rr = shape.r + delta
        if rr > 0:
            for a, b in shape._edges():
                m |= dist_to_seg(X, Y, a[0], a[1], b[0], b[1]) <= rr + 1e-12
        return m
    raise TypeError(shape)


def point_in_poly(x: float, y: float, pts: Sequence[Tuple[float, float]]) -> bool:
    inside = False
    n = len(pts)
    for i in range(n):
        xa, ya = pts[i]
        xb, yb = pts[(i + 1) % n]
        if (ya > y) != (yb > y):
            if x < xa + (y - ya) * (xb - xa) / (yb - ya):
                inside = not inside
    return inside


def polygon_area(pts):
    s = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def dedupe(pts):
    out = []
    for p in pts:
        if not out or (abs(p[0] - out[-1][0]) > 1e-9 or abs(p[1] - out[-1][1]) > 1e-9):
            out.append(p)
    if len(out) > 2 and abs(out[0][0] - out[-1][0]) < 1e-9 and abs(out[0][1] - out[-1][1]) < 1e-9:
        out.pop()
    return out
