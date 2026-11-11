"""
Minimal, standards compliant RS-274X (Gerber X2) writer.

Only the constructs a 2-layer board needs are emitted:

    %FSLAX46Y46*%      absolute, 4.6 format (1 nm resolution)
    %MOMM*%            millimetres
    %LPD% / %LPC*%     dark / clear polarity
    G36 / G37          regions (copper pours)
    D01 / D02 / D03    draw / move / flash

Apertures are cached and emitted in a single AD block per file.
"""

from __future__ import annotations

import math
from typing import Dict, List, Sequence, Tuple

from . import geom

INT_SCALE = 1e6   # 4.6 format


def fmt(v: float) -> str:
    return f"{int(round(v * INT_SCALE)):d}"


class GerberWriter:
    def __init__(self, file_function: str, polarity: str = "Positive"):
        self.lines: List[str] = []
        self.apertures: Dict[Tuple, int] = {}
        self.dcode = 10
        self.cur_ap = None
        self.cur_xy = None
        self.file_function = file_function
        self.in_region = False
        self.header()

    def header(self):
        L = self.lines
        L.append("%TF.GenerationSoftware,pcbgen,LoRaTracker,1.0*%")
        L.append(f"%TF.FileFunction,{self.file_function}*%")
        L.append("%TF.FilePolarity,Positive*%")
        L.append("%FSLAX46Y46*%")
        L.append("%MOMM*%")
        L.append("G04 Ultra-low-power LoRa/GPS tracker - 2 layer, 60 x 45 mm*")
        L.append("%LPD*%")
        L.append("G01*")

    def _ap(self, key) -> int:
        if key in self.apertures:
            return self.apertures[key]
        self.dcode += 1
        self.apertures[key] = self.dcode
        return self.dcode

    def _aperture_defs(self) -> List[str]:
        out = []
        for key, code in sorted(self.apertures.items(), key=lambda kv: kv[1]):
            kind = key[0]
            if kind == "C":
                out.append(f"%ADD{code}C,{key[1]:.6f}*%")
            elif kind == "R":
                out.append(f"%ADD{code}R,{key[1]:.6f}X{key[2]:.6f}*%")
            elif kind == "O":
                out.append(f"%ADD{code}O,{key[1]:.6f}X{key[2]:.6f}*%")
        return out

    def select(self, key):
        code = self._ap(key)
        if code != self.cur_ap:
            self.lines.append(f"D{code}*")
            self.cur_ap = code

    def move(self, x, y):
        self.lines.append(f"X{fmt(x)}Y{fmt(y)}D02*")
        self.cur_xy = (x, y)

    def draw(self, x, y):
        self.lines.append(f"X{fmt(x)}Y{fmt(y)}D01*")
        self.cur_xy = (x, y)

    def flash(self, x, y):
        self.lines.append(f"X{fmt(x)}Y{fmt(y)}D03*")
        self.cur_xy = (x, y)

    # -- primitives ------------------------------------------------------
    def line(self, x1, y1, x2, y2, width):
        self.select(("C", round(width, 6)))
        self.move(x1, y1)
        self.draw(x2, y2)

    def polyline(self, pts: Sequence[Tuple[float, float]], width: float, closed=False):
        if len(pts) < 2:
            return
        self.select(("C", round(width, 6)))
        self.move(*pts[0])
        for p in pts[1:]:
            self.draw(*p)
        if closed:
            self.draw(*pts[0])

    def circle(self, x, y, dia):
        self.select(("C", round(dia, 6)))
        self.flash(x, y)

    def rect(self, x, y, w, h):
        self.select(("R", round(w, 6), round(h, 6)))
        self.flash(x, y)

    def obround(self, x, y, w, h):
        self.select(("O", round(w, 6), round(h, 6)))
        self.flash(x, y)

    def region(self, pts: Sequence[Tuple[float, float]]):
        if len(pts) < 3:
            return
        L = self.lines
        L.append("G36*")
        L.append(f"X{fmt(pts[0][0])}Y{fmt(pts[0][1])}D02*")
        L.append("G01*")
        for p in pts[1:]:
            L.append(f"X{fmt(p[0])}Y{fmt(p[1])}D01*")
        L.append(f"X{fmt(pts[0][0])}Y{fmt(pts[0][1])}D01*")
        L.append("G37*")

    # -- output ----------------------------------------------------------
    def render(self) -> str:
        body = list(self.lines)
        n = next(i for i, l in enumerate(body) if l.startswith("G01*"))
        out = body[:n + 1] + self._aperture_defs() + body[n + 1:]
        out.append("M02*")
        return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# stroke font for silkscreen - 5 x 7 cell, polylines on 0..4 / 0..6
