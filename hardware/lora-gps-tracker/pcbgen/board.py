"""
Board definition: design rules, netlist, placement.

Coordinate frame: millimetres, origin at the board's top-left corner,
x to the right, y downwards.  The board is 60.0 x 45.0 mm.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from . import footprints as FP

# --------------------------------------------------------------------------
# board / process parameters
# --------------------------------------------------------------------------
BOARD_W = 60.0
BOARD_H = 45.0
BOARD_THICKNESS = 1.6
LAYERS = 2
COPPER_OZ = 1

RULES = dict(
    clearance=0.20,          # min copper-copper clearance (8 mil)
    edge_clearance=0.30,     # copper to board edge
    min_track=0.20,
    min_drill=0.30,
    hole_to_hole=0.30,
    annulus=0.15,
    silk_width=0.15,
    silk_clearance=0.15,
    mask_expansion=0.05,
)

VIA_DIA = 0.60
VIA_DRILL = 0.30

TRACK_POWER = 0.60           # 24 mil
TRACK_SIGNAL = 0.254         # 10 mil

POWER_NETS = {"3V3", "VCC_SW", "GND"}

# Routing order: power rails first (wide and hard to route), then the short
# critical drops, then the buses.
NET_PRIORITY = [
    "3V3", "VCC_SW",
    "NET_PWR_CTRL", "NET_MOSFET_GATE", "RTC_VBAT",
    "SPI_SCK", "SPI_MISO", "SPI_MOSI",
    "VIB_WAKE", "RTC_INT_WAKE", "LORA_RST", "LORA_DIO0",
    "I2C_SDA", "I2C_SCL", "LORA_CS", "SD_CS",
    "NET_EN", "NET_IO0", "NET_TXD0", "NET_RXD0",
    "GPS_RX_ESP", "GPS_TX_ESP",
]


# --------------------------------------------------------------------------
# netlist (net -> list of (reference, pad))
# --------------------------------------------------------------------------
NETLIST: Dict[str, List[Tuple[str, str]]] = {
    "GND": [
        ("U1", "1"), ("U1", "15"), ("U1", "38"), ("U1", "39"),
        ("U2", "1"), ("U2", "8"), ("U2", "9"), ("U2", "16"),
        ("U3", "5"), ("BAT1", "2"),
        ("J1", "6"), ("J1", "MP"),
        ("J2", "2"), ("J3", "2"), ("J4", "2"), ("SW2", "2"),
        ("C1", "2"), ("C2", "2"), ("C3", "2"), ("C4", "2"),
        ("C5", "2"), ("C6", "2"), ("R10", "2"),
    ],
    "3V3": [
        ("J4", "1"), ("U1", "2"), ("U2", "2"), ("U3", "2"), ("Q1", "2"),
        ("J3", "1"), ("SW1", "1"),
        ("C1", "1"), ("C2", "1"), ("C4", "1"), ("C5", "1"),
        ("R1", "1"), ("R2", "1"), ("R3", "1"), ("R4", "1"), ("R5", "1"),
        ("R7", "1"), ("R8", "1"), ("R9", "1"),
    ],
    "VCC_SW": [("Q1", "3"), ("J1", "4"), ("J2", "1"), ("C6", "1")],
    "NET_EN": [("U1", "3"), ("R1", "2"), ("C3", "1"), ("SW2", "1")],
    "NET_IO0": [("U1", "25"), ("R2", "2"), ("J3", "5")],
    "NET_TXD0": [("U1", "35"), ("J3", "3")],
    "NET_RXD0": [("U1", "34"), ("J3", "4")],
    "NET_MOSFET_GATE": [("Q1", "1"), ("R3", "2"), ("R6", "1")],
    "NET_PWR_CTRL": [("U1", "10"), ("R6", "2")],
    "SPI_SCK": [("U1", "30"), ("U2", "12"), ("J1", "5")],
    "SPI_MISO": [("U1", "31"), ("U2", "13"), ("J1", "7")],
    "SPI_MOSI": [("U1", "37"), ("U2", "14"), ("J1", "3")],
    "LORA_CS": [("U1", "29"), ("U2", "15"), ("R4", "2")],
    "LORA_RST": [("U1", "13"), ("U2", "4")],
    "LORA_DIO0": [("U1", "24"), ("U2", "5")],
    "SD_CS": [("U1", "26"), ("J1", "2"), ("R5", "2")],
    "I2C_SDA": [("U1", "33"), ("U3", "7"), ("R7", "2")],
    "I2C_SCL": [("U1", "36"), ("U3", "8"), ("R8", "2")],
    "RTC_INT_WAKE": [("U1", "8"), ("U3", "3"), ("R9", "2")],
    "RTC_VBAT": [("U3", "6"), ("BAT1", "1")],
    "GPS_RX_ESP": [("U1", "27"), ("J2", "3")],
    "GPS_TX_ESP": [("U1", "28"), ("J2", "4")],
    "VIB_WAKE": [("U1", "9"), ("SW1", "2"), ("R10", "1")],
}


# --------------------------------------------------------------------------
# placement  (ref, x, y, angle, side)
# --------------------------------------------------------------------------
@dataclass
class Placement:
    ref: str
    x: float
    y: float
    ang: float = 0.0
    side: str = "top"


PLACEMENT: List[Placement] = [
    # --- main modules -----------------------------------------------------
    # ESP32-WROOM-32E: module body y = 0 .. 25.5, so the PCB antenna section
    # overhangs the top board edge (Espressif keep-out).
    Placement("U1", 15.50, 12.75, 0.0),
    # Ra-02 LoRa, IPEX antenna end pointing up (keep-out above it)
    Placement("U2", 43.00, 30.50, 0.0),
    # DS3231MZ RTC
    Placement("U3", 30.50, 10.00, 0.0),
    # microSD socket, card mouth faces the right board edge
    Placement("J1", 51.00, 12.00, 270.0),
    # CR1220 back-up cell, bottom side under the ESP32
    Placement("BAT1", 22.50, 27.50, 0.0, "bottom"),
    # --- connectors along the bottom edge ---------------------------------
    Placement("J4", 7.50, 42.50, 0.0),       # LiFePO4 battery input
    Placement("J3", 16.00, 42.50, 0.0),      # UART flashing / programming
    Placement("J2", 26.60, 42.50, 0.0),      # external GPS module
    Placement("SW1", 34.20, 42.50, 0.0),     # SW-420 vibration sensor
    # --- reset button, left edge -----------------------------------------
    Placement("SW2", 2.40, 33.80, 90.0),
    # --- power switch cluster (bottom left) -------------------------------
    Placement("Q1", 4.00, 38.50, 0.0),       # AO3401A high-side P-FET
    Placement("R6", 4.00, 30.00, 0.0),       # 100R gate resistor
    Placement("R3", 8.20, 30.00, 0.0),       # 10k gate pull-up
    Placement("C1", 12.00, 37.50, 0.0),      # 10uF bulk
    Placement("C6", 30.00, 37.50, 0.0),      # 100nF on VCC_SW
    # --- ESP32 support passives (left strip) -------------------------------
    Placement("C4", 2.40, 6.60, 90.0),       # 10uF bulk
    Placement("C2", 2.40, 9.75, 90.0),
    Placement("R1", 2.40, 12.90, 90.0),      # EN pull-up
    Placement("C3", 2.40, 16.05, 90.0),      # EN RC delay
    Placement("R10", 2.40, 19.20, 90.0),     # vibration sensor pull-down
    Placement("R9", 2.40, 22.35, 90.0),      # RTC wake pull-up
    Placement("R2", 2.40, 25.50, 90.0),      # IO0 pull-up
    # --- RTC / LoRa / SD support ------------------------------------------
    Placement("C5", 26.00, 5.30, 90.0),      # RTC decoupling
    Placement("R7", 26.00, 10.50, 90.0),     # SDA pull-up
    Placement("R8", 26.00, 14.50, 90.0),     # SCL pull-up
    Placement("R4", 35.00, 17.00, 0.0),      # LORA_CS pull-up
    Placement("R5", 40.00, 16.00, 0.0),      # SD_CS pull-up
]

REF_FOOTPRINTS = {
    "U1": lambda r: FP.esp32_wroom(r),
    "U2": lambda r: FP.ra02(r),
    "U3": lambda r: FP.soic8(r, "DS3231MZ"),
    "BAT1": lambda r: FP.cr1220(r),
    "J1": lambda r: FP.microsd(r),
    "J2": lambda r: FP.header_tht(r, 4, "Conn_GPS_1x04"),
    "J3": lambda r: FP.header_tht(r, 5, "Conn_UART_1x05"),
    "J4": lambda r: FP.header_tht(r, 2, "Conn_PWR_1x02"),
    "SW1": lambda r: FP.header_tht(r, 2, "SW-420"),
    "SW2": lambda r: FP.tact_3x4(r, "SW_Reset"),
    "Q1": lambda r: FP.sot23(r, "AO3401A"),
    "C1": lambda r: FP.passive_0805(r, "10uF/16V"),
    "C4": lambda r: FP.passive_0805(r, "10uF/16V"),
    "C2": lambda r: FP.passive_0805(r, "100nF/50V"),
    "C3": lambda r: FP.passive_0805(r, "100nF/50V"),
    "C5": lambda r: FP.passive_0805(r, "100nF/50V"),
    "C6": lambda r: FP.passive_0805(r, "100nF/50V"),
    "R1": lambda r: FP.passive_0805(r, "10k"),
    "R2": lambda r: FP.passive_0805(r, "10k"),
    "R3": lambda r: FP.passive_0805(r, "10k"),
    "R4": lambda r: FP.passive_0805(r, "10k"),
    "R5": lambda r: FP.passive_0805(r, "10k"),
    "R6": lambda r: FP.passive_0805(r, "100R"),
    "R7": lambda r: FP.passive_0805(r, "10k"),
    "R8": lambda r: FP.passive_0805(r, "10k"),
    "R9": lambda r: FP.passive_0805(r, "10k"),
    "R10": lambda r: FP.passive_0805(r, "10k"),
}

MOUNTING_HOLES = [(3.0, 3.0), (57.0, 3.0), (3.0, 42.0), (57.0, 42.0)]
MOUNTING_DIA = 2.70

# physical module silhouettes (used for documentation / edge checks)
COMPONENT_EDGES = [
    ("U1", 6.50, 0.00, 24.50, 25.50),      # ESP32-WROOM-32E 18 x 25.5
    ("U2", 35.00, 22.50, 51.00, 38.50),    # Ra-02 16 x 16
    ("J1", 43.90, 7.80, 58.10, 16.20),     # microSD 14.2 x 8.4
]

# copper keep-outs (no copper on any layer), board coordinates
KEEPOUTS = [
    # ESP32 PCB antenna area - strict keep-out on BOTH layers
    (6.50, 0.00, 24.50, 5.20),
    # Ra-02 IPEX antenna end (RF section)
    (39.50, 22.50, 46.50, 24.60),
]


# --------------------------------------------------------------------------
class PlacedPad:
    __slots__ = ("ref", "num", "x", "y", "w", "h", "shape", "drill", "kind",
                 "net", "ang", "mask", "layer")

    def __init__(self, ref, num, x, y, w, h, shape, drill, kind, net, ang, mask):
        self.ref, self.num = ref, num
        self.x, self.y = x, y
        self.w, self.h = w, h
        self.shape, self.drill, self.kind = shape, drill, kind
        self.net, self.ang, self.mask = net, ang, mask
        self.layer = "top" if kind == "smd" else "both"

    @property
    def is_tht(self):
        return self.drill > 0.0

    @property
    def label(self):
        return f"{self.ref}.{self.num}"

    def shape_wh(self):
        """(w, h) rotated into the board frame."""
        a = abs(self.ang) % 180.0
        if abs(a - 90.0) < 1e-6:
            return (self.h, self.w)
        return (self.w, self.h)


class PlacedFootprint:
    def __init__(self, ref, value, fp, x, y, ang, side):
        self.ref, self.value, self.fp = ref, value, fp
        self.x, self.y, self.ang, self.side = x, y, ang, side
        self.pads: List[PlacedPad] = []
        mirror = (side == "bottom")
        for p in fp.pads:
            wx, wy = FP.place((p.x, p.y), (x, y), ang, mirror)
            self.pads.append(PlacedPad(ref, p.num, wx, wy, p.w, p.h, p.shape,
                                       p.drill, p.kind, None, ang, p.mask))
        self.msilk = "bottom" if side == "bottom" else "top"
        for p in self.pads:
            if p.kind == "smd" and side == "bottom":
                p.layer = "bottom"
        self.silk = [FP.transform_outline(o, (x, y), ang, mirror)
                     for o in FP.normalize_outlines(fp.silk)]
        self.fab = [FP.transform_outline(o, (x, y), ang, mirror)
                    for o in FP.normalize_outlines(fp.fab)]
        self.courtyard = [FP.transform_outline(o, (x, y), ang, mirror)
                          for o in FP.normalize_outlines(fp.courtyard)]
        self.keepouts = []
        for (x0, y0, x1, y1) in fp.keepouts:
            pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            tp = FP.transform_outline(pts, (x, y), ang, mirror)
            self.keepouts.append((min(p[0] for p in tp), min(p[1] for p in tp),
                                  max(p[0] for p in tp), max(p[1] for p in tp)))
        self.npth = [(FP.place((hx, hy), (x, y), ang, mirror), d) for hx, hy, d in fp.npth]
        rx, ry = FP.place(fp.ref_off, (x, y), ang, mirror)
        self.ref_pos = (rx, ry)
        self.body_h = fp.body_h

    def pad(self, num):
        for p in self.pads:
            if p.num == num:
                return p
        raise KeyError(f"{self.ref} has no pad {num}")


class Board:
    def __init__(self):
        self.fps: Dict[str, PlacedFootprint] = {}
        self.nets: Dict[str, List[PlacedPad]] = {}
        self._build()

    def _build(self):
        for pl in PLACEMENT:
            fp = REF_FOOTPRINTS[pl.ref](pl.ref)
            self.fps[pl.ref] = PlacedFootprint(pl.ref, fp.value, fp,
                                               pl.x, pl.y, pl.ang, pl.side)
        for net, ends in NETLIST.items():
            pads = []
            for (ref, num) in ends:
                cands = [p for p in self.fps[ref].pads if p.num == num]
                if not cands:
                    raise KeyError(f"pad {ref}.{num} not found")
                for c in cands:
                    c.net = net
                    pads.append(c)
            self.nets[net] = pads
        for i, (mx, my) in enumerate(MOUNTING_HOLES, start=1):
            fp = FP.mounting_hole(f"H{i}", MOUNTING_DIA)
            self.fps[f"H{i}"] = PlacedFootprint(f"H{i}", fp.value, fp, mx, my, 0, "top")

    # -- queries ---------------------------------------------------------
    def all_pads(self):
        for fp in self.fps.values():
            for p in fp.pads:
                yield p

    def net_width(self, net: str) -> float:
        return TRACK_POWER if net in POWER_NETS else TRACK_SIGNAL

    def keepout_rects(self):
        return list(KEEPOUTS)
