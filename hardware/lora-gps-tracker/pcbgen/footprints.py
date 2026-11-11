"""
Footprint library for the ultra-low-power LoRa/GPS tracker.

All footprints are described in *footprint-local* millimetres (y down) and are
instantiated on the board with a (x, y, angle, side) placement.

Pad geometry for the ESP32-WROOM-32E is taken pad-for-pad from Espressif's own
land pattern (espressif/kicad-libraries, ESP32-WROOM-32E.kicad_mod):
1.27 mm pitch, 1.5 x 0.9 mm castellated pads, 9 x 0.9 mm thermal openings.
The Ra-02 pad arrangement (1..8 left, 9..16 right, 2.0 mm pitch) was cross
checked against two independent community KiCad footprints.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple


# --------------------------------------------------------------------------
@dataclass
class Pad:
    num: str
    x: float
    y: float
    w: float
    h: float
    shape: str = "rect"          # rect | circle
    drill: float = 0.0           # >0 => plated through hole
    kind: str = "smd"            # smd | tht
    net: Optional[str] = None
    mask: bool = True

    @property
    def is_tht(self) -> bool:
        return self.drill > 0.0


@dataclass
class Footprint:
    ref: str
    value: str
    w: float                      # courtyard width
    h: float                      # courtyard height
    pads: List[Pad] = field(default_factory=list)
    silk: list = field(default_factory=list)
    fab: list = field(default_factory=list)
    courtyard: list = field(default_factory=list)
    keepouts: List[Tuple[float, float, float, float]] = field(default_factory=list)
    npth: List[Tuple[float, float, float]] = field(default_factory=list)
    body_h: float = 0.0
    ref_off: Tuple[float, float] = (0.0, 0.0)


# --------------------------------------------------------------------------
# placement helpers
# --------------------------------------------------------------------------
def place(local: Tuple[float, float], pos: Tuple[float, float], ang: float,
          mirror: bool = False):
    """Local -> board.  `ang` is CCW as seen from the top, y grows downwards.

    Mirroring (bottom side) flips x before the rotation, which is what a part
    mounted on the bottom and viewed from the top looks like.
    """
    x, y = local
    if mirror:
        x = -x
    if abs(ang) < 1e-12:
        return (x + pos[0], y + pos[1])
    a = math.radians(ang)
    ca, sa = math.cos(a), math.sin(a)
    return (pos[0] + x * ca + y * sa, pos[1] - x * sa + y * ca)


def normalize_outlines(v):
    """Accept [[(x,y), ...], ...] as well as a bare [(x,y), ...]."""
    if not v:
        return []
    first = v[0]
    if len(first) == 2 and all(isinstance(c, (int, float)) for c in first):
        return [tuple(tuple(p) for p in v)]
    return [tuple(tuple(p) for p in o) for o in v]


def transform_outline(pts, pos, ang, mirror=False):
    return [place(p, pos, ang, mirror) for p in pts]


def vec_rect(w, h):
    return [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]


def _crt(fw: float, fh: float, m: float = 0.25):
    return [(-fw / 2 - m, -fh / 2 - m), (fw / 2 + m, -fh / 2 - m),
            (fw / 2 + m, fh / 2 + m), (-fw / 2 - m, fh / 2 + m)]


def _circle_pts(r, n=32):
    return [(r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n))
            for i in range(n + 1)]


# --------------------------------------------------------------------------
# footprint builders
# --------------------------------------------------------------------------
def passive_0805(ref: str, value: str) -> Footprint:
    """0805 (2012 metric) two terminal passive: 1.1 x 1.4 mm pads, 1.9 mm pitch."""
    fp = Footprint(ref, value, w=3.2, h=1.8)
    fp.pads = [Pad("1", -0.95, 0, 1.10, 1.40),
               Pad("2", +0.95, 0, 1.10, 1.40)]
    fp.silk = [((-0.55, -0.75), (0.55, -0.75)), ((0.55, 0.75), (-0.55, 0.75))]
    fp.fab = [((-1.0, -0.625), (1.0, -0.625), (1.0, 0.625), (-1.0, 0.625), (-1.0, -0.625))]
    fp.courtyard = [_crt(fp.w, fp.h, 0.0)]
    fp.body_h = 0.6
    return fp


def sot23(ref: str, value: str) -> Footprint:
    """SOT-23: 0.95 mm pitch, 0.6 x 1.0 mm pads."""
    fp = Footprint(ref, value, w=4.2, h=3.4)
    fp.pads = [Pad("1", -1.0, -0.95, 0.60, 1.00),
               Pad("2", -1.0, +0.95, 0.60, 1.00),
               Pad("3", +1.0, 0.00, 0.60, 1.00)]
    fp.silk = [((-1.55, -1.5), (-1.55, 1.5)), ((1.55, 1.9), (-0.8, 1.9)),
               ((-0.8, -1.9), (1.55, -1.9))]
    fp.fab = [((-1.4, -1.5), (1.4, -1.5), (1.4, 1.5), (-1.4, 1.5), (-1.4, -1.5))]
    fp.courtyard = [_crt(fp.w, fp.h, 0.0)]
    fp.body_h = 1.1
    fp.ref_off = (0, -2.4)
    return fp


def soic8(ref: str, value: str) -> Footprint:
    """SOIC-8 narrow body, 1.27 mm pitch, 1.55 x 0.6 mm pads."""
    fp = Footprint(ref, value, w=6.6, h=6.0)
    pads = []
    for i in range(4):
        x = -1.905 + 1.27 * i
        pads.append(Pad(str(i + 1), x, -2.70, 0.60, 1.55))
        pads.append(Pad(str(8 - i), x, +2.70, 0.60, 1.55))
    fp.pads = pads
    fp.silk = [((-2.35, -1.95), (2.35, -1.95)), ((2.35, 1.95), (-2.35, 1.95))]
    fp.fab = [((-1.95, -2.45), (1.95, -2.45), (1.95, 2.45), (-1.95, 2.45), (-1.95, -2.45))]
    fp.courtyard = [_crt(fp.w, fp.h, 0.0)]
    fp.body_h = 1.75
    fp.ref_off = (0, -3.6)
    return fp


def esp32_wroom(ref: str = "U1") -> Footprint:
    """ESP32-WROOM-32E, Espressif recommended land pattern."""
    fp = Footprint(ref, "ESP32-WROOM-32E", w=19.6, h=27.4)
    pads = []
    for i in range(14):                                   # left, pins 1..14
        pads.append(Pad(str(i + 1), -8.75, -7.01 + 1.27 * i, 1.50, 0.90))
    for i in range(10):                                   # bottom, pins 15..24
        pads.append(Pad(str(15 + i), -5.72 + 1.27 * i, 10.75, 0.90, 1.50))
    for i in range(14):                                   # right, pins 25..38
        pads.append(Pad(str(25 + i), 8.75, 9.50 - 1.27 * i, 1.50, 0.90))
    for gx in (-2.9, -1.5, -0.1):                         # thermal pad 39
        for gy in (-0.69, 0.71, 2.11):
            pads.append(Pad("39", gx, gy, 0.90, 0.90))
    fp.pads = pads
    fp.fab = [((-9.0, -14.5), (9.0, -14.5), (9.0, 11.0), (-9.0, 11.0), (-9.0, -14.5))]
    fp.silk = [((-9.0, -14.5), (-9.0, -7.75)),
               ((-9.0, 11.0), (9.0, 11.0)),
               ((9.0, -14.5), (9.0, 11.0))]
    fp.courtyard = [(-9.8, -14.8), (9.8, -14.8), (9.8, 11.8), (-9.8, 11.8)]
    fp.keepouts = [(-9.0, -14.5, 9.0, -8.56)]     # PCB antenna area (local frame)
    fp.body_h = 3.1
    fp.ref_off = (0, 12.6)
    return fp


def ra02(ref: str = "U2") -> Footprint:
    """Ai-Thinker Ra-02 (SX1278): 16 castellated pads, 2.0 mm pitch, 16 x 16 body."""
    fp = Footprint(ref, "Ra-02 SX1278", w=18.6, h=18.4)
    pads = []
    for i in range(8):
        pads.append(Pad(str(i + 1), -7.90, -7.0 + 2.0 * i, 2.00, 1.10))
    for i in range(8):
        pads.append(Pad(str(9 + i), 7.90, 7.0 - 2.0 * i, 2.00, 1.10))
    fp.pads = pads
    fp.fab = [((-8.0, -8.0), (8.0, -8.0), (8.0, 8.0), (-8.0, 8.0), (-8.0, -8.0))]
    fp.silk = [((-6.6, -8.0), (6.6, -8.0)), ((-6.6, 8.0), (6.6, 8.0)),
               ((-8.0, -8.0), (-8.0, 8.0)), ((8.0, -8.0), (8.0, 8.0))]
    fp.courtyard = [(-9.1, -9.2), (9.1, -9.2), (9.1, 9.2), (-9.1, 9.2)]
    fp.body_h = 3.2
    fp.ref_off = (0, 9.8)
    return fp


def cr1220(ref: str = "BAT1") -> Footprint:
    """SMD CR1220 coin cell holder (12.5 mm cell)."""
    fp = Footprint(ref, "CR1220 holder", w=14.4, h=14.4)
    fp.pads = [Pad("1", -5.10, 4.60, 2.40, 1.60),      # positive
               Pad("2", -5.10, -4.60, 2.40, 1.60)]     # negative
    body = _circle_pts(6.35)
    fp.silk = [body]
    fp.fab = [body]
    fp.courtyard = [(-7.2, -7.2), (7.2, -7.2), (7.2, 7.2), (-7.2, 7.2)]
    fp.body_h = 3.2
    fp.ref_off = (0, -8.4)
    return fp


def microsd(ref: str = "J1") -> Footprint:
    """TF-01A push-push microSD socket, 9 signal pads + shell pads.

    Local frame: card mouth faces -y, signal pads on the +y edge.
    Pin 9 (card detect) is present in the footprint but not connected in rev 1.0.
    """
    fp = Footprint(ref, "microSD TF-01A", w=16.4, h=16.8)
    pads = []
    for i in range(8):
        pads.append(Pad(str(i + 1), -3.85 + 1.10 * i, 7.20, 0.90, 1.90))
    pads.append(Pad("9", 3.40, -6.60, 0.90, 1.40))       # card-detect switch
    pads.append(Pad("MP", -5.20, -6.80, 1.80, 1.40))
    pads.append(Pad("MP", 5.20, -6.80, 1.80, 1.40))
    pads.append(Pad("MP", -6.60, 1.00, 1.20, 2.20))
    pads.append(Pad("MP", 6.60, 1.00, 1.20, 2.20))
    fp.pads = pads
    fp.fab = [((-7.1, -7.5), (7.1, -7.5), (7.1, 7.5), (-7.1, 7.5), (-7.1, -7.5))]
    fp.silk = [((-7.1, -7.5), (-7.1, 7.5)), ((7.1, -7.5), (7.1, 7.5)),
               ((-7.1, 7.5), (7.1, 7.5))]
    fp.courtyard = [(-7.9, -8.2), (7.9, -8.2), (7.9, 8.2), (-7.9, 8.2)]
    fp.body_h = 1.5
    fp.ref_off = (0, 9.0)
    return fp


def header_tht(ref: str, n: int, value: str = "") -> Footprint:
    """Vertical 2.54 mm THT header, pin 1 square."""
    fp = Footprint(ref, value or f"Conn_01x{n:02d}", w=2.54 * n + 1.0, h=3.6)
    for i in range(n):
        fp.pads.append(Pad(str(i + 1), -1.27 * (n - 1) + 2.54 * i, 0.0,
                           1.70, 1.70, shape="rect" if i == 0 else "circle", drill=1.00))
    hw = 1.27 * (n - 1) + 0.5
    fp.silk = [((-hw, -1.55), (hw, -1.55)), ((hw, -1.55), (hw, 1.55)),
               ((hw, 1.55), (-hw, 1.55)), ((-hw, 1.55), (-hw, -1.55))]
    fp.courtyard = [vec_rect(2.54 * (n - 1) + 2.6, 4.0)]
    fp.body_h = 2.5
    fp.ref_off = (0, -3.4)
    return fp


def tact_3x4(ref: str, value: str = "SW_Push") -> Footprint:
    """3 x 4 mm SMD tactile switch, terminals on the long axis."""
    fp = Footprint(ref, value, w=5.2, h=2.8)
    fp.pads = [Pad("1", -1.90, 0.0, 1.20, 1.00),
               Pad("2", +1.90, 0.0, 1.20, 1.00)]
    fp.silk = [((-1.55, -1.05), (1.55, -1.05)), ((1.55, 1.05), (-1.55, 1.05)),
               ((1.55, -1.05), (1.55, 1.05)), ((-1.55, 1.05), (-1.55, -1.05))]
    fp.fab = [((-1.5, -1.0), (1.5, -1.0), (1.5, 1.0), (-1.5, 1.0), (-1.5, -1.0))]
    fp.courtyard = [_crt(fp.w, fp.h, 0.0)]
    fp.body_h = 2.5
    fp.ref_off = (0, -2.4)
    return fp


def mounting_hole(ref: str, dia: float = 2.7) -> Footprint:
    fp = Footprint(ref, f"MountingHole_{dia}", w=dia + 1.6, h=dia + 1.6)
    fp.npth = [(0.0, 0.0, dia)]
    fp.silk = [_circle_pts(dia / 2 + 0.55)]
    fp.courtyard = [vec_rect(dia + 1.6, dia + 1.6)]
    return fp
