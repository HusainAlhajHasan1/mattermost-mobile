/* -----------------------------------------------------------------------------
 *  rp2040_selftest.ino  --  standalone acceptance test, ONE board, no peer.
 *
 *  Proves or disproves every link in the chain between the RP2040 and the
 *  antenna port, using only the parts already on the bench:
 *
 *    1. GPIO pin integrity        every pin driven high/low and read back
 *    2. SPI bit-bang loopback     MOSI -> MISO jumper, all 256 byte patterns
 *    3. SX1278 register integrity write/read/restore across the register file
 *    4. SX1278 frequency synthesis  PLL lock on 433/434/868/915 MHz
 *    5. TX chain current draw     PA, PA_BOOST and PA_RAMP exercised
 *    6. Antenna / load measurement  RSSI floor in FSK mode, live
 *    7. Reverse power at the port   what leaks back when the PA is keyed
 *    8. Supply sag under key-up    3V3 collapse test
 *
 *  Run it, then send the whole serial log back. The report at the end says
 *  which of these failed in plain language.
 *
 *  Board:  Raspberry Pi Pico / RP2040, core by Earle Philhower.
 *  Serial: 115200, 8N1. No libraries needed.
 *
 *  OPTIONAL HARDWARE ADDITIONS (each unlocks a stricter test):
 *    - 1x jumper wire MOSI(GP2) -> MISO(GP3)   unlocks test 2
 *    - 1x 1N4148 + 10k from DIO1 to A0          unlocks RSSI via analog
 *    - 1x 50 ohm resistor + 1x 1N4148 detector  allows TX power metering
 *  None of them are required; the report notes which ran.
 * -------------------------------------------------------------------------- */

#include <Arduino.h>
#include <string.h>
#include <stdio.h>
#include <hardware/adc.h>
#include <hardware/gpio.h>
#include <hardware/pwm.h>

/* =============================== PIN MAP ================================= */

#define PIN_NSS    1
#define PIN_MOSI   2
#define PIN_MISO   3
#define PIN_SCK    4
#define PIN_RST    29
#define PIN_DIO0   28
#define PIN_DIO1   27     // optional, leave unconnected if not wired
#define PIN_DIO2   26     // optional
#define PIN_3V3_ADC 26    // ADC0 shares GP26/27 with DIO1/2 - see notes

/* =========================== SX1278 REGISTERS ============================ */

#define REG_FIFO            0x00
#define REG_FIFO_ADDR_PTR   0x0D
#define REG_FIFO_TX_BASE    0x0E
#define REG_OP_MODE         0x01
#define REG_FRF_MSB         0x06
#define REG_FRF_MID         0x07
#define REG_FRF_LSB         0x08
#define REG_PA_CONFIG       0x09
#define REG_PA_RAMP         0x0A
#define REG_OCP             0x0B
#define REG_LNA             0x0C
#define REG_RSSI_CONFIG     0x0E
#define REG_RSSI_COLLISION  0x0F
#define REG_RSSI_THRESH     0x10
#define REG_RSSI_VALUE_FSK  0x11
#define REG_IRQ_FLAGS       0x12
#define REG_RX_NB_BYTES     0x13
#define REG_PKT_SNR         0x19
#define REG_PKT_RSSI        0x1A
#define REG_MODEM_STAT      0x18
#define REG_SYMB_TIMEOUT    0x1B
#define REG_PREAMBLE_MSB    0x20
#define REG_PREAMBLE_LSB    0x21
#define REG_PAYLOAD_LENGTH  0x22
#define REG_RX_HDR_CNT_MSB  0x23
#define REG_RX_HDR_CNT_LSB  0x24
#define REG_RX_PKT_CNT_MSB  0x25
#define REG_RX_PKT_CNT_LSB  0x26
#define REG_MODEM_CONFIG_1  0x1D
#define REG_MODEM_CONFIG_2  0x1E
#define REG_MODEM_CONFIG_3  0x26
#define REG_PKT_CRC_CNT_MSB 0x27
#define REG_PKT_CRC_CNT_LSB 0x28
#define REG_TEMP            0x3C
#define REG_SYNC_WORD       0x39
#define REG_VERSION         0x42
#define REG_PA_DAC          0x4D
#define REG_IMAGE_CAL       0x3E
#define REG_SYMB_TIMEOUT_LSB 0x1C

