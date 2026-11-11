"""Fast placement sanity check: pad-to-pad and pad-to-edge clearances."""
from shapely.geometry import Point, box
from shapely.strtree import STRtree

from pcbgen.board import BOARD_H, BOARD_W, RULES, Board

b = Board()
items = []
for fp in b.fps.values():
    for p in fp.pads:
        w, h = p.shape_wh()
        sh = (Point(p.x, p.y).buffer(p.w / 2, quad_segs=12) if p.shape == "circle"
              else box(p.x - w / 2, p.y - h / 2, p.x + w / 2, p.y + h / 2))
        items.append((p, sh))
    for (pos, dia) in fp.npth:
        items.append((None, Point(pos).buffer(dia / 2, quad_segs=12)))

tree = STRtree([s for _, s in items])
bad = 0
for i, (p, s) in enumerate(items):
    for j in tree.query(s):
        if j <= i:
            continue
        p2, s2 = items[j]
        if p is not None and p2 is not None and p.net == p2.net and p.ref == p2.ref:
            continue
        d = s.distance(s2)
        if d < RULES["clearance"] - 1e-9:
            bad += 1
            print(f"PAD  gap {d:.3f}: {p.label if p else 'HOLE'} <-> {p2.label if p2 else 'HOLE'}")
    bb = s.bounds
    e = min(bb[0], bb[1], BOARD_W - bb[2], BOARD_H - bb[3])
    if e < 0.25:
        print(f"EDGE {p.label if p else 'HOLE'} margin {e:.3f}")
print(f"{bad} pad clearance violations")
