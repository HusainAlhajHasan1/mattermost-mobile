/* -----------------------------------------------------------------------------
 *  rp2040_lora_hwspi.ino  --  Ra-02 / SX1278 link tester for RP2040 USB boards
 *
 *  HARDWARE SPI BUILD - the recommended one, but it needs THREE WIRES MOVED
 *  relative to the original wiring table, because the RP2040's SPI0 alternate
 *  function is fixed in silicon:
 *
 *      RP2040 SPI0:  SCK = GP2      TX(MOSI) = GP3
 *                    RX(MISO) = GP4  CSn = GP1
 *
 *  Corrected wiring (matches the hardware peripheral):
 *
 *      Ra-02 pin   ->  RP2040   |  original table   |  action
 *      ----------      ------   |  --------------   |  ------
 *      GND             GND      |  GND              |  unchanged
 *      3V3             3V3      |  3V3 (3.3 V ONLY) |  unchanged
 *      RESET           GP29     |  GP29             |  unchanged
 *      DIO0            GP28     |  GP28             |  unchanged
 *      NSS             GP1      |  GP1              |  unchanged
 *      MOSI            GP3      |  GP2  (SCK)       |  MOVE
 *      MISO            GP4      |  GP3  (MOSI)      |  MOVE
 *      SCK             GP2      |  GP4  (MISO)      |  MOVE
 *
 *  If you cannot move the wires, use rp2040_lora_softspi.ino instead - same
 *  functionality, bit-banged bus, works with the table exactly as written.
 *
 *  Required libraries (Library Manager):
 *      "LoRa" by Sandeep Mistry            (search: LoRa)
 *
 *  Board:  Raspberry Pi Pico / any RP2040 board, core
 *          "Raspberry Pi Pico/RP2040" by Earle Philhower.
 *
 *  Serial monitor: 115200 baud. Commands:
 *      r        receive mode (default at boot)
 *      t        send one test packet
 *      a        auto-beacon every BEACON_INTERVAL_MS
 *      s        stop auto-beacon / go idle
 *      m<text>  send <text> as payload, e.g.  mhello world
 *      i        print radio status
 *      h        help
 *
 *  Never key the transmitter without an antenna attached.
 * -------------------------------------------------------------------------- */

#include <Arduino.h>
#include <SPI.h>
#include <LoRa.h>

/* =============================== CONFIG ================================== */

#define NODE_ID                 1
#define FREQ_HZ                 433E6
#define SPREADING_FACTOR        9
x
#define TX_POWER_DBM            17
#define SYNC_WORD               0x12
#define BEACON_INTERVAL_MS      5000UL
#define MAX_PAYLOAD             64

/* -------------------- pin map: corrected for SPI0 ------------------------ */

#define PIN_NSS                 1          // SPI0 CSn
#define PIN_SCK                 2          // SPI0 SCK
#define PIN_MOSI                3          // SPI0 TX
#define PIN_MISO                4          // SPI0 RX
#define PIN_RST                 29
#define PIN_DIO0                28

/* ============================== application ============================== */

enum AppMode { MODE_RX, MODE_IDLE, MODE_AUTO };
static AppMode  appMode    = MODE_RX;
static uint32_t seqNo      = 0;
static uint32_t lastBeacon = 0;
static uint32_t rxCount    = 0;
static uint32_t txCount    = 0;

static void printStatus() {
    Serial.print(F("mode="));
    Serial.print(appMode == MODE_RX ? F("RX") : appMode == MODE_AUTO ? F("AUTO") : F("IDLE"));
    Serial.print(F("  node="));
    Serial.print(NODE_ID);
    Serial.print(F("  freq="));
    Serial.print((long)(FREQ_HZ / 1000.0));
    Serial.print(F("kHz  sf="));
    Serial.print(SPREADING_FACTOR);
    Serial.print(F("  bw="));
    Serial.print((long)(BANDWIDTH / 1000.0));
    Serial.print(F("kHz  tx="));
    Serial.print(txCount);
    Serial.print(F("  rx="));
    Serial.print(rxCount);
    Serial.print(F("  last_rssi="));
    Serial.print(LoRa.packetRssi());
    Serial.print(F("dBm  last_snr="));
    Serial.print(LoRa.packetSnr());
    Serial.println(F("dB"));
}

static void help() {
    Serial.println(F("commands: r=receive  t=test-tx  a=auto-beacon  s=stop  "
                     "m<text>=send  i=status  h=help"));
}

static void listen() {
    LoRa.receive();
}

static void beacon() {
    char pkt[MAX_PAYLOAD];
    int n = snprintf(pkt, sizeof(pkt), "TK,%u,%lu,%lu",
                     NODE_ID, (unsigned long)seqNo++, (unsigned long)millis());
    if (n <= 0) return;

    LoRa.beginPacket();
    LoRa.write((const uint8_t *)pkt, (size_t)n);
    LoRa.endPacket();                     // blocks until TxDone, then idle
    txCount++;
    Serial.print(F("[tx] "));
    Serial.println(pkt);
    if (appMode != MODE_IDLE) listen();
}