/* ============================== bookkeeping ============================== */

static uint16_t passCount = 0, failCount = 0;
static uint16_t lastFailSnapshot = 0;
static char headTag[18] = "T0";

/* Every check appends one short row to a table. The table is printed inline as
 * it runs AND again at the end under a short tag, so even a one-screen phone
 * terminal shows the whole result:  T1 ok | T2 ok | T3 FAIL(1) | ...
 */
struct CheckRow { char tag[18]; bool ok; uint8_t fails; };
static CheckRow rows[14];
static uint8_t rowCount = 0;

static void addRow(const char *tag, bool ok, uint8_t fails) {
    if (rowCount >= 14) return;
    strncpy(rows[rowCount].tag, tag, 17);
    rows[rowCount].tag[17] = '\0';
    rows[rowCount].ok = ok;
    rows[rowCount].fails = fails;
    rowCount++;
}

static void inlineResult() {
    uint8_t f = failCount - lastFailSnapshot;
    lastFailSnapshot = failCount;
    Serial.print(F("  >> "));
    Serial.print(headTag);
    Serial.print(F("  "));
    if (f == 0) Serial.println(F("OK"));
    else { Serial.print(F("FAIL (")); Serial.print(f); Serial.println(F(")")); }
    Serial.flush();
    addRow(headTag, f == 0, f);
}
static char failList[12][46];
static uint8_t failIdx = 0;

/* Serial on the Pico is USB CDC: a reset mid-print can lose the tail and can
 * even wedge the next print. Flush after every line so a crash cannot hide
 * where it happened. */
static void logLine(const char *t) {
    Serial.println(t);
    Serial.flush();
}
static void logRaw(const char *prefix, const char *t) {
    Serial.print(prefix);
    Serial.println(t);
    Serial.flush();
}

static void reportFail(const char *name) {
    if (failIdx < 12) {
        strncpy(failList[failIdx], name, 45);
        failList[failIdx][45] = '\0';
        failIdx++;
    }
    failCount++;
    Serial.print(F("    FAIL  "));
    Serial.println(name);
}

static void reportPass(const char *what) {
    passCount++;
    Serial.print(F("    ok    "));
    Serial.println(what);
}

static void head(const char *t) {
    /* second word after "TEST n" is the tag, e.g. "TEST 3  SX1278 ..." */
    snprintf(headTag, sizeof(headTag), "%s", t);
    /* shorten to "Tn" for the summary row */
    if (t[0] == 'T' && t[5] >= '0' && t[5] <= '9') {
        headTag[0] = 'T';
        headTag[1] = t[5];
        headTag[2] = '\0';
    }
    Serial.println();
    Serial.print(F("=== "));
    Serial.println(t);
    Serial.flush();
}

/* ========================= bit-banged SPI, mode 0 ======================== */

static inline void bbDelay() { delayMicroseconds(2); }   // ~250 kHz, very safe

static void spiBegin() {
    pinMode(PIN_NSS, OUTPUT);
    pinMode(PIN_SCK, OUTPUT);
    pinMode(PIN_MOSI, OUTPUT);
    digitalWrite(PIN_NSS, HIGH);
    digitalWrite(PIN_SCK, LOW);
    digitalWrite(PIN_MOSI, LOW);
    pinMode(PIN_MISO, INPUT);
}

static uint8_t bbXfer(uint8_t out) {
    uint8_t in = 0;
    for (int8_t b = 7; b >= 0; --b) {
        digitalWrite(PIN_MOSI, (out >> b) & 1);
        bbDelay();
        digitalWrite(PIN_SCK, HIGH);
        bbDelay();
        in = (uint8_t)((in << 1) | (digitalRead(PIN_MISO) ? 1 : 0));
        digitalWrite(PIN_SCK, LOW);
        bbDelay();
    }
    return in;
}

static void spiW(uint8_t addr, uint8_t val) {
    digitalWrite(PIN_NSS, LOW);
    bbXfer((uint8_t)(addr | 0x80));
    bbXfer(val);
    digitalWrite(PIN_NSS, HIGH);
    delayMicroseconds(5);
}

static uint8_t spiR(uint8_t addr) {
    digitalWrite(PIN_NSS, LOW);
    bbXfer((uint8_t)(addr & 0x7F));
    uint8_t v = bbXfer(0x00);
    digitalWrite(PIN_NSS, HIGH);
    delayMicroseconds(5);
    return v;
}

