"""
KiCad 8 (.kicad_pcb) writer.

The board is exported as a native KiCad board:

    * real footprints (pads, silk, fab, courtyard on F.Cu / B.Cu)
    * every track segment and every via
    * GND zones on both layers, pre-filled with the copper this generator
      computed, plus rule areas (keep-outs) over the antenna zones
    * Edge.Cuts outline, nets, net classes and design rules

Opening it in KiCad 7/8 and pressing B (refill zones) reproduces the same copper;
the Gerber set shipped next to it is generated from the same geometry, so the two
agree.
"""

from __future__ import annotations

import uuid
from typing import Dict, List

from .board import BOARD_H, BOARD_THICKNESS, BOARD_W, KEEPOUTS, RULES, Board
from .footprints import normalize_outlines
from .pour import Pour
from .router import Track, Via

_uuid_ns = uuid.UUID("6f1c8f7e-5b91-4c4b-9a3e-0d1e2f3a4b5c")


def _u(*parts) -> str:
    return str(uuid.uuid5(_uuid_ns, "|".join(str(p) for p in parts)))


def f(v: float) -> str:
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


class SExpr:
    def __init__(self):
        self.out: List[str] = []
        self.level = 0

    def open(self, name: str, *args):
        bits = [name] + [str(a) for a in args if a is not None and a != ""]
        self.out.append("  " * self.level + "(" + " ".join(bits))
        self.level += 1

    def close(self):
        self.level -= 1
        self.out[-1] += ")"

    def line(self, *bits):
        self.out.append("  " * self.level + "(" + " ".join(str(b) for b in bits) + ")")

    def text(self) -> str:
        return "\n".join(self.out) + "\n"


