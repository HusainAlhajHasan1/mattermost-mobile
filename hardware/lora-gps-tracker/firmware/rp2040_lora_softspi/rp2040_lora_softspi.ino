/* -----------------------------------------------------------------------------
 *  rp2040_lora_softspi.ino  --  Ra-02 / SX1278 link tester for RP2040 USB boards
 *
 *  USE THIS SKETCH IF YOU DO NOT WANT TO MOVE ANY WIRES.
 *
 *  It talks to the radio over a bit-banged SPI bus, so it works with the exact
 *  pin assignment from the wiring table:
 *
 *      Ra-02 pin   ->  RP2040
 *      ----------      ------
 *      GND             GND
 *      3V3             3V3   (3.3 V ONLY - never 5 V)
 *      RESET           GP29
 *      DIO0            GP28
 *      NSS             GP1
 *      MOSI            GP2
 *      MISO            GP3
 *      SCK             GP4
 *
 *  Why: on the RP2040 the hardware SPI0 block is hard-wired to
 *  SCK=GP2, TX(MOSI)=GP3, RX(MISO)=GP4, CSn=GP1. The table above has the three
 *  data lines rotated by one position, so arduino-pico's hardware SPI cannot be
 *  used with it. Bit-banging ignores the alternate-function mux entirely, so this
 *  sketch works as wired. If you *can* move the three data wires (MOSI->GP3,
 *  MISO->GP4, SCK->GP2) use rp2040_lora_hwspi.ino instead - it is faster and
 *  uses the proven LoRa library.
 *
 *  Board:  Raspberry Pi Pico (or any RP2040 board) via the
 *          "Raspberry Pi Pico/RP2040" core by Earle Philhower.
 *
 *  Serial monitor: 115200 baud. Commands:
 *      r        receive mode (default at boot)
 *      t        send one test packet
 *      a        auto-beacon every BEACON_INTERVAL_MS
 *      s        stop auto-beacon / go idle
 *      m<text>  send <text> as the payload, e.g.  mhello world
 *      i        print radio status (mode, RSSI of last packet, counters)
 *      h        help
 *
 *  Bench test: flash two boards (different NODE_ID), put both in receive mode,
 *  press 't' on one. Never key the transmitter without an antenna attached.
 * -------------------------------------------------------------------------- */

#include <Arduino.h>
#include <stdio.h>
#include <string.h>

/* =============================== CONFIG ================================== */

#define NODE_ID                 1          // give each board its own id
#define FREQ_HZ                 433000000UL
#define SPREADING_FACTOR        9          // 7..12 (9 = good range/speed mix)
#define CODING_RATE_4_5         1          // 1 = 4/5, 2 = 4/6, 3 = 4/7, 4 = 4/8
#define TX_POWER_DBM            17         // +17 dBm max for the Ra-02
#define SYNC_WORD               0x12       // private network sync word
#define BEACON_INTERVAL_MS      5000UL
#define MAX_PAYLOAD             64

/* ------------------------------- pin map --------------------------------- */

#define PIN_NSS                 1
#define PIN_MOSI                2
#define PIN_MISO                3
#define PIN_SCK                 4
#define PIN_RST                 29
#define PIN_DIO0                28

/* ========================= bit-banged SPI, mode 0 ======================== */
/* MSB first, data sampled on the rising SCK edge, ~330 kHz (SX1278 max is
 * 10 MHz so there is plenty of margin). */

static inline void nssLow()  { digitalWrite(PIN_NSS, LOW);  }
static inline void nssHigh() { digitalWrite(PIN_NSS, HIGH); }

static uint8_t bbTransfer(uint8_t out) {
    uint8_t in = 0;
    for (int8_t bit = 7; bit >= 0; --bit) {
        digitalWrite(PIN_MOSI, (out >> bit) & 0x01);
        delayMicroseconds(1);
        digitalWrite(PIN_SCK, HIGH);
        delayMicroseconds(1);
        in = (uint8_t)((in << 1) | (digitalRead(PIN_MISO) ? 1 : 0));
        digitalWrite(PIN_SCK, LOW);
        delayMicroseconds(1);
    }
    return in;
}