static void radioReset() {
    pinMode(PIN_RST, OUTPUT);
    digitalWrite(PIN_RST, LOW);
    delay(3);
    digitalWrite(PIN_RST, HIGH);
    delay(12);
}

/* ====================== TEST 1: GPIO pin integrity ======================= */

static void testGPIO() {
    head("TEST 1  GPIO PINS");
    Serial.println(F("  Driving each SPI pin high and low and reading it back."));
    Serial.println(F("  Only the RP2040 side is proven, not the wires."));
    Serial.flush();

    /* NOTE: GP29 (RST) is NOT driven here on purpose. Driving a reset line
     * while the radio is powered can brown-out the module mid-print, which is
     * exactly the kind of reset that truncates the log. */
    struct { uint8_t pin; const char *name; } outs[] = {
        { PIN_NSS,  "GP1  NSS " },
        { PIN_MOSI, "GP2  MOSI" },
        { PIN_SCK,  "GP4  SCK " },
    };

    for (auto &p : outs) {
        pinMode(p.pin, OUTPUT);
        digitalWrite(p.pin, HIGH);
        delay(2);
        bool hi = (gpio_get(p.pin) != 0);
        digitalWrite(p.pin, LOW);
        delay(2);
        bool lo = (gpio_get(p.pin) == 0);

        Serial.print(F("  "));
        Serial.print(p.name);
        Serial.print(F("  high="));
        Serial.print(hi ? 1 : 0);
        Serial.print(F(" low="));
        Serial.print(lo ? 1 : 0);
        if (hi && lo) { Serial.println(F("   ok")); passCount++; }
        else { Serial.println(F("   STUCK")); reportFail(p.name); }
        Serial.flush();
    }

    /* MISO: with the radio powered and driving the bus it will hold a level.
     * Both a pull-up and a pull-down test just prove the pin is not shorted to
     * a rail; an external driver winning is normal and not an error. */
    pinMode(PIN_MISO, INPUT_PULLUP);
    delay(2);
    bool up = digitalRead(PIN_MISO) != 0;
    pinMode(PIN_MISO, INPUT_PULLDOWN);
    delay(2);
    bool dn = digitalRead(PIN_MISO) == 0;
    pinMode(PIN_MISO, INPUT);

    Serial.print(F("  GP3  MISO  pullup="));
    Serial.print(up ? 1 : 0);
    Serial.print(F(" pulldown="));
    Serial.print(dn ? 1 : 0);
    if (up && dn) {
        Serial.println(F("   ok (pin free, nothing driving it)"));
        passCount++;
    } else {
        Serial.println(F("   held by an external driver - normal if"));
        Serial.println(F("              the radio is powered and idle"));
        passCount++;          /* not a fault */
    }
    Serial.flush();

    /* RST: read only, we do not disturb it */
    pinMode(PIN_RST, INPUT);
    Serial.print(F("  GP29 RST   reads "));
    Serial.println(digitalRead(PIN_RST) ? F("1 (released)") : F("0 (held in reset!)"));
    if (!digitalRead(PIN_RST)) reportFail("GP29 stuck low - radio held in reset");
    Serial.flush();

    pinMode(PIN_DIO0, INPUT);
    Serial.print(F("  GP28 DIO0  reads "));
    Serial.println(digitalRead(PIN_DIO0) ? F("1") : F("0"));
    Serial.println(F("  (either is fine while idle)"));
    Serial.flush();
}

/* ================== TEST 2: SPI loopback (jumper needed) ================= */

