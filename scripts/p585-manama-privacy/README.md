# P585 Manama timezone and scan add-on

This is a companion to an **already active** Manama mock GPS provider; it does
not install, remove or replace the mock-location app. It requires Android 8.1
on the identified rooted SM-P585, ADB, and a working Magisk root grant. The
installer first verifies the GPS mock at 26.223536, 50.586994; it refuses to
change settings if it cannot verify that mock.

* It records the original timezone, automatic timezone, Wi-Fi scan-always and
  Bluetooth scan-always settings and six carrier-property values. A private
  checksum-verified copy is saved on both the host and tablet before settings
  are changed. These are **settings backups, not a full device backup**.
* It uses Android's alarm service, via its runtime `IAlarmManager` interface,
  to set `Asia/Bahrain`, disables automatic timezone, and sends a timezone-change
  broadcast. It verifies the property and a fresh Android Java process's default
  timezone. Apps may still use their own caches, account or server timezones.
* It sets `wifi_scan_always_enabled` and `ble_scan_always_enabled` to `0` and
  **does not turn off Wi-Fi or Bluetooth**. On AOSP 8.1 those settings limit
  scan-always when the respective radio is off. They do **not** prevent scans
  for connectivity or scans requested by apps while Wi-Fi/Bluetooth is on.
  This tool does not forge access points, Wi-Fi BSSIDs, SSIDs, or Bluetooth
  devices and cannot make all Wi-Fi scan results look like Bahrain.
* With the original `p585_manama` module present and recognized, that module
  alone owns the carrier-property spoof. Otherwise this add-on can set the six
  same volatile `gsm.*` properties to Batelco/Bahrain while the mock GPS is
  verified. No actual SIM, SIM state, IMEI, IMSI, ICCID, EFS, modem, carrier
  connection, or network is changed. An app can still report **no SIM**.
* The Magisk late-start worker retries settings after Android finishes booting.
  This is best effort: it cannot stop leaks before the worker starts, while a
  mock is missing, between retries, or from cached data. It does not guarantee
  a real-location lockout. Apps can detect mocks or use Wi-Fi, IP, accounts,
  Bluetooth, Google services and other location signals. **No VPN is installed;
  the internet IP and its apparent location do not change.**

Carrier-property changes can affect telephony, mobile data and emergency
behavior even with no SIM. Do not use a mocked location for emergency services,
navigation or safety. The recovery UI on this tablet has previously been
unusable, so any persistent root module carries additional recovery risk.

The script prints an exact status command and a restore command inside its
private host backup directory. Run the add-on's **restore before restoring the
original Manama module** if both are installed. Restoration verifies both
snapshots, stops the add-on worker, restores marked settings where the current
value is still the add-on value, and removes only the add-on. Independently
changed values are preserved. Rebooting may help refresh caches; do not delete
backups until satisfied.

## Timezone helper provenance

`TzControl.java` is the complete source for the 1.9 KB `tzcontrol.jar`, which
contains only `classes.dex`. It uses reflection to call the installed device's
`IAlarmManager.setTimeZone(String)`; it does not guess a Samsung Binder
transaction number. The class was compiled as Java 6 with the AOSP prebuilt
JDK 8 from `khadas/android_prebuilts_jdk_jdk8` (`khadas-vim3-pie`), then
converted to DEX 035 using `zoutaov/dx-mavenized` commit
`7c980bab574163242cbf50f5c0fdf21f67aa19b7`, jar Git blob
`1ba9d528befb4635412d036fc2eca6a0422d9023` (SHA-256
`e9941cbcafbce03a728108bf5d602ec8a17fa928347e25954a29f72d280f3747`).
The ZIP contains a single `classes.dex`, timestamped 2009-01-01 for
reproducibility. The installer pins the Git blob and SHA-256 of both the helper
and the device-side script. Build tools are not downloaded to the tablet.

The tests use synthetic Android commands, a fake Binder interface and fake ADB.
They do **not** establish actual compatibility on this Samsung tablet, correct
behavior of Samsung/Google location services, reboot persistence, or app-level
privacy. No commands from this add-on have been run on the tablet by the agent.