def write_kicad_pcb(path: str, board: Board, tracks: List[Track], vias: List[Via],
                    pours: Dict[str, Pour], title: str = "Ultra-low-power LoRa/GPS tracker"):
    b = board
    s = SExpr()
    s.open("kicad_pcb", "(version 20240108)", "(generator pcbgen)",
           '(generator_version "8.0")')
    s.open("general")
    s.line("thickness", f(BOARD_THICKNESS))
    s.close()
    s.line('paper "A4"')
    s.open("title_block")
    s.line(f'title "{title}"')
    s.line('date "2026-11-08"')
    s.line('rev "1.0"')
    s.line('company "Arena.ai - hardware engineering"')
    s.line('comment 1 "2-layer FR4 1.6 mm, 1 oz, 60 x 45 mm"')
    s.line('comment 2 "Power 0.6 mm / signal 0.254 mm, clearance 0.2 mm, via 0.6/0.3"')
    s.close()

    s.open("layers")
    s.line('0 "F.Cu" signal')
    s.line('31 "B.Cu" signal')
    s.line('32 "B.Adhes" user "B.Adhesive"')
    s.line('33 "F.Adhes" user "F.Adhesive"')
    s.line('34 "B.Paste" user')
    s.line('35 "F.Paste" user')
    s.line('36 "B.SilkS" user "B.Silkscreen"')
    s.line('37 "F.SilkS" user "F.Silkscreen"')
    s.line('38 "B.Mask" user')
    s.line('39 "F.Mask" user')
    s.line('40 "Dwgs.User" user "User.Drawings"')
    s.line('41 "Cmts.User" user "User.Comments"')
    s.line('42 "Eco1.User" user "User.Eco1"')
    s.line('43 "Eco2.User" user "User.Eco2"')
    s.line('44 "Edge.Cuts" user')
    s.line('45 "Margin" user')
    s.line('46 "B.CrtYd" user "B.Courtyard"')
    s.line('47 "F.CrtYd" user "F.Courtyard"')
    s.line('48 "B.Fab" user')
    s.line('49 "F.Fab" user')
    s.close()

    s.open("setup")
    s.line("pad_to_mask_clearance", f(RULES["mask_expansion"]))
    s.line("solder_mask_min_width 0.1")
    s.line("allow_soldermask_bridges_in_footprints no")
    s.close()

    s.open("net_class", '"Default"', '"Default net class"')
    s.line("clearance", f(RULES["clearance"]))
    s.line("trace_width", f(0.254))
    s.line("via_dia", "0.6")
    s.line("via_drill", "0.3")
    s.line("add_net 3V3")
    s.close()
    s.open("net_class", '"Power"', '"power rails and ground"')
    s.line("clearance", f(RULES["clearance"]))
    s.line("trace_width", "0.6")
    s.line("via_dia", "0.6")
    s.line("via_drill", "0.3")
    s.line("add_net GND")
    s.line("add_net 3V3")
    s.line("add_net VCC_SW")
    s.close()

    s.line('net 0 ""')
    net_ids = {}
    for i, net in enumerate(sorted(b.nets.keys()), start=1):
        net_ids[net] = i
        s.line(f'net {i} "{net}"')

    # ---- footprints -------------------------------------------------
    for ref in sorted(b.fps.keys()):
        fp = b.fps[ref]
        if ref.startswith("H"):
            _mounting_footprint(s, fp)
            continue
        src = fp.fp
        layer = "B.Cu" if fp.side == "bottom" else "F.Cu"
        silk = "B.SilkS" if fp.side == "bottom" else "F.SilkS"
        fabl = "B.Fab" if fp.side == "bottom" else "F.Fab"
        crt = "B.CrtYd" if fp.side == "bottom" else "F.CrtYd"
        s.open("footprint", f'"pcbgen:{_safe(src.value)}"', f'(layer "{layer}")',
               f'(uuid "{_u("fp", ref)}")', f"(at {f(fp.x)} {f(fp.y)} {f(fp.ang)})")
        s.line(f'(descr "{src.value}")')
        s.line(f'(tags "{ref}")')
        s.line("attr smd" if any(p.kind == "smd" for p in src.pads) else "attr through_hole")
        s.open("fp_text", "reference", f'"{ref}"',
               f"(at {f(fp.ref_pos[0])} {f(fp.ref_pos[1])} 0)", f'(layer "{silk}")',
               f'(uuid "{_u("ref", ref)}")')
        s.line("effects (font (size 0.9 0.9) (thickness 0.15)) (justify center)")
        s.close()
        s.open("fp_text", "value", f'"{src.value}"', f"(at {f(fp.x)} {f(fp.y)} 0)",
               f'(layer "{fabl}")', f'(uuid "{_u("val", ref)}")')
        s.line("effects (font (size 0.8 0.8) (thickness 0.12)) (justify center) hide")
        s.close()
        for outline in normalize_outlines(src.silk):
            _poly_lines(s, outline, silk, RULES["silk_width"], ref, "silk")
        for outline in normalize_outlines(src.fab):
            _poly_lines(s, outline, fabl, 0.1, ref, "fab")
        for outline in normalize_outlines(src.courtyard):
            _poly_lines(s, outline, crt, 0.05, ref, "crt")
        for p in src.pads:
            _pad(s, p, fp, ref, net_ids)
        s.close()

    # ---- tracks -----------------------------------------------------
    for i, t in enumerate(tracks):
        layer = "F.Cu" if t.layer == "top" else "B.Cu"
        for j, (a, c) in enumerate(t.segments()):
            s.line(f"(segment (start {f(a[0])} {f(a[1])}) (end {f(c[0])} {f(c[1])}) "
                   f"(width {f(t.width)}) (layer \"{layer}\") (net {net_ids[t.net]}) "
                   f"(uuid \"{_u('seg', i, j)}\"))")

    # ---- vias -------------------------------------------------------
    for i, v in enumerate(vias):
        s.line(f"(via (at {f(v.x)} {f(v.y)}) (size {f(v.dia)}) (drill {f(v.drill)}) "
               f"(layers \"F.Cu\" \"B.Cu\") (net {net_ids.get(v.net, 1)}) "
               f"(uuid \"{_u('via', i)}\"))")

    # ---- edge cuts --------------------------------------------------
    pts = [(0, 0), (BOARD_W, 0), (BOARD_W, BOARD_H), (0, BOARD_H)]
    for i in range(4):
        a, c = pts[i], pts[(i + 1) % 4]
        s.line(f"(gr_line (start {f(a[0])} {f(a[1])}) (end {f(c[0])} {f(c[1])}) "
               f"(stroke (width 0.15) (type solid)) (layer \"Edge.Cuts\") "
               f"(uuid \"{_u('edge', i)}\"))")

    # ---- antenna rule areas ----------------------------------------
    zones = list(KEEPOUTS) + [k for fp in b.fps.values() for k in fp.keepouts]
    for i, (x0, y0, x1, y1) in enumerate(zones):
        for lname, tag in (("F.Cu", "ko"), ("B.Cu", "kob")):
            s.open("zone", "(net 0)", '(net_name "keepout")', f'(layer "{lname}")',
                   f'(uuid "{_u(tag, i)}")', "(hatch edge 0.5)")
            s.line("(connect_pads (clearance 0))")
            s.line("(min_thickness 0.25)")
            s.line("(keepout (tracks not_allowed) (vias not_allowed) (pads not_allowed) "
                   "(copperpour not_allowed) (footprints allowed))")
            s.open("polygon")
            s.open("pts")
            for (px, py) in [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]:
                s.line(f"xy {f(px)} {f(py)}")
            s.close()
            s.close()
            s.close()

    # ---- ground planes ---------------------------------------------
    for layer, pour in pours.items():
        if not pour.polys:
            continue
        lname = "F.Cu" if layer == "top" else "B.Cu"
        s.open("zone", "(net 1)", '(net_name "GND")', f'(layer "{lname}")',
               f'(uuid "{_u("zone", layer)}")', "(hatch edge 0.5)")
        s.line(f"(connect_pads (clearance {f(RULES['clearance'])}))")
        s.line("(min_thickness 0.25)")
        s.line("(filled_areas_thickness no)")
        s.line("(fill yes (thermal_gap 0.3) (thermal_bridge_width 0.4))")
        s.open("polygon")
        s.open("pts")
        for (px, py) in [(0.3, 0.3), (BOARD_W - 0.3, 0.3),
                         (BOARD_W - 0.3, BOARD_H - 0.3), (0.3, BOARD_H - 0.3)]:
            s.line(f"xy {f(px)} {f(py)}")
        s.close()
        s.close()
        for sh in pour.resolved():
            s.open("filled_polygon", f'(layer "{lname}")')
            s.open("pts")
            for (px, py) in list(sh.exterior.coords)[:-1]:
                s.line(f"xy {f(px)} {f(py)}")
            s.close()
            for ring in sh.interiors:
                s.open("pts")
                for (px, py) in list(ring.coords)[:-1]:
                    s.line(f"xy {f(px)} {f(py)}")
                s.close()
            s.close()
        s.close()

    s.close()
    with open(path, "w") as fh:
        fh.write(s.text())
    return path


