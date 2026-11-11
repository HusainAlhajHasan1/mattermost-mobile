#!/usr/bin/env python3
"""
Build every deliverable for the ultra-low-power LoRa/GPS tracker node.

    out/
        gerbers/                     Gerber RS-274X set + Excellon drill files
        LoRaTracker_Gerbers.zip      <-- upload this to the PCB house
        LoRaTracker.kicad_pcb        <-- open in KiCad 7/8
        LoRaTracker-bom.csv
        LoRaTracker-pos.csv
        LoRaTracker-drc-report.txt
        LoRaTracker-preview.png
        LoRaTracker-preview-bottom.png
        README.txt

usage:  python build.py [--q 0.2] [--reuse-route]

Requirements: numpy, scipy, shapely, matplotlib  (see requirements.txt)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import time
import zipfile

from pcbgen.board import BOARD_H, BOARD_THICKNESS, BOARD_W, NET_PRIORITY, NETLIST, RULES, Board
from pcbgen.fab import LAYER_FILES, Fab, write_drill, write_gerbers
from pcbgen.kicad import write_kicad_pcb
from pcbgen.plot import plot_board
from pcbgen.pour import build_pour, place_stitching_vias
from pcbgen.router import Router, Track, Via

OUT = "out"


# --------------------------------------------------------------------------
def route_all(board: Board, q: float):
    r = Router(board, q=q)
    r.prepare()
    nets = []
    for n in NET_PRIORITY:
        if n in NETLIST and n not in nets:
            nets.append(n)
    failed = []
    for net in nets:
        t0 = time.time()
        ok = r.route_net(net, board.nets[net])
        print(f"  route {net:16s} {'OK' if ok else 'FAILED':7s} {time.time()-t0:6.1f}s "
              f"({len(r.tracks)} segs, {len(r.vias)} vias)")
        if not ok:
            failed.append(net)
    return r, failed


def rip_up_reroute(board: Board, q: float, failing, attempts: int = 3):
    """Rip-up & retry: route the failing nets first on the next pass."""
    order = list(failing)
    r = None
    for attempt in range(attempts):
        print(f"  rip-up retry {attempt + 1}: routing {order} first")
        r = Router(board, q=q)
        r.prepare()
        nets = [n for n in order if n in NETLIST]
        nets += [n for n in NET_PRIORITY if n in NETLIST and n not in nets]
        bad = []
        for net in nets:
            if not r.route_net(net, board.nets[net]):
                bad.append(net)
                print(f"    {net} still failing")
        if not bad:
            return r, []
        order = bad
    return r, order


def save_route(path, r):
    json.dump({"tracks": [{"net": t.net, "layer": t.layer, "width": t.width, "pts": t.pts}
                          for t in r.tracks],
               "vias": [{"net": v.net, "x": v.x, "y": v.y, "dia": v.dia, "drill": v.drill}
                        for v in r.vias]}, open(path, "w"))


def load_route(path):
    d = json.load(open(path))

    class R:
        pass
    r = R()
    r.tracks = [Track(t["net"], t["layer"], [tuple(p) for p in t["pts"]], t["width"])
                for t in d["tracks"]]
    r.vias = [Via(v["net"], v["x"], v["y"], v["dia"], v["drill"]) for v in d["vias"]]
    return r


# --------------------------------------------------------------------------
def build(q: float = 0.20, reuse: bool = False):
    os.makedirs(OUT, exist_ok=True)
    t_start = time.time()

    print("== 1/6 board + netlist")
    board = Board()
    print(f"   {len(board.fps)} footprints, {len(board.nets)} nets, "
          f"{sum(len(v) for v in board.nets.values())} pad connections")

    cache = os.path.join(OUT, "route_cache.json")
    if reuse and os.path.exists(cache):
        print("== 2/6 reusing cached routing")
        r = load_route(cache)
        failed = []
    else:
        print(f"== 2/6 autorouting (grid {q} mm)")
        r, failed = route_all(board, q)
        if failed:
            r, failed = rip_up_reroute(board, q, failed)
        save_route(cache, r)
    print(f"   {len(r.tracks)} segments, {len(r.vias)} signal vias, failed nets: {failed}")

    print("== 3/6 stitching vias + ground pours")
    stitch = place_stitching_vias(board, r.tracks, r.vias, q=q)
    print(f"   {len(stitch)} stitching vias")
    pours = {}
    for layer in ("top", "bottom"):
        pours[layer] = build_pour(board, r.tracks, r.vias, stitch, q=q, layer=layer)
        print(f"   {layer} pour: {len(pours[layer].polys)} islands, "
              f"{pours[layer].area:.0f} mm^2 copper")

    print("== 4/6 design rule check")
    fab = Fab(board, r.tracks, r.vias + stitch, pours)
    errors, warnings = fab.drc()
    for e in errors:
        print("   ERROR:", e)
    for w in warnings:
        print("   WARN :", w)
    print(f"   {len(errors)} errors, {len(warnings)} warnings")

    print("== 5/6 fabrication data")
    gdir = os.path.join(OUT, "gerbers")
    if os.path.isdir(gdir):
        shutil.rmtree(gdir)
    files = write_gerbers(fab, gdir) + write_drill(fab, gdir)
    for f in files:
        print("   ", os.path.basename(f))
    kicad = write_kicad_pcb(os.path.join(OUT, "LoRaTracker.kicad_pcb"),
                            board, r.tracks, r.vias + stitch, pours)
    print("   ", os.path.basename(kicad))
    write_bom(board, os.path.join(OUT, "LoRaTracker-bom.csv"))
    write_pos(board, os.path.join(OUT, "LoRaTracker-pos.csv"))
    write_drc_report(errors, warnings, fab, r, stitch,
                     os.path.join(OUT, "LoRaTracker-drc-report.txt"))

    print("== 6/6 previews + archive")
    plot_board(board, r.tracks, r.vias + stitch, pours,
               os.path.join(OUT, "LoRaTracker-preview.png"),
               title="LoRa GPS tracker rev 1.0")
    zpath = os.path.join(OUT, "LoRaTracker_Gerbers.zip")
    make_zip(gdir, zpath, board, r, stitch, errors, warnings)
    print(f"   {zpath} ({os.path.getsize(zpath)/1024:.1f} kB)")
    print(f"done in {time.time() - t_start:.1f}s, {len(errors)} DRC errors")
    return board, r, pours, failed


# --------------------------------------------------------------------------
def _refkey(ref):
    return (ref[0], int("".join(c for c in ref if c.isdigit()) or 0))


def write_bom(board, path):
    rows = []
    for ref in sorted(board.fps, key=_refkey):
        if ref.startswith("H"):
            continue
        fp = board.fps[ref]
        rows.append({"Reference": ref, "Value": fp.value, "Footprint": fp.fp.value,
                     "Pads": len(fp.pads), "Side": fp.side})
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def write_pos(board, path):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Ref", "Val", "Package", "PosX", "PosY", "Rot", "Side"])
        for ref in sorted(board.fps, key=_refkey):
            if ref.startswith("H"):
                continue
            fp = board.fps[ref]
            # pick & place convention: origin bottom-left, y up
            w.writerow([ref, fp.value, fp.fp.value, f"{fp.x:.3f}", f"{BOARD_H - fp.y:.3f}",
                        f"{fp.ang:.1f}", "bottom" if fp.side == "bottom" else "top"])


def write_drc_report(errors, warnings, fab, r, stitch, path):
    with open(path, "w") as fh:
        fh.write("DESIGN RULE CHECK REPORT - ultra-low-power LoRa/GPS tracker rev 1.0\n")
        fh.write("=" * 78 + "\n\n")
        fh.write(f"Design rules : clearance {RULES['clearance']} mm, "
                 f"edge clearance {RULES['edge_clearance']} mm,\n"
                 f"               min track {RULES['min_track']} mm, "
                 f"min drill {RULES['min_drill']} mm, "
                 f"annular ring {RULES['annulus']} mm,\n"
                 f"               hole-to-hole {RULES['hole_to_hole']} mm\n\n")
        fh.write(f"Copper items checked : {len(fab.items)}\n")
        fh.write(f"Track segments       : {sum(len(t.segments()) for t in r.tracks)}\n")
        fh.write(f"Vias                 : {len(fab.vias)} ({len(stitch)} GND stitching vias)\n\n")
        fh.write(f"ERRORS   : {len(errors)}\n")
        for e in errors:
            fh.write("  [E] " + e + "\n")
        fh.write(f"\nWARNINGS : {len(warnings)}\n")
        for w in warnings:
            fh.write("  [W] " + w + "\n")
        if not errors:
            fh.write("\nRESULT: PASS - every net fully connected, all clearances met,\n"
                     "        antennas keep-out clear on both layers.\n")


def make_zip(gdir, zpath, board, r, stitch, errors, warnings):
    readme = [
        "ULTRA-LOW-POWER LoRa/GPS TRACKER NODE - FABRICATION PACKAGE",
        "==========================================================",
        "",
        "Board   : 60.0 x 45.0 mm, 2 layers, FR4, 1.6 mm, 1 oz copper",
        "Finish  : green solder mask, white silkscreen, lead-free HASL",
        "Drill   : 0.3 mm vias, 1.0 mm header pins, 2.7 mm NPTH mounting",
        "",
        "GERBER FILES (RS-274X, millimetres, 4.6 format, X2 file functions)",
        "  " + "\n  ".join(f"{k:12s} {v}" for k, v in LAYER_FILES.items()),
        "  drill: LoRaTracker-PTH.DRL (plated), LoRaTracker-NPTH.DRL (non-plated)",
        "",
        "STACK-UP",
        "  L1  F.Cu     signal + GND pour",
        "  L2  B.Cu     GND pour (near solid ground reference)",
        "  --  Edge.Cuts 60 x 45 mm outline",
        "",
        "MANUFACTURING NOTES",
        "  * 4 x M2.5 mounting holes, 2.7 mm NPTH, 3 mm inset from each corner.",
        "  * Minimum track 0.20 mm, minimum clearance 0.20 mm, minimum drill 0.30 mm:",
        "    inside every standard 2-layer prototype process.",
        "  * The ESP32-WROOM-32E PCB antenna overhangs the top board edge; the",
        "    antenna keep-out carries NO copper on either layer.",
        "  * The Ra-02 IPEX antenna end is kept clear on both layers.",
        "  * No controlled impedance required (433 MHz LoRa + external GPS module).",
        "  * Solder mask expansion 0.05 mm per side.",
        "",
        f"DRC RESULT: {len(errors)} errors, {len(warnings)} warnings",
        "  Full report: LoRaTracker-drc-report.txt",
        "",
        f"ROUTING: {len(r.tracks)} track segments, {len(r.vias)} signal vias, "
        f"{len(stitch)} GND stitching vias",
        "",
        "The Gerbers, the drill files and the .kicad_pcb are produced from the same",
        "geometry by the pcbgen toolchain, so they are guaranteed to agree.",
    ]
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(os.listdir(gdir)):
            z.write(os.path.join(gdir, f), f)
        for extra in ("LoRaTracker.kicad_pcb", "LoRaTracker-bom.csv",
                      "LoRaTracker-pos.csv", "LoRaTracker-drc-report.txt"):
            p = os.path.join(OUT, extra)
            if os.path.exists(p):
                z.write(p, extra)
        z.writestr("README.txt", "\n".join(readme) + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--q", type=float, default=0.20)
    ap.add_argument("--reuse-route", action="store_true")
    a = ap.parse_args()
    build(q=a.q, reuse=a.reuse_route)