static void spiWrite(uint8_t addr, uint8_t value) {
    nssLow();
    bbTransfer((uint8_t)(addr | 0x80));
    bbTransfer(value);
    nssHigh();
}

static uint8_t spiRead(uint8_t addr) {
    nssLow();
    bbTransfer((uint8_t)(addr & 0x7F));
    uint8_t v = bbTransfer(0x00);
    nssHigh();
    return v;
}

/* ========================== SX1278 register map ========================== */

#define REG_FIFO                 0x00
#define REG_OP_MODE              0x01
#define REG_FR_MSB               0x06
#define REG_FR_MID               0x07
#define REG_FR_LSB               0x08
#define REG_PA_CONFIG            0x09
#define REG_PA_RAMP              0x0A
#define REG_OCP                  0x0B
#define REG_LNA                  0x0C
#define REG_RSSI_CONFIG          0x0E   // alias: FIFO_TX_BASE in normal mode
#define REG_RSSI_VALUE           0x11
#define REG_FIFO_ADDR_PTR        0x0D
#define REG_FIFO_TX_BASE         0x0E
#define REG_FIFO_RX_BASE         0x0F
#define REG_FIFO_RX_CURRENT      0x10
#define REG_IRQ_FLAGS            0x12
#define REG_RX_NB_BYTES          0x13
#define REG_PKT_SNR              0x19
#define REG_PKT_RSSI             0x1A
#define REG_MODEM_CONFIG_1       0x1D
#define REG_MODEM_CONFIG_2       0x1E
#define REG_PREAMBLE_MSB         0x20
#define REG_PREAMBLE_LSB         0x21
#define REG_PAYLOAD_LENGTH       0x22
#define REG_MODEM_CONFIG_3       0x26
#define REG_DETECT_OPTIMIZE      0x31
#define REG_DETECT_THRESHOLD     0x37
#define REG_SYNC_WORD            0x39
#define REG_DIO_MAPPING_1        0x40
#define REG_VERSION              0x42
#define REG_PA_DAC               0x4D

#define MODE_LONG_RANGE          0x80
#define MODE_SLEEP               0x00
#define MODE_STDBY               0x01
#define MODE_TX                  0x03
#define MODE_RX_CONT             0x05

#define IRQ_RX_TIMEOUT           0x80
#define IRQ_RX_DONE              0x40
#define IRQ_CRC_ERROR            0x20
#define IRQ_TX_DONE              0x08

/* 433 MHz sits on the SX1276 low-frequency port, so the packet-RSSI offset
 * is -164 dBm (-157 is the HF / 868-915 MHz constant). */
#define RSSI_OFFSET              (-164)

/* ============================ radio driver =============================== */

static int16_t  lastRssi = 0;
static int8_t   lastSnr  = 0;

static void waitReady() { delay(2); }

static void radioReset() {
    pinMode(PIN_RST, OUTPUT);
    digitalWrite(PIN_RST, LOW);
    delay(2);
    digitalWrite(PIN_RST, HIGH);
    delay(10);                       // datasheet: 5 ms after reset
}

static void radioSleep() {
    spiWrite(REG_OP_MODE, MODE_LONG_RANGE | MODE_SLEEP);
    waitReady();
}

static void radioStandby() {
    spiWrite(REG_OP_MODE, MODE_LONG_RANGE | MODE_STDBY);
    waitReady();
}

static void radioSetFrequency(uint32_t hz) {
    uint64_t frf = ((uint64_t)hz << 19) / 32000000ULL;
    spiWrite(REG_FR_MSB, (uint8_t)(frf >> 16));
    spiWrite(REG_FR_MID, (uint8_t)(frf >> 8));
    spiWrite(REG_FR_LSB, (uint8_t)(frf));
}

/* ModemConfig1: BW bits 7..4 = 0111 (125 kHz), CR bits 3..1, implicit-header
 * bit 0 = 0 (explicit header). 0x72 with CR 4/5. */