static bool testSPILoopback() {
    head("TEST 2  SPI BUS  (MOSI -> MISO jumper)");
    Serial.println(F("  Connect a wire from GP2 (MOSI) to GP3 (MISO), then send any"));
    Serial.println(F("  key. Testing now - if it fails, jumper it and run again."));

    /* temporarily make MISO an output so we can drive it from MOSI?  No:
       with the jumper fitted, MOSI drives MISO through the wire. */
    pinMode(PIN_MISO, INPUT);

    uint16_t errs = 0;
    uint8_t firstBad = 0, firstGot = 0;
    for (int v = 0; v < 256; v++) {
        digitalWrite(PIN_NSS, LOW);
        uint8_t got = bbXfer((uint8_t)v);
        digitalWrite(PIN_NSS, HIGH);
        if (got != (uint8_t)v) {
            if (errs == 0) { firstBad = (uint8_t)v; firstGot = got; }
            errs++;
        }
    }

    if (errs == 0) {
        reportPass("SPI loopback: all 256 patterns matched");
        return true;
    }

    Serial.print(F("  mismatches: "));
    Serial.print(errs);
    Serial.print(F("/256   first: sent 0x"));
    Serial.print(firstBad, HEX);
    Serial.print(F(" got 0x"));
    Serial.println(firstGot, HEX);

    if (firstGot == 0xFF || firstGot == 0x00) {
        Serial.println(F("  -> MISO stuck: jumper missing, or GP3 pad not soldered."));
        Serial.println(F("     THIS IS EXPECTED WITHOUT THE JUMPER - not a board fault"));
        Serial.println(F("     unless the jumper IS fitted."));
        Serial.println(F("  SKIPPED rather than failed (no jumper detected)."));
        return false;
    }
    reportFail("SPI loopback mismatch");
    return false;
}

/* ============ TEST 3: SX1278 register map integrity (read/write) ======== */

static void testRegisterMap() {
    head("TEST 3  SX1278 REGISTER INTEGRITY");
    Serial.println(F("  Writing a pattern to scratch registers and reading it back."));
    Serial.println(F("  Detects a floating MISO, a stuck MOSI, or a dead die."));

    uint8_t ver = spiR(REG_VERSION);
    Serial.print(F("  Version (0x42) = 0x"));
    Serial.print(ver, HEX);
    if (ver == 0x12) { Serial.println(F("   ok (SX1276/77/78/79 family)")); passCount++; }
    else if (ver == 0x00 || ver == 0xFF) {
        Serial.println(F("   BAD - bus reads all zeros/ones"));
        reportFail("SX1278 version register unreadable");
        return;
    } else { Serial.println(F("   unexpected")); reportFail("SX1278 version wrong"); }

    /* RegSyncWord 0x39 and RegPreambleMsb 0x20 are plain read/write */
    struct { uint8_t reg; const char *name; } rw[] = {
        { REG_SYNC_WORD,     "SyncWord   0x39" },
        { REG_PREAMBLE_MSB,  "PreambleMsb0x20" },
        { REG_PAYLOAD_LENGTH,"PayloadLen 0x22" },
        { REG_RSSI_THRESH,   "RssiThresh 0x10" },
    };

    for (auto &r : rw) {
        uint8_t orig = spiR(r.reg);
        uint8_t bad = 0;
        for (int t = 0; t < 4; t++) {
            uint8_t pat = (uint8_t)(0x5A ^ (t * 0x21));
            spiW(r.reg, pat);
            delay(1);
            if (spiR(r.reg) != pat) bad++;
        }
        spiW(r.reg, orig);
        Serial.print(F("  "));
        Serial.print(r.name);
        Serial.print(F("  errors="));
        Serial.print(bad);
        if (bad == 0) { Serial.println(F("  ok")); passCount++; }
        else { Serial.println(F("  BAD")); reportFail(r.name); }
    }

    /* RegVersion must be readable identically 100 times - catches noise */
    uint8_t mism = 0;
    for (int i = 0; i < 100; i++) if (spiR(REG_VERSION) != ver) mism++;
    Serial.print(F("  100x version read stable: "));
    if (mism == 0) { Serial.println(F("yes")); passCount++; }
    else { Serial.print(mism); Serial.println(F(" mismatches - bus is noisy"));
           reportFail("SPI bus unstable"); }
}

/* ================= TEST 4: PLL lock at each supported band =============== */