void setup() {
    Serial.begin(115200);
    uint32_t t0 = millis();
    while (!Serial && (millis() - t0) < 3000) { delay(10); }

    Serial.println();
    Serial.println(F("=== RP2040 + Ra-02 (SX1278) - hardware SPI0 build ==="));
    Serial.println(F("wiring: NSS=GP1 SCK=GP2 MOSI=GP3 MISO=GP4 RST=GP29 DIO0=GP28"));

    /* Explicit pin mux - the Pico defaults are the same, but be unambiguous. */
    SPI.setRX(PIN_MISO);
    SPI.setTX(PIN_MOSI);
    SPI.setSCK(PIN_SCK);
    SPI.setCS(PIN_NSS);
    SPI.begin();
    delay(10);

    LoRa.setSPI(SPI);
    LoRa.setPins(PIN_NSS, PIN_RST, PIN_DIO0);

    if (!LoRa.begin(FREQ_HZ)) {
        Serial.println(F("LoRa.begin() failed."));
        Serial.println(F("  1) 3V3 present and ONLY 3.3 V (never 5 V)"));
        Serial.println(F("  2) SCK GP2 / MOSI GP3 / MISO GP4 / NSS GP1"));
        Serial.println(F("  3) RESET GP29 tied and not floating, common GND"));
        Serial.println(F("  4) antenna or 50 ohm load attached"));
        while (true) { digitalWrite(LED_BUILTIN, !digitalRead(LED_BUILTIN)); delay(200); }
    }

    LoRa.setSpreadingFactor(SPREADING_FACTOR);
    LoRa.setSignalBandwidth(BANDWIDTH);
    LoRa.setCodingRate4(5);
    LoRa.setPreambleLength(8);
    LoRa.setSyncWord(SYNC_WORD);
    LoRa.setTxPower(TX_POWER_DBM, PA_OUTPUT_PA_BOOST_PIN);
    LoRa.enableCrc();
    LoRa.idle();

    Serial.print(F("radio ready on "));
    Serial.print((long)(FREQ_HZ / 1000.0));
    Serial.println(F(" kHz, listening"));
    help();
    listen();
}

void loop() {
    /* ---------------------------------------------------- serial commands */
    static char cmdBuf[80];
    static uint8_t cmdLen = 0;

    while (Serial.available()) {
        char c = (char)Serial.read();
        if (c == '\r') continue;
        if (c == '\n') {
            cmdBuf[cmdLen] = '\0';
            if (cmdLen > 0) {
                switch (cmdBuf[0]) {
                    case 'r': appMode = MODE_RX; listen();
                              Serial.println(F("[mode] receive")); break;
                    case 't': beacon(); lastBeacon = millis(); break;
                    case 'a': appMode = MODE_AUTO; lastBeacon = millis() - BEACON_INTERVAL_MS;
                              Serial.println(F("[mode] auto-beacon")); break;
                    case 's': appMode = MODE_IDLE; LoRa.idle();
                              Serial.println(F("[mode] idle")); break;
                    case 'i': printStatus(); break;
                    case 'h': help(); break;
                    case 'm': {
                        const char *msg = cmdBuf + 1;
                        while (*msg == ' ') msg++;
                        size_t len = strlen(msg);
                        if (len == 0) { Serial.println(F("usage: m<text>")); break; }
                        if (len > MAX_PAYLOAD) len = MAX_PAYLOAD;
                        LoRa.beginPacket();
                        LoRa.write((const uint8_t *)msg, len);
                        LoRa.endPacket();
                        txCount++;
                        Serial.print(F("[tx] ")); Serial.println(msg);
                        if (appMode != MODE_IDLE) listen();
                        break;
                    }
                    default:
                        Serial.println(F("unknown command - h for help"));
                        break;
                }
            }
            cmdLen = 0;
        } else if (cmdLen < sizeof(cmdBuf) - 1) {
            cmdBuf[cmdLen++] = c;
        }
    }

    /* ------------------------------------------------------ auto beaconing */
    if (appMode == MODE_AUTO && millis() - lastBeacon >= BEACON_INTERVAL_MS) {
        lastBeacon = millis();
        beacon();
    }

    /* ------------------------------------------------------- receive path */
    if (appMode != MODE_IDLE) {
        int packetSize = LoRa.parsePacket();
        if (packetSize > 0) {
            char buf[MAX_PAYLOAD + 1];
            int n = 0;
            while (LoRa.available() && n < MAX_PAYLOAD) {
                buf[n++] = (char)LoRa.read();
            }
            buf[n] = '\0';
            rxCount++;
            Serial.print(F("[rx] "));
            Serial.print(buf);
            Serial.print(F("   rssi="));
            Serial.print(LoRa.packetRssi());
            Serial.print(F("dBm snr="));
            Serial.print(LoRa.packetSnr());
            Serial.println(F("dB"));
        }
    }

    delay(1);
}