static uint8_t modemConfig1() {
    return (uint8_t)((0x07 << 4) | ((CODING_RATE_4_5 & 0x07) << 1));
}

/* Returns true if the radio answered with the expected version byte. */
static bool radioBegin() {
    pinMode(PIN_NSS, OUTPUT);
    pinMode(PIN_SCK, OUTPUT);
    pinMode(PIN_MOSI, OUTPUT);
    pinMode(PIN_MISO, INPUT);        // module drives MISO
    pinMode(PIN_DIO0, INPUT);
    digitalWrite(PIN_NSS, HIGH);
    digitalWrite(PIN_SCK, LOW);

    radioReset();

    uint8_t version = spiRead(REG_VERSION);
    Serial.print(F("SX127x version register: 0x"));
    Serial.println(version, HEX);
    if (version != 0x12) {
        Serial.println(F("!! expected 0x12 - check MISO/MOSI/SCK/NSS and 3V3"));
        return false;
    }

    radioSleep();                    // must be in sleep to switch to LoRa mode
    spiWrite(REG_OP_MODE, MODE_LONG_RANGE | MODE_SLEEP);
    delay(10);
    radioStandby();

    radioSetFrequency(FREQ_HZ);

    /* +17 dBm on the PA_BOOST pin (Ra-02 is wired for PA_BOOST). */
    spiWrite(REG_PA_CONFIG, 0xFF);
    spiWrite(REG_PA_DAC, 0x84);
    spiWrite(REG_PA_RAMP, 0x09);     // 40 us ramp
    spiWrite(REG_OCP, 0x2B);         // 100 mA over-current protection
    spiWrite(REG_LNA, 0x23);         // max gain, LNA boost

    spiWrite(REG_FIFO_TX_BASE, 0x00);
    spiWrite(REG_FIFO_RX_BASE, 0x00);
    spiWrite(REG_FIFO_ADDR_PTR, 0x00);

    spiWrite(REG_MODEM_CONFIG_1, modemConfig1());
    spiWrite(REG_MODEM_CONFIG_2,
             (uint8_t)(((SPREADING_FACTOR & 0x0F) << 4) | 0x04));   // CRC on
    spiWrite(REG_MODEM_CONFIG_3, (uint8_t)(SPREADING_FACTOR >= 11 ? 0x0C : 0x04));
    spiWrite(REG_PREAMBLE_MSB, 0x00);
    spiWrite(REG_PREAMBLE_LSB, 0x08);
    spiWrite(REG_SYNC_WORD, SYNC_WORD);
    spiWrite(REG_DETECT_OPTIMIZE, 0xC3);
    spiWrite(REG_DETECT_THRESHOLD, 0x0A);
    spiWrite(REG_DIO_MAPPING_1, 0x00);   // DIO0 = RxDone
    spiWrite(REG_IRQ_FLAGS, 0xFF);       // clear everything

    return true;
}

static bool waitIrq(uint8_t mask, uint32_t timeoutMs) {
    uint32_t t0 = millis();
    while (millis() - t0 < timeoutMs) {
        if (spiRead(REG_IRQ_FLAGS) & mask) return true;
        if (digitalRead(PIN_DIO0)) {                 // early out via DIO0
            if (spiRead(REG_IRQ_FLAGS) & mask) return true;
        }
        delay(1);
    }
    return false;
}

static bool radioSend(const uint8_t *data, uint8_t len) {
    if (len > MAX_PAYLOAD) len = MAX_PAYLOAD;

    radioStandby();
    spiWrite(REG_IRQ_FLAGS, 0xFF);
    spiWrite(REG_FIFO_ADDR_PTR, 0x00);
    spiWrite(REG_FIFO_TX_BASE, 0x00);
    for (uint8_t i = 0; i < len; i++) spiWrite(REG_FIFO, data[i]);
    spiWrite(REG_PAYLOAD_LENGTH, len);

    spiWrite(REG_OP_MODE, MODE_LONG_RANGE | MODE_TX);
    bool ok = waitIrq(IRQ_TX_DONE, 4000);
    spiWrite(REG_IRQ_FLAGS, 0xFF);
    radioStandby();
    return ok;
}