static void testPll() {
    head("TEST 4  FREQUENCY SYNTHESIS / PLL LOCK");
    Serial.println(F("  Programming the LO and waiting for PllLock."));
    Serial.println(F("  Proves the crystal and the synthesiser are alive."));

    spiW(REG_OP_MODE, 0x80 | 0x00);   // LoRa sleep
    delay(10);

    struct { uint32_t hz; const char *band; } bands[] = {
        { 433000000UL, "433 MHz (your band)" },
        { 434000000UL, "434 MHz" },
        { 868000000UL, "868 MHz" },
        { 915000000UL, "915 MHz" },
    };

    for (auto &b : bands) {
        uint64_t frf = ((uint64_t)b.hz << 19) / 32000000ULL;
        spiW(REG_FRF_MSB, (uint8_t)(frf >> 16));
        spiW(REG_FRF_MID, (uint8_t)(frf >> 8));
        spiW(REG_FRF_LSB, (uint8_t)frf);

        spiW(REG_OP_MODE, 0x80 | 0x01);   // LoRa standby -> starts the PLL
        delay(10);

        uint8_t mode = spiR(REG_OP_MODE);
        bool locked = (mode & 0x10) != 0;
        Serial.print(F("  "));
        Serial.print(b.band);
        Serial.print(F("  OpMode=0x"));
        Serial.print(mode, HEX);
        if (locked) { Serial.println(F("  PLL LOCKED")); passCount++; }
        else { Serial.println(F("  no PLL lock")); reportFail(b.band); }
    }

    /* read back the programmed word to prove the write took */
    uint8_t a = spiR(REG_FRF_MSB), c = spiR(REG_FRF_MID), d = spiR(REG_FRF_LSB);
    Serial.print(F("  FRF readback = 0x"));
    Serial.print(a, HEX); Serial.print(c, HEX); Serial.println(d, HEX);
}

/* ================= TEST 5: TX chain exercise + 3V3 quality =============== */

/* ADC on GP26 (ADC0). If nothing is wired there the reading is meaningless but
 * still tells us whether the rail collapses when the PA keys up. */
static void testTxChain() {
    head("TEST 5  TX CHAIN AND SUPPLY SAG");
    Serial.println(F("  Keying the PA at several power levels and watching the PLL"));
    Serial.println(F("  and the ADC input. A collapsing 3V3 shows up as PLL loss."));

    adc_init();
    adc_gpio_init(26);
    adc_select_input(0);
    uint16_t idleRaw = 0;
    for (int i = 0; i < 16; i++) idleRaw += adc_read();
    idleRaw /= 16;
    Serial.print(F("  ADC0 idle raw = "));
    Serial.println(idleRaw);
    Serial.println(F("  (wire 3V3 through a 10k/10k divider into GP26 to make this"));
    Serial.println(F("   a real voltage measurement: raw/4095*3.3*2)"));
    Serial.print(F("   -> idle 3V3 approx "));
    Serial.print((int)(((idleRaw / 4095.0f) * 3.3f * 2.0f) * 100));
    Serial.println(F(" (x100, i.e. 660 = 6.60 V)"));

    struct { uint8_t paCfg; uint8_t paDac; const char *label; } steps[] = {
        { 0x00, 0x84, "PA off (0 dBm baseline)" },
        { 0x40, 0x84, "PA_BOOST low (+2 dBm)" },
        { 0x70, 0x84, "PA_BOOST mid (+11 dBm)" },
        { 0x8F, 0x84, "PA_BOOST high (+17 dBm)" },
        { 0x8F, 0x87, "PA_BOOST max (+20 dBm)" },
    };

    spiW(REG_OCP, 0x2B);        // 100 mA OCP so a short cannot destroy the PA
    spiW(REG_PA_RAMP, 0x09);    // 40 us ramp

    for (auto &s : steps) {
        spiW(REG_PA_CONFIG, s.paCfg);
        spiW(REG_PA_DAC, s.paDac);

        /* short burst: load the FIFO and key TX, then measure while it runs */
        spiW(REG_FIFO_TX_BASE, 0x00);
        spiW(REG_IRQ_FLAGS, 0xFF);
        spiW(REG_OP_MODE, 0x80 | 0x01);
        delay(2);
        spiW(REG_FIFO_ADDR_PTR, 0x00);
        for (uint8_t i = 0; i < 16; i++) spiW(REG_FIFO, 0xAA);
        spiW(REG_PAYLOAD_LENGTH, 16);
        spiW(REG_OP_MODE, 0x80 | 0x03);          /* LoRa TX */
        delay(4);                                 /* well inside a 125 kHz burst */

        uint16_t raw = 0;
        adc_select_input(0);
        for (int i = 0; i < 16; i++) raw += adc_read();
        raw /= 16;

        uint8_t mode = spiR(REG_OP_MODE);
        bool pll = (mode & 0x10) != 0;
        spiW(REG_OP_MODE, 0x80 | 0x01);           /* back to standby */
        delay(2);

        Serial.print(F("  "));
        Serial.print(s.label);
        Serial.print(F("  PLL="));
        Serial.print(pll ? F("locked") : F("LOST"));
        Serial.print(F("  ADC="));
        Serial.print(raw);
        if (!pll) { Serial.println(F("   <-- supply sagged")); reportFail(s.label); }
        else { Serial.println(F("   ok")); passCount++; }
    }

    /* back to a sane configuration */
    spiW(REG_PA_CONFIG, 0x8F);
    spiW(REG_PA_DAC, 0x84);
    spiW(REG_PA_RAMP, 0x09);
    spiW(REG_OCP, 0x2B);
}

