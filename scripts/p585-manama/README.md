# P585 Manama simulation

This is a **best-effort, reversible simulation** for the previously identified
SM-P585 running Android 8.1 and Magisk. It is not a guarantee that every app
will see Bahrain, nor does it prevent every real-location leak. It does not
change a SIM, IMEI, EFS, modem, physical network, IP address, account region or
payment country. No VPN is installed. Apps can detect or reject mock locations
or derive a real region from IP, cell information, Wi-Fi, accounts, sensors,
caches, Google services or other channels.

The setup:

* Backs up the original location-provider choices, automatic timezone and time
  zone, and the presence and values of six `gsm.*` properties on the host, then
  copies and checksum-verifies that snapshot on the tablet before installation.
  It refuses to overwrite a pre-existing `io.appium.settings` package or an
  existing mock-location provider/allowance.
* Runs Android test providers at 26.223536, 50.586994 (central Manama). Android's
  real `gps` and `network` providers are displaced while the mock providers are
  active; this is **not** a hardware GPS kill switch or a guarantee that GNSS
  hardware cannot be queried by privileged software. The helper also attempts
  the Google Play services fused mock API where available, but the script only
  verifies the Android GPS and network test-provider output.
* Sets the timezone **property** to `Asia/Bahrain` and disables automatic time
  zone while active. Some running apps may require a restart to notice the
  changed zone. The UTC clock itself is not adjusted.
* Uses volatile Magisk `resetprop -n` changes for Bahrain/Batelco country,
  numeric operator `42601` and operator name. Radio/SIM services can overwrite
  these properties before the late-start watcher reapplies them, and other
  telephony sources are not spoofed. Changing these properties can affect
  mobile calls, data, emergency behavior and other region-sensitive features.
* Installs an isolated late-start Magisk module (`service.sh`, not
  `post-fs-data.sh` or `system.prop`) that retries the mock after normal Android
  boot. It never reboots the tablet. The module pauses its carrier changes when
  it cannot verify the helper and mock providers. Do not depend on spoofing
  while the phone is booting, after a crash, or while the helper is unavailable.

**Do not use simulated location for emergency calls, safety services or
navigation.** Keep both backup copies private. Do not change SIMs while the
simulation is active: restoration uses the saved property snapshot, which may
be stale after a SIM/network change. Rebooting after restoration may help the
radio publish current values again, but the script does not reboot or promise
that this fixes network service.

The installation prints the location of a private `tool.sh` in its backup
folder. From the host terminal, use:

```sh
bash /path/to/private/backup/tool.sh status
bash /path/to/private/backup/tool.sh restore
```

The actual paths are printed by the script. Restore validates both snapshots,
stops the watcher and helper, restores only settings the script marked as
changed, and preserves a carrier value if Android/the radio has independently
replaced the spoofed value. It checks that the helper APK is still the exact
locally generated file before uninstalling it, and removes only its own module.
Backups remain in place. An interrupted action holds a lock so retrying cannot
overlap an ongoing action. Google Play services leaves its fused mock mode
asynchronously, and apps may retain cached locations; restart affected apps or
reboot after restoring. The host and device snapshots are checksummed for
accidental damage, not cryptographically authenticated against a malicious root
user.

## APK provenance and hardening

The script downloads the npm release archive for the Apache-2.0-licensed
[Appium Settings 5.7.5](https://github.com/appium/io.appium.settings/tree/v5.7.5),
checks pinned SHA-256 hashes for both the archive and original APK, and runs
`repack.py` locally. The upstream dex, resources and non-signature metadata
are retained; the pinned upstream [NOTICE](NOTICE-Appium.txt) and license are
included in the generated APK under `assets/manama-notices/`.
`AndroidManifest.xml` documents the changes: remove all exported
receivers and unrelated services/permissions, retain only the location service
protected by `android.permission.DUMP`, retain the non-exported Google API
activity and version metadata, and make the app non-debuggable. Only fine,
coarse and mock-location permissions are requested. The source of the compiled
binary manifest embedded in `repack.py` is the adjacent XML, linked against
Android API 27 with aapt2.

A fresh self-signed APK v1 signing key is generated on the host and deleted
after signing. The APK is kept in the private backup for verification. This
version cannot be updated using Appium's original signing key; restore and
uninstall it before installing a different Appium package. Never share device
backups. The setup requires Bash, ADB, curl, tar, SHA-256 tools, Python 3.8+ and
OpenSSL on the Linux host, and a working Magisk root grant for the ADB shell.

Tests in `scripts/tests/test_p585_manama*.py` use synthetic APKs and simulated
ADB/device commands. They do **not** establish that this particular tablet,
Samsung's location implementation, Play services, auto-start after reboot,
cellular service, or any third-party app actually behaves as expected.