static void radioStartRx() {
    radioStandby();
    spiWrite(REG_IRQ_FLAGS, 0xFF);
    spiWrite(REG_DIO_MAPPING_1, 0x00);   // DIO0 = RxDone
    spiWrite(REG_FIFO_RX_BASE, 0x00);
    spiWrite(REG_FIFO_ADDR_PTR, 0x00);
    spiWrite(REG_OP_MODE, MODE_LONG_RANGE | MODE_RX_CONT);
    waitReady();
}

/* Non-blocking: returns payload length, or 0 if nothing complete yet. */
static uint8_t radioPoll(uint8_t *buf, uint8_t maxLen) {
    uint8_t flags = spiRead(REG_IRQ_FLAGS);
    if (!(flags & IRQ_RX_DONE)) {
        if (flags & IRQ_RX_TIMEOUT) spiWrite(REG_IRQ_FLAGS, 0xFF);
        return 0;
    }
    spiWrite(REG_IRQ_FLAGS, 0xFF);       // writing 1s clears

    if (flags & IRQ_CRC_ERROR) {
        Serial.println(F("[rx] packet with bad CRC - dropped"));
        return 0;
    }

    lastSnr  = (int8_t)spiRead(REG_PKT_SNR);
    lastRssi = (int16_t)(RSSI_OFFSET + spiRead(REG_PKT_RSSI));
    if (lastSnr < 0) lastRssi += lastSnr;      // datasheet packet-RSSI rule

    uint8_t n = spiRead(REG_RX_NB_BYTES);
    if (n > maxLen) n = maxLen;
    spiWrite(REG_FIFO_ADDR_PTR, spiRead(REG_FIFO_RX_CURRENT));
    for (uint8_t i = 0; i < n; i++) buf[i] = spiRead(REG_FIFO);
    return n;
}

/* ============================== application ============================== */

enum AppMode { MODE_RX, MODE_IDLE, MODE_AUTO };
static AppMode  appMode     = MODE_RX;
static uint32_t seqNo       = 0;
static uint32_t lastBeacon  = 0;
static uint32_t rxCount     = 0;
static uint32_t txCount     = 0;
static volatile uint32_t dio0Edges = 0;

static void dio0Isr() { dio0Edges++; }

static void printStatus() {
    Serial.print(F("mode="));
    Serial.print(appMode == MODE_RX ? F("RX") : appMode == MODE_AUTO ? F("AUTO") : F("IDLE"));
    Serial.print(F("  node="));
    Serial.print(NODE_ID);
    Serial.print(F("  freq="));
    Serial.print((uint32_t)(FREQ_HZ / 1000));
    Serial.print(F("kHz  sf="));
    Serial.print(SPREADING_FACTOR);
    Serial.print(F("  tx="));
    Serial.print(txCount);
    Serial.print(F("  rx="));
    Serial.print(rxCount);
    Serial.print(F("  dio0_isr="));
    Serial.print(dio0Edges);
    Serial.print(F("  last_rssi="));
    Serial.print(lastRssi);
    Serial.print(F("dBm  last_snr="));
    Serial.print(lastSnr);
    Serial.println(F("dB"));
}

/* 'd' - dump the registers we care about and do a loopback-free sanity report.
 * This is the fastest way to tell "radio is fine, antenna/reception is not"
 * apart from "radio never configured". */
