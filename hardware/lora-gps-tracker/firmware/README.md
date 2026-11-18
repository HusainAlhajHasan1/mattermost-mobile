# Firmware — RP2040 USB + Ra-02 (SX1278) link tester

Two interchangeable sketches. Pick one based on whether you can move three wires.

| Sketch | SPI bus | Wiring needed |
|---|---|---|
| `rp2040_lora_hwspi/` (recommended) | hardware SPI0 | MOSI→GP3, MISO→GP4, SCK→GP2 |
| `rp2040_lora_softspi/` | bit-banged | the user's table exactly, as written |

## Why there are two

The RP2040's SPI0 alternate function is fixed in silicon (datasheet table 2):

| signal | valid GPIO |
|---|---|
| SCK | GP2, GP6, GP10, GP14, GP18 |
| TX (MOSI) | GP3, GP7, GP11, GP15, GP19 |
| RX (MISO) | GP0, GP4, GP8, GP12, GP16 |
| CSn | GP1, GP5, GP9, GP13, GP17 |

The original table has **MOSI on GP2 (an SCK pin), MISO on GP3 (a MOSI pin),
SCK on GP4 (a MISO pin)** — the three data lines are rotated by one. Hardware
SPI cannot be routed that way, so either move the wires (`hwspi`) or bit-bang
the bus (`softspi`). `NSS→GP1`, `RST→GP29`, `DIO0→GP28` are correct in both and
need no change; GP28/GP29 are plain GPIO and fine for DIO0 interrupt + reset.

## Wiring

```
Ra-02            RP2040                 original table   hwspi build
------           ------                 --------------   -----------
GND              GND                    GND              unchanged
3V3              3V3  (3.3 V ONLY)      3V3              unchanged
RESET            GP29                   GP29             unchanged
DIO0             GP28                   GP28             unchanged
NSS              GP1                    GP1              unchanged
MOSI             GP2  -> move to GP3    GP2 (SCK)        GP3
MISO             GP3  -> move to GP4    GP3 (MOSI)       GP4
SCK              GP4  -> move to GP2    GP4 (MISO)       GP2
```

**3.3 V only.** The SX1278 is not 5 V tolerant. If your RP2040 board feeds 5 V
onto the 3V3 pin (some clones do when USB-powered through a bad regulator), stop
and use the regulated 3V3 pin only. Common GND is mandatory.

Never transmit without an antenna or a 50 Ω terminator on the IPEX/u.FL port.

## Arduino IDE setup

1. **Boards Manager** → install *Raspberry Pi Pico/RP2040* (Earle Philhower).
2. **Library Manager** → install *LoRa* by Sandeep Mistry (only needed for the
   `hwspi` sketch; the `softspi` sketch is dependency-free).
3. Select your RP2040 USB board, then *Sketch → Upload*.
4. Open Serial Monitor at **115200** baud.

## Serial commands

| key | action |
|---|---|
| `r` | receive mode (default at boot) |
| `t` | send one test packet |
| `a` | auto-beacon every 5 s |
| `s` | stop / idle |
| `m<text>` | send `<text>` as the payload, e.g. `mhello world` |
| `i` | print status (mode, node, freq, SF, counters, last RSSI/SNR) |
| `h` | help |

Packet format: `TK,<node>,<seq>,<millis>`. Bench test: flash two boards with
different `NODE_ID`, put both in receive mode, press `t` on one.

Defaults: 433 MHz, SF9, BW 125 kHz, CR 4/5, +17 dBm, sync word 0x12, CRC on.

## Bring-up checklist

1. Boots, prints `SX127x version register: 0x12` (softspi) or no `LoRa.begin()
   failed` (hwspi) → SPI is wired correctly.
2. `[tx] ...` then `[rx] ...` on the second board → RF path works.
3. No antenna / bad antenna shows up as `LoRa.begin()` failing or zero range.

## If you breadboard this onto the tracker PCB

The committed PCB (`../../out/LoRaTracker.kicad_pcb`) is laid out for
**U1 = ESP32-WROOM-32E**. It does not fit an RP2040 board and the pin mapping in
this firmware does not match that netlist. Two options:

- Breadboard the RP2040 + Ra-02 outside the PCB for the firmware bring-up, or
- Re-layout U1 for the RP2040 module/castellation and re-route SPI0 as in the
  table above (same SPI0 pins the ESP32 uses — HSPI/VSPI mapping differs, so the
  netlist's SPI nets must be re-checked pin by pin).

Nothing in this folder changes the existing board or its Gerbers.