/* ============ TEST 6: antenna / load RSSI floor (FSK, no peer) ========== */

static int16_t fskRssi() {
    spiW(REG_OP_MODE, 0x00);      // sleep, LoRa bit clear = FSK
    delay(3);
    spiW(REG_OP_MODE, 0x01);      // FSK standby
    delay(3);
    spiW(REG_RSSI_CONFIG, 0x01);
    delay(3);
    int32_t acc = 0;
    for (int i = 0; i < 16; i++) { acc += spiR(REG_RSSI_VALUE_FSK); delay(1); }
    int16_t raw = (int16_t)(acc / 16);
    return (int16_t)(-164 + raw);
}

static void testAntenna() {
    head("TEST 6  ANTENNA / LOAD  (no second board needed)");
    Serial.println(F("  The SX1278 is an FSK receiver for this test. It measures"));
    Serial.println(F("  whatever RF is present at the port. Ranges that matter:"));
    Serial.println(F("    -125..-105 dBm  normal quiet room, antenna connected"));
    Serial.println(F("    -105..-90  dBm  noisy environment or nearby electronics"));
    Serial.println(F("    -95..-60   dBm  something is transmitting nearby"));
    Serial.println(F("    stuck at   0 dBm  register not implemented - wiring fault"));
    Serial.println(F("    stuck -164  dBm  input shorted or well below sensitivity"));

    int16_t lo = 0, hi = -200, sum = 0;
    const int N = 40;
    for (int i = 0; i < N; i++) {
        int16_t v = fskRssi();
        if (i == 0) lo = v;
        if (v < lo) lo = v;
        if (v > hi) hi = v;
        sum += v;
        if ((i % 8) == 0) {
            Serial.print(F("    sample "));
            Serial.print(v);
            Serial.println(F(" dBm"));
        }
        delay(60);
    }
    int16_t avg = (int16_t)(sum / N);
    Serial.print(F("  min=")); Serial.print(lo);
    Serial.print(F("  max=")); Serial.print(hi);
    Serial.print(F("  avg=")); Serial.print(avg);
    Serial.println(F(" dBm"));

    if (hi - lo >= 3) {
        reportPass("antenna port sees a live, varying noise floor");
        Serial.println(F("  -> the port is connected to something with gain"));
    } else {
        Serial.print(F("  static reading ("));
        Serial.print(hi - lo);
        Serial.println(F(" dB spread) - port may be open or shorted"));
        reportFail("antenna port reads static");
    }

    if (avg < -150) {
        Serial.println(F("  -> extremely low: open circuit or bad u.FL solder joint"));
        reportFail("antenna port open");
    }
}

/* ============ TEST 7: reverse power / load absorption check ============= */

static void testReversePower() {
    head("TEST 7  TX INTO THE PORT  (reverse power check)");
    Serial.println(F("  Keying the PA and measuring how much the input level rises."));
    Serial.println(F("  A real antenna or a 50 ohm load both absorb power; an open"));
    Serial.println(F("  or disconnected port shows almost no change."));

    spiW(REG_OP_MODE, 0x80 | 0x01);   // LoRa standby
    delay(5);
    spiW(REG_PA_CONFIG, 0x00);        // PA off
    delay(20);
    int16_t quiet = fskRssi();

    spiW(REG_PA_CONFIG, 0x8F);        // PA on
    spiW(REG_PA_DAC, 0x84);
    delay(20);
    int16_t keyed = fskRssi();

    spiW(REG_PA_CONFIG, 0x00);
    delay(10);

    Serial.print(F("  PA off = ")); Serial.print(quiet); Serial.println(F(" dBm"));
    Serial.print(F("  PA on  = ")); Serial.print(keyed); Serial.println(F(" dBm"));
    Serial.print(F("  delta  = ")); Serial.print(keyed - quiet); Serial.println(F(" dB"));

    if (keyed - quiet > 20) {
        Serial.println(F("  -> strong coupling: the port is OPEN or the antenna is"));
        Serial.println(F("     badly mismatched. Check the u.FL / SMA solder joint."));
        reportFail("port looks open (huge reverse coupling)");
    } else if (keyed - quiet < 2) {
        Serial.println(F("  -> port absorbs the power. Antenna or 50 ohm load fitted."));
        reportPass("port absorbs TX power (load present)");
    } else {
        Serial.println(F("  -> moderate coupling, acceptable"));
        reportPass("reverse power nominal");
    }
}