static void radioDump() {
    radioStandby();
    uint8_t mode  = spiRead(REG_OP_MODE);
    uint8_t fr1   = spiRead(REG_MODEM_CONFIG_1);
    uint8_t fr2   = spiRead(REG_MODEM_CONFIG_2);
    uint8_t pa    = spiRead(REG_PA_CONFIG);
    uint8_t lna   = spiRead(REG_LNA);
    uint8_t sync  = spiRead(REG_SYNC_WORD);
    uint8_t plen  = spiRead(REG_PAYLOAD_LENGTH);

    Serial.println(F("--- radio registers ---"));
    Serial.print(F("  Version(0x42)   = 0x")); Serial.println(spiRead(REG_VERSION), HEX);
    Serial.print(F("  OpMode (0x01)   = 0x")); Serial.println(mode, HEX);
    Serial.print(F("    LoRa mode     : ")); Serial.println((mode & 0x80) ? F("yes") : F("NO <-- problem"));
    Serial.print(F("    modem state   : 0x")); Serial.println((mode & 0x07), HEX);
    Serial.print(F("  ModemCfg1(0x1D) = 0x")); Serial.println(fr1, HEX);
    Serial.print(F("    bandwidth     : "));
    switch ((fr1 >> 4) & 0x0F) {
        case 7:  Serial.println(F("125 kHz")); break;
        case 8:  Serial.println(F("250 kHz")); break;
        case 9:  Serial.println(F("500 kHz")); break;
        default: Serial.print(F("0x")); Serial.println((fr1 >> 4) & 0x0F, HEX); break;
    }
    Serial.print(F("    coding rate   : 4/")); Serial.println(5 + ((fr1 >> 1) & 0x07));
    Serial.print(F("  ModemCfg2(0x1E) = 0x")); Serial.println(fr2, HEX);
    Serial.print(F("    spreading     : SF")); Serial.println((fr2 >> 4) & 0x0F);
    Serial.print(F("    CRC           : ")); Serial.println((fr2 & 0x04) ? F("on") : F("OFF <-- problem"));
    Serial.print(F("  PaConfig(0x09)  = 0x")); Serial.println(pa, HEX);
    Serial.print(F("  Lna(0x0C)       = 0x")); Serial.println(lna, HEX);
    Serial.print(F("  SyncWord(0x39)  = 0x")); Serial.println(sync, HEX);
    Serial.print(F("  PayloadLen(0x22)= ")); Serial.println(plen);
    Serial.print(F("  Freq (0x06..08) = "));
    /* read back the frequency word we programmed */
    uint32_t msb = spiRead(REG_FR_MSB), mid = spiRead(REG_FR_MID), lsb = spiRead(REG_FR_LSB);
    uint32_t frf = (msb << 16) | (mid << 8) | lsb;
    Serial.print((uint32_t)(((uint64_t)frf * 32000000ULL) / (1ULL << 19)));
    Serial.println(F(" Hz"));
    Serial.println(F("  RSSI / SNR only update after a packet is received."));

    /* An RF level that is stuck at 0x00 or 0xFF usually means the antenna port
     * is open or shorted; a live floor moves with the environment. */
    uint8_t rf = spiRead(REG_PKT_RSSI);
    Serial.print(F("  PacketRssi(0x1A)= 0x")); Serial.print(rf, HEX);
    Serial.print(F("  -> ")); Serial.print((int)RSSI_OFFSET + rf); Serial.println(F(" dBm if a packet arrived"));
    Serial.println(F("-----------------------"));
}

/* ---------------------------------------------------------------------------
 * 'n' - live noise-floor monitor. Puts the radio in continuous RX-RSSI mode
 * (datasheet 6.4) and prints the raw RSSI register so you can watch the RF
 * floor move. This needs NO packets and NO matching settings on the other
 * board: if the antenna and front end are alive, the number changes when you
 * bring a transmitter near. Press any key to stop.
 * ------------------------------------------------------------------------- */