def _safe(name: str) -> str:
    return name.replace("/", "_").replace(":", "_").replace(" ", "_")


def _mounting_footprint(s: SExpr, fp):
    (pos, dia) = fp.npth[0]
    ref = fp.ref
    s.open("footprint", '"pcbgen:MountingHole_2.7mm"', '(layer "F.Cu")',
           f'(uuid "{_u("fp", ref)}")', f"(at {f(pos[0])} {f(pos[1])})")
    s.line('(descr "M2.5 mounting hole, non-plated")')
    s.line("attr exclude_from_pos_files exclude_from_bom")
    s.open("fp_text", "reference", f'"{ref}"',
           f"(at {f(pos[0])} {f(pos[1] - 2.3)} 0)", '(layer "F.SilkS")',
           f'(uuid "{_u("ref", ref)}")')
    s.line("effects (font (size 0.7 0.7) (thickness 0.12)) (justify center)")
    s.close()
    for outline in normalize_outlines(fp.fp.silk):
        _poly_lines(s, outline, "F.SilkS", 0.15, ref, "silk")
    s.line(f'(pad "" np_thru_hole circle (at {f(pos[0])} {f(pos[1])}) '
           f'(size {f(dia)} {f(dia)}) (drill {f(dia)}) (layers "*.Cu" "*.Mask") '
           f'(uuid "{_u("npth", ref)}"))')
    s.close()


def _poly_lines(s: SExpr, pts, layer, width, ref, tag):
    pts = list(pts)
    if len(pts) < 2:
        return
    for i in range(len(pts)):
        a = pts[i]
        c = pts[(i + 1) % len(pts)]
        if abs(a[0] - c[0]) < 1e-9 and abs(a[1] - c[1]) < 1e-9:
            continue
        s.line(f"(fp_line (start {f(a[0])} {f(a[1])}) (end {f(c[0])} {f(c[1])}) "
               f"(stroke (width {f(width)}) (type solid)) (layer \"{layer}\") "
               f"(uuid \"{_u(tag, ref, i, a[0], a[1])}\"))")


def _pad(s: SExpr, p, fp, ref, net_ids):
    layer = "B.Cu" if (fp.side == "bottom" and p.kind == "smd") else "F.Cu"
    if p.is_tht:
        layers = '"*.Cu" "*.Mask"'
        kind = "thru_hole"
    else:
        suffix = "B" if fp.side == "bottom" else "F"
        layers = f'"{layer}" "{suffix}.Paste" "{suffix}.Mask"'
        kind = "smd"
    shape = "circle" if p.shape == "circle" else "rect"
    net = p.net
    net_s = f'(net {net_ids[net]} "{net}")' if net in net_ids else ""
    drill = f"(drill {f(p.drill)}) " if p.is_tht else ""
    s.line(f"(pad \"{p.num}\" {kind} {shape} (at {f(p.x)} {f(p.y)}) "
           f"(size {f(p.w)} {f(p.h)}) {drill}(layers {layers}) {net_s} "
           f"(uuid \"{_u('pad', ref, p.num, p.x, p.y)}\"))")