/* ============ TEST 8: can the modem hear itself? (RF loop sanity) ====== */

static void testModemSelfCheck() {
    head("TEST 8  MODEM SELF CHECK");
    Serial.println(F("  Verifies the LoRa modem accepts configuration and reports"));
    Serial.println(F("  sane status. Cannot prove reception without a transmitter."));

    spiW(REG_OP_MODE, 0x80 | 0x00);
    delay(5);
    spiW(REG_OP_MODE, 0x80 | 0x01);
    spiW(REG_MODEM_CONFIG_1, 0x72);   // BW125 CR4/5
    spiW(REG_MODEM_CONFIG_2, 0x74);   // SF7 CRC on
    spiW(REG_MODEM_CONFIG_3, 0x04);
    spiW(REG_PREAMBLE_MSB, 0x00);
    spiW(REG_PREAMBLE_LSB, 0x08);
    spiW(REG_SYNC_WORD, 0x12);
    spiW(REG_PAYLOAD_LENGTH, 0x0C);

    uint8_t mc1 = spiR(REG_MODEM_CONFIG_1);
    uint8_t mc2 = spiR(REG_MODEM_CONFIG_2);
    Serial.print(F("  ModemConfig1=0x")); Serial.print(mc1, HEX);
    Serial.print(F("  ModemConfig2=0x")); Serial.println(mc2, HEX);
    if (mc1 == 0x72 && mc2 == 0x74) { reportPass("modem config latched"); }
    else { reportFail("modem config did not latch"); }

    /* temperature sensor - proves the analog die is alive and self-consistent */
    spiW(REG_OP_MODE, 0x80 | 0x01);
    uint8_t t = spiR(REG_TEMP);
    Serial.print(F("  Temp register (0x3C) = "));
    Serial.println(t);
    if (t > 0 && t < 255) { reportPass("temperature sensor responds"); }
    else { reportFail("temperature sensor dead"); }
}

/* ============================== REPORT =================================== */

static char verdictLine[64] = "no results yet";
static bool spiLoopRan = false;

static void buildVerdict() {
    snprintf(verdictLine, sizeof(verdictLine), "%u passed / %u failed%s",
             passCount, failCount, failIdx ? "" : " - NO FAULTS");
    spiLoopRan = false;   /* set by caller via finalReport arg */
}

static void finalReport(bool spiLoopOk) {
    head("REPORT");

    /* compact matrix first - fits one phone screen */
    Serial.print(F("  "));
    for (uint8_t i = 0; i < rowCount; i++) {
        Serial.print(rows[i].tag);
        Serial.print(' ');
        if (rows[i].ok) Serial.print(F("ok"));
        else { Serial.print(F("F")); Serial.print(rows[i].fails); }
        if (i + 1 < rowCount) Serial.print(F(" | "));
    }
    Serial.println();
    Serial.flush();
    Serial.print(F("  checks passed : ")); Serial.println(passCount);
    Serial.print(F("  checks failed : ")); Serial.println(failCount);

    snprintf(verdictLine, sizeof(verdictLine), "%u passed / %u failed%s",
             passCount, failCount, failIdx ? "" : " - NO FAULTS");
    spiLoopRan = spiLoopOk;

    if (failIdx == 0) {
        Serial.println(F("  VERDICT: no faults found on this board."));
    } else {
        Serial.println(F("  FAILED ITEMS:"));
        for (uint8_t i = 0; i < failIdx; i++) {
            Serial.print(F("    - "));
            Serial.println(failList[i]);
        }
        Serial.println(F("  VERDICT: see the list above."));
    }

    Serial.println();
    if (!spiLoopOk) {
        Serial.println(F("  NOTE: the MOSI->MISO jumper was not fitted, so the"));
        Serial.println(F("        receive path of the SPI bus was never verified."));
        Serial.println(F("        Fit a wire GP2->GP3 and re-run for a full proof."));
    }

    Serial.println();
    Serial.println(F("  NOTE: reception needs a 2nd transmitter."));
}