static void rssiMonitor() {
    const uint32_t WINDOW_MS = 12000;

    radioStandby();
    spiWrite(REG_OP_MODE, MODE_LONG_RANGE | MODE_SLEEP);
    delay(5);
    /* RxRssiContinuous: low 3 bits = 101, keep LoRa bit */
    spiWrite(REG_OP_MODE, MODE_LONG_RANGE | 0x05);
    /* writes to 0x0E go to RSSI_CONFIG while in this mode */
    spiWrite(REG_RSSI_CONFIG, 0x01);

    Serial.println(F("[scan] continuous RSSI for 12 s - any key stops it"));
    Serial.println(F("       strong signal = -30..-70 dBm | empty band = -100..-125"));

    int16_t best = -200, worst = 0;
    uint32_t t0 = millis();
    while (millis() - t0 < WINDOW_MS) {
        if (Serial.available()) { while (Serial.available()) Serial.read(); break; }

        uint8_t raw = spiRead(REG_RSSI_VALUE);
        int16_t dbm = (int16_t)RSSI_OFFSET + raw;
        if (dbm < worst) worst = dbm;
        if (dbm > best)  best  = dbm;

        Serial.print(F("  rssi="));
        Serial.print(dbm);
        Serial.print(F(" dBm  "));
        int bars = (dbm + 130) / 3;          // -130..-100 -> 0..10 bars
        for (int i = 0; i < 10; i++) Serial.print(i < bars ? '#' : '.');
        Serial.println();
        delay(200);
    }

    Serial.print(F("[scan] best="));
    Serial.print(best);
    Serial.print(F(" dBm  worst="));
    Serial.print(worst);
    Serial.println(F(" dBm"));
    if (best > -85) {
        Serial.println(F("[scan] RF front end is ALIVE (signal detected)"));
    } else {
        Serial.println(F("[scan] flat noise floor - check antenna and that the"));
        Serial.println(F("       other board is actually transmitting"));
    }

    radioStartRx();
    Serial.println(F("[scan] back to receive"));
}

static void help() {
    Serial.println(F("commands: r=receive  t=test-tx  a=auto-beacon  s=stop  "
                     "m<text>=send  i=status  d=radio-dump  n=rf-scan  h=help"));
}

static void beacon() {
    char pkt[MAX_PAYLOAD];
    int n = snprintf(pkt, sizeof(pkt), "TK,%u,%lu,%lu",
                     NODE_ID, (unsigned long)seqNo++, (unsigned long)millis());
    if (n <= 0) return;
    if (radioSend((const uint8_t *)pkt, (uint8_t)n)) {
        txCount++;
        Serial.print(F("[tx] "));
        Serial.println(pkt);
    } else {
        Serial.println(F("[tx] no TxDone - radio did not finish transmit"));
    }
}

void setup() {
    Serial.begin(115200);
    uint32_t t0 = millis();
    while (!Serial && (millis() - t0) < 3000) { delay(10); }

    Serial.println();
    Serial.println(F("=== RP2040 + Ra-02 (SX1278) - bit-banged SPI build ==="));
    Serial.println(F("wiring: NSS=GP1 MOSI=GP2 MISO=GP3 SCK=GP4 RST=GP29 DIO0=GP28"));

    if (!radioBegin()) {
        Serial.println(F("radio init failed - halting. Check the wiring table and 3V3."));
        while (true) { digitalWrite(LED_BUILTIN, !digitalRead(LED_BUILTIN)); delay(200); }
    }

    pinMode(PIN_DIO0, INPUT);
    attachInterrupt(digitalPinToInterrupt(PIN_DIO0), dio0Isr, RISING);

    Serial.print(F("radio ready on "));
    Serial.print((uint32_t)(FREQ_HZ / 1000));
    Serial.println(F(" kHz, listening"));
    Serial.println(F("type t + Enter (or just t) to send a test packet"));
    help();

    radioStartRx();
}

