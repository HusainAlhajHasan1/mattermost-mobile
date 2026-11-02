# SM-P585: Uptodown, F-Droid and Instagram via ADB

This **Linux host** script is specific to the previously identified Samsung
SM-P585 (`gtanotexllte`, build `P585JXS3CTL1`, Android 8.1/API 27,
`armeabi-v7a`) and serial `5203c66b523ca357`. It does not flash anything or
require root. It installs with ADB, without tapping an on-device installer.
Android must already be booted, USB debugging authorized, and owner user 0
active. The host needs Python 3.6+, `adb`, `curl`, and `gpgv`; the download
command also uses `sha256sum`. Free host disk space is needed for temporary
APKs. From a checkout run:

```sh
P585_SERIAL=5203c66b523ca357 python3 scripts/p585-uptodown-install.py
```

## What it does

- Checks the exact serial, model, device, build, API, ABI, boot state and user
  before any installation. It lists both currently installed and retained
  packages and checks again immediately before installing. Existing Uptodown,
  Instagram, F-Droid and Aurora are not replaced, cleared or uninstalled.
  Packages retained after an uninstall are not reinstalled automatically.
- Attempts the Uptodown App Store 7.42 APK from Uptodown, comparing downloaded
  bytes to the pinned SHA-256 below before `adb install`.
- If Instagram is absent, looks on Uptodown for **one exact older Instagram
  APK**. It will install only if the downloaded bytes match the independently
  published fingerprint below. It does not guess an architecture, install the
  latest ARM64-only APK, use XAPKs/splits, or silently use a different mirror.
  The catalog search and downloader are bounded; if the exact file is absent,
  ambiguous, inaccessible, or the service changes, Instagram is not installed.
- If F-Droid is missing, attempts its official APK from `f-droid.org`. It
  validates a pinned F-Droid public key and detached signature with `gpgv`.
- Reports each package separately. An exit code of 1 indicates an incomplete
  result; `already installed` means presence only, not that the existing APK's
  authenticity or version was checked. Newly installed packages must return
  `Success` and appear in the owner package list.

The Instagram build is from **April 2025**, so it may lack security updates or
be refused by Instagram's servers. APKMirror lists its minimum OS and ABI as
compatible; that is **not** proof that it will run on this Samsung device or
log in successfully. Instagram can require an Instagram account even though a
Google account is not required for these installation attempts. A newer
compatible APK may exist; this installer does not assume one is authentic or
install it without a separately established fingerprint.

## Source and verification details

- Uptodown App Store: package `com.uptodown`, version 7.42, app ID 18098,
  Uptodown file ID 1232507681, Android 6+ and `armeabi-v7a` among its ABIs.
  The [Uptodown file page](https://uptodown-android.en.uptodown.com/android/download/1232507681-x)
  publishes SHA-256
  `d903bec98d76854baa279c81b97048273a7ffd506e2d421308efbb6e7bf66b76`.
  The SHA-256 comes from the distributor's page; it is not an independently
  verified Uptodown signing certificate.
- Instagram: package `com.instagram.android`, version 378.0.0.52.68, Android
  version code 378213503, standalone APK, `armeabi-v7a`, nodpi, Android 8+.
  [APKMirror's listing](https://www.apkmirror.com/apk/instagram/instagram-instagram/instagram-378-0-0-52-68-release/instagram-378-0-0-52-68-19-android-apk-download/)
  publishes APK SHA-256
  `47e70d9ed13da9228277f8f8b7cb564b845074a71fd0afb118650a52cedacdbe`
  and an Instagram signing-certificate fingerprint. APKMirror provides the
  independent fingerprint only; the script downloads solely from Uptodown.
  Uptodown's availability of these *exact* bytes is not established.
- F-Droid: official
  [`org.fdroid.fdroid_1023052.apk`](https://f-droid.org/repo/org.fdroid.fdroid_1023052.apk)
  and its detached signature embedded in the script. The pinned signing-key
  file is fetched from `f-droid/fdroidclient` commit
  `ed2b119f33aeaa04b722f85fc267cc7dffca28a9`, SHA-256
  `b2ee80ba470992f8f44943a4a763235ba9494721fb0f5a6f540798603634cfac`.
  The expected primary fingerprint is
  `37D2C98789D8311948394E3E41E7044E1DBA2E89` and signing subkey is
  `802A9799016112346E1FEFF47A029E54DD5DCE7A`. See F-Droid's
  [verification guide](https://f-droid.org/docs/Verifying_Downloaded_APK/).
- Uptodown anonymous download-URL resolution uses the public client protocol
  documented in [Obtainium's Uptodown source](https://github.com/ImranR98/Obtainium/blob/main/lib/app_sources/uptodown.dart).
  This is an undocumented service and may require a newer protocol, a browser
  challenge, or be blocked. The script does not request a personal account,
  store a bearer token on disk, or send it to a redirect/CDN.

The script and unit tests were developed without access to the tablet and
without fetching the real APK binaries or testing the live Uptodown API.
Synthetic tests do not prove that the store, Instagram, or the tablet will
work. An Android package manager success confirms installation, not a
successful first launch or login.

## Privacy and safety

The script does not change the tablet's VPN, Wi-Fi, location, timezone,
permissions, app-ops, system packages, partitions, or SIM properties. Download
requests originate from the **Linux host**, not necessarily through the
**tablet's VPN**; Uptodown, F-Droid, GitHub and their CDNs may see the host's
IP and requested resources. Uptodown's anonymous protocol uses a freshly
random client identifier. Opening either store or Instagram later generates
its own app network traffic according to that app and the device's VPN
routing; the script does not verify that routing or DNS privacy.

Temporary downloaded APKs are removed when the script exits normally. A
terminal interruption or force-kill can leave temporary files; they contain
APK downloads, not a backup of the tablet's app data. Nothing is uninstalled
to fix signature conflicts. If a step fails, retain the output and share the
three `Result:` lines and relevant error; do not share any passwords or account
tokens.

## Tests

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v scripts/tests/test_p585_uptodown_install.py
```

The tests mock ADB and the network, exercise pinned-hash and redirect checks,
use a synthetic signed payload with real `gpgv` when available, and test
retained packages, false ADB success, and partial failures. They do not use a
real account, an Android device, or a real distributor APK.