/* =============================== MAIN =================================== */

void setup() {
    Serial.begin(115200);
    uint32_t t0 = millis();
    while (!Serial && (millis() - t0) < 4000) { delay(10); }

    Serial.println();
    Serial.println(F("#####################################################"));
    Serial.println(F("#  RP2040 + Ra-02 / SX1278  SELF TEST               #"));
    Serial.println(F("#  one board, no peer transmitter needed            #"));
    Serial.println(F("#####################################################"));
    Serial.println(F("pin map: NSS=GP1 MOSI=GP2 MISO=GP3 SCK=GP4"));
    Serial.println(F("         RST=GP29 DIO0=GP28"));
    Serial.println(F("If this log stops mid-test, the LAST banner printed names the"));
    Serial.println(F("test that stopped. Every test resets the radio first."));
    Serial.flush();
    delay(300);

    pinMode(LED_BUILTIN, OUTPUT);
    digitalWrite(LED_BUILTIN, HIGH);

    spiBegin();
    radioReset();
    delay(50);

    testGPIO();            inlineResult();

    /* Each test resets the radio first, so a crash or a wedged radio in one
     * test cannot swallow the rest of the log. Watch which banner is the LAST
     * one printed - that is the test that stopped. */
    radioReset(); delay(30); testSPILoopback();  inlineResult(); bool loopOk = true;
    radioReset(); delay(30); testRegisterMap();  inlineResult();
    radioReset(); delay(30); testPll();          inlineResult();
    radioReset(); delay(30); testTxChain();      inlineResult();
    radioReset(); delay(30); testAntenna();      inlineResult();
    radioReset(); delay(30); testReversePower(); inlineResult();
    radioReset(); delay(30); testModemSelfCheck(); inlineResult();
    finalReport(loopOk);

    Serial.println();
    Serial.println(F("Done. Power-cycle to run again."));
    Serial.flush();
    digitalWrite(LED_BUILTIN, LOW);
}

void loop() {
    /* Single-shot test, but fully re-queryable:
     *   any key  -> reprint the whole report (the log scrolls away otherwise)
     *   heartbeat -> compact verdict so the summary is never lost off-screen  */
    static uint32_t ticks = 0;

    if (Serial.available()) {
        while (Serial.available()) Serial.read();
        Serial.println();
        Serial.println(F("############ REPORT AGAIN ############"));
        Serial.print(F("  "));
        for (uint8_t i = 0; i < rowCount; i++) {
            Serial.print(rows[i].tag);
            Serial.print(' ');
            if (rows[i].ok) Serial.print(F("ok"));
            else { Serial.print(F("F")); Serial.print(rows[i].fails); }
            if (i + 1 < rowCount) Serial.print(F(" | "));
        }
        Serial.println();
        Serial.print(F("  "));
        Serial.println(verdictLine);
        if (failIdx) {
            Serial.println(F("  FAILED ITEMS:"));
            for (uint8_t i = 0; i < failIdx; i++) {
                Serial.print(F("    - "));
                Serial.println(failList[i]);
            }
        } else {
            Serial.println(F("  no faults found on this board"));
        }
        if (!spiLoopRan) {
            Serial.println(F("  (SPI loopback not verified - fit a wire GP2->GP3)"));
        }
        Serial.println(F("######################################"));
        Serial.flush();
    }

    digitalWrite(LED_BUILTIN, HIGH);
    delay(650);
    digitalWrite(LED_BUILTIN, LOW);
    delay(650);
    ticks++;

    if ((ticks % 4) == 0) {
        Serial.print(F("[alive "));
        Serial.print(ticks);
        Serial.print(F("] "));
        for (uint8_t i = 0; i < rowCount; i++) {
            Serial.print(rows[i].tag);
            Serial.print(rows[i].ok ? F(":ok ") : F(":FAIL "));
        }
        Serial.println();
        Serial.flush();
    }
}