/* Runs one command line. Tolerant of what phone terminal apps actually send. */
static void runCommand(char *line) {
    /* strip leading whitespace */
    while (*line == ' ' || *line == '\t') line++;
    /* strip trailing whitespace incl. a stray \r */
    size_t len = strlen(line);
    while (len > 0 && (line[len - 1] == ' ' || line[len - 1] == '\t' ||
                       line[len - 1] == '\r' || line[len - 1] == '\n')) {
        line[--len] = '\0';
    }
    if (len == 0) return;

    /* phones auto-capitalise the first letter: "T" must work like "t" */
    char op = line[0];
    if (op >= 'A' && op <= 'Z') op = (char)(op - 'A' + 'a');

    switch (op) {
        case 'r': appMode = MODE_RX;   radioStartRx();
                  Serial.println(F("[mode] receive")); break;
        case 't': beacon(); lastBeacon = millis(); break;
        case 'a': appMode = MODE_AUTO; lastBeacon = millis() - BEACON_INTERVAL_MS;
                  Serial.println(F("[mode] auto-beacon")); break;
        case 's': appMode = MODE_IDLE; radioStandby();
                  Serial.println(F("[mode] idle")); break;
        case 'i': printStatus(); break;
        case 'd': radioDump(); break;
        case 'n': rssiMonitor(); break;
        case 'h': help(); break;
        case 'm': {
            const char *msg = line + 1;
            while (*msg == ' ' || *msg == ':') msg++;
            size_t mlen = strlen(msg);
            if (mlen == 0) { Serial.println(F("usage: m<text>")); break; }
            if (mlen > MAX_PAYLOAD) mlen = MAX_PAYLOAD;
            if (radioSend((const uint8_t *)msg, (uint8_t)mlen)) {
                txCount++;
                Serial.print(F("[tx] ")); Serial.println(msg);
            } else {
                Serial.println(F("[tx] failed"));
            }
            if (appMode != MODE_IDLE) radioStartRx();
            break;
        }
        default:
            Serial.print(F("unknown command: "));
            Serial.println(line);
            help();
            break;
    }
}

void loop() {
    /* ---------------------------------------------------- serial commands
     *
     * Phone serial apps are inconsistent about line endings:
     *   "t\r\n"  normal   "t\r"  old-Mac style   "t"  no terminator at all
     * Some send nothing until another key is pressed. So: buffer printable
     * characters and fire the command on EITHER \r or \n (first one wins,
     * the second is swallowed because the buffer is already empty), or
     * after COMMIT_MS of silence which handles the no-terminator case. */
    static char     cmdBuf[80];
    static uint8_t  cmdLen = 0;
    static uint32_t lastCharMs = 0;
    const  uint32_t COMMIT_MS = 250;      // silence that counts as "line finished"

    while (Serial.available()) {
        char c = (char)Serial.read();
        lastCharMs = millis();

        if (c == '\r' || c == '\n') {
            cmdBuf[cmdLen] = '\0';
            runCommand(cmdBuf);
            cmdLen = 0;
        } else if (c == 8 || c == 127) {  // backspace / delete
            if (cmdLen > 0) cmdLen--;
        } else if (c >= 32 && c <= 126) { // printable only, ignore control junk
            if (cmdLen < sizeof(cmdBuf) - 1) {
                cmdBuf[cmdLen++] = c;
            } else {                      // buffer full: run what we have
                cmdBuf[cmdLen] = '\0';
                runCommand(cmdBuf);
                cmdLen = 0;
            }
        }
    }

    /* no line terminator arrived - commit on silence */
    if (cmdLen > 0 && (millis() - lastCharMs) > COMMIT_MS) {
        cmdBuf[cmdLen] = '\0';
        runCommand(cmdBuf);
        cmdLen = 0;
    }

    /* ------------------------------------------------------ auto beaconing */
    if (appMode == MODE_AUTO && millis() - lastBeacon >= BEACON_INTERVAL_MS) {
        lastBeacon = millis();
        beacon();
        radioStartRx();                  // back to listening after the burst
    }

    /* ------------------------------------------------------- receive path */
    if (appMode != MODE_IDLE) {
        uint8_t buf[MAX_PAYLOAD + 1];
        uint8_t n = radioPoll(buf, MAX_PAYLOAD);
        if (n > 0) {
            buf[n] = '\0';
            rxCount++;
            Serial.print(F("[rx] "));
            Serial.print((char *)buf);
            Serial.print(F("   rssi="));
            Serial.print(lastRssi);
            Serial.print(F("dBm snr="));
            Serial.print(lastSnr);
            Serial.println(F("dB"));
            if (appMode == MODE_RX) radioStartRx();   // continuous re-arm
        }
    }

    delay(1);
}