# --------------------------------------------------------------------------
FONT = {
    "0": [[(0, 1), (0, 5), (1, 6), (3, 6), (4, 5), (4, 1), (3, 0), (1, 0), (0, 1)]],
    "1": [[(1, 4), (2, 6), (2, 0)], [(1, 0), (3, 0)]],
    "2": [[(0, 5), (1, 6), (3, 6), (4, 5), (4, 4), (0, 0), (4, 0)]],
    "3": [[(0, 5), (1, 6), (3, 6), (4, 5), (4, 3), (2, 3)],
          [(4, 3), (4, 1), (3, 0), (1, 0), (0, 1)]],
    "4": [[(3, 0), (3, 6), (0, 2), (4, 2)]],
    "5": [[(4, 6), (0, 6), (0, 3), (3, 3), (4, 2), (4, 1), (3, 0), (1, 0), (0, 1)]],
    "6": [[(4, 5), (3, 6), (1, 6), (0, 5), (0, 1), (1, 0), (3, 0), (4, 1), (4, 2),
           (3, 3), (0, 3)]],
    "7": [[(0, 6), (4, 6), (1, 0)]],
    "8": [[(1, 3), (0, 4), (0, 5), (1, 6), (3, 6), (4, 5), (4, 4), (3, 3), (1, 3),
           (0, 2), (0, 1), (1, 0), (3, 0), (4, 1), (4, 2), (3, 3)]],
    "9": [[(0, 1), (1, 0), (3, 0), (4, 1), (4, 5), (3, 6), (1, 6), (0, 5), (0, 4),
           (1, 3), (4, 3)]],
    "A": [[(0, 0), (0, 4), (2, 6), (4, 4), (4, 0)], [(0, 2), (4, 2)]],
    "B": [[(0, 0), (0, 6), (3, 6), (4, 5), (4, 4), (3, 3), (0, 3)],
          [(3, 3), (4, 2), (4, 1), (3, 0), (0, 0)]],
    "C": [[(4, 5), (3, 6), (1, 6), (0, 5), (0, 1), (1, 0), (3, 0), (4, 1)]],
    "D": [[(0, 0), (0, 6), (2, 6), (4, 4), (4, 2), (2, 0), (0, 0)]],
    "E": [[(4, 6), (0, 6), (0, 0), (4, 0)], [(0, 3), (3, 3)]],
    "F": [[(4, 6), (0, 6), (0, 0)], [(0, 3), (3, 3)]],
    "G": [[(4, 5), (3, 6), (1, 6), (0, 5), (0, 1), (1, 0), (3, 0), (4, 1), (4, 3), (2, 3)]],
    "H": [[(0, 0), (0, 6)], [(4, 0), (4, 6)], [(0, 3), (4, 3)]],
    "I": [[(1, 6), (3, 6)], [(2, 6), (2, 0)], [(1, 0), (3, 0)]],
    "J": [[(4, 6), (4, 1), (3, 0), (1, 0), (0, 1)]],
    "K": [[(0, 0), (0, 6)], [(4, 6), (0, 2)], [(1, 3), (4, 0)]],
    "L": [[(0, 6), (0, 0), (4, 0)]],
    "M": [[(0, 0), (0, 6), (2, 3), (4, 6), (4, 0)]],
    "N": [[(0, 0), (0, 6), (4, 0), (4, 6)]],
    "O": [[(1, 0), (0, 1), (0, 5), (1, 6), (3, 6), (4, 5), (4, 1), (3, 0), (1, 0)]],
    "P": [[(0, 0), (0, 6), (3, 6), (4, 5), (4, 3), (3, 2), (0, 2)]],
    "Q": [[(1, 0), (0, 1), (0, 5), (1, 6), (3, 6), (4, 5), (4, 1), (3, 0), (1, 0)],
          [(2, 2), (4, 0)]],
    "R": [[(0, 0), (0, 6), (3, 6), (4, 5), (4, 3), (3, 2), (0, 2)], [(2, 2), (4, 0)]],
    "S": [[(4, 5), (3, 6), (1, 6), (0, 5), (0, 4), (1, 3), (3, 3), (4, 2), (4, 1),
           (3, 0), (1, 0), (0, 1)]],
    "T": [[(0, 6), (4, 6)], [(2, 6), (2, 0)]],
    "U": [[(0, 6), (0, 1), (1, 0), (3, 0), (4, 1), (4, 6)]],
    "V": [[(0, 6), (2, 0), (4, 6)]],
    "W": [[(0, 6), (1, 0), (2, 4), (3, 0), (4, 6)]],
    "X": [[(0, 0), (4, 6)], [(0, 6), (4, 0)]],
    "Y": [[(0, 6), (2, 3), (4, 6)], [(2, 3), (2, 0)]],
    "Z": [[(0, 6), (4, 6), (0, 0), (4, 0)]],
    "-": [[(1, 3), (3, 3)]],
    "+": [[(1, 3), (3, 3)], [(2, 2), (2, 4)]],
    ".": [[(2, 0), (2, 0.4)]],
    "_": [[(0, 0), (4, 0)]],
    " ": [],
}


def text_polylines_at(text: str, x: float, y: float, h: float, ang: float = 0.0):
    """Stroke-font polylines for `text`, centred at (x, y)."""
    cw = h * (5.0 / 7.0)
    scale = h / 7.0
    tw = cw * len(text)
    out = []
    for i, ch in enumerate(text.upper()):
        g = FONT.get(ch, FONT[" "])
        ox = -tw / 2 + cw * i
        oy = -h / 2
        for pl in g:
            pts = [(ox + px * scale, oy + (6 - py) * scale) for px, py in pl]
            pts = geom.rotate(pts, 0, 0, ang)
            out.append([(x + px, y + py) for px, py in pts])
    return out
