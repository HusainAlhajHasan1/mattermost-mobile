# SM-P585: F-Droid, Aurora Store and Instagram

This is a Linux-host ADB installer for the previously identified SM-P585,
`gtanotexllte`, build `P585JXS3CTL1`, Android 8.1 / API 27, `armeabi-v7a`.
It checks a hash of the supplied device serial and refuses other devices or
builds. Android must be fully booted as owner user 0 with authorized USB
debugging. The host needs Python 3, `adb`, `curl` and `gpgv`, as well as HTTPS
access to GitHub's API and `f-droid.org`. The tablet needs working Wi-Fi and
access to Aurora's anonymous service and Google Play.

The script:

1. Checks for existing and retained-data packages. It never uninstalls,
   clears, upgrades or downgrades any existing package. It does not touch
   EFS, radio, partitions, Wi-Fi settings, or the existing location modules.
2. Downloads F-Droid **1.23.2** and Aurora Store **4.8.4** directly from the
   official F-Droid repository. Their embedded detached OpenPGP signatures pin
   the exact APK bytes. The public key is fetched from the F-Droid client source
   at a fixed commit and checked against SHA-256
   `b2ee80ba470992f8f44943a4a763235ba9494721fb0f5a6f540798603634cfac`.
   `gpgv` must verify signing subkey
   `802A9799016112346E1FEFF47A029E54DD5DCE7A` and primary fingerprint
   `37D2C98789D8311948394E3E41E7044E1DBA2E89`. The signatures are from
   [F-Droid 1.23.2](https://f-droid.org/repo/org.fdroid.fdroid_1023052.apk.asc)
   and [Aurora 4.8.4](https://f-droid.org/repo/com.aurora.store_76.apk.asc).
   A missing/replaced APK or bad signature is a failure, not a reason to skip
   verification. The Instagram APK is never obtained from an APK mirror.
3. Installs missing clients using ADB and confirms their package presence for
   user 0. It skips already-installed packages, including those with a different
   signing key, and will not reinstall a package that has retained data.
4. If Instagram is missing, confirms the installed Aurora APK is byte-for-byte
   the verified F-Droid 4.8.4 build. It attempts onboarding and anonymous login,
   opens the Instagram listing via Aurora's supported app-info intent, requests
   the download and approves an Android installer only if it identifies
   Instagram. It checks the actual package list before reporting installation.
   A pre-existing Aurora session must visibly identify itself as anonymous;
   a personal or unrecognized account is not used automatically. Existing
   Instagram is reported as already installed without claiming its provenance.

The accessibility-based automation recognizes Aurora's English, Persian and
Arabic labels. Keep Android unlocked and its display awake; do not interact
while it runs. It refuses unrecognized screens instead of guessing coordinates.
Aurora's service may be rate-limited or unavailable; the tablet's Android 8.1
build may not be offered a compatible Instagram version, and installation may
still require interaction. If so the script exits nonzero and reports partial
success. Instagram itself may not function fully without Google services. This
script has not been run on the actual tablet.

## Temporary install permission and recovery

Aurora needs its per-app `REQUEST_INSTALL_PACKAGES` app-op enabled to request an
installation on Android 8. If it is not already allowed, the script saves its
original mode as a private JSON file under
`~/root-p585/p585-apps-backup-*/aurora-appop-before.json`, temporarily allows
it, and attempts to restore and verify the original mode on success, error,
Ctrl-C, or normal SIGTERM. It does not grant Aurora root access. On connection
loss, forced termination or power failure, restoration may fail; use the
script's printed command or the backed-up mode to restore it manually, for
example (replace the last word with the saved `previous_mode`):

```sh
adb -s "$P585_SERIAL" shell appops set --user 0 com.aurora.store REQUEST_INSTALL_PACKAGES default
```

Aurora's normal first-run defaults and anonymous session are created inside
Aurora; these are not a backup of the app's data. The UI dump uses a random
short-lived file under `/data/local/tmp` and removes it in the same command;
a disconnect at exactly that time can leave that temporary file behind.

Anonymous Aurora login uses shared access, not the user's personal Google
account, but it is **not anonymous to the network**: Google Play and Aurora's
services may see the tablet's real IP, requests, device details and installed
app list. No VPN or SIM is added. Screens/settings and mocks cannot guarantee
that Instagram sees a Bahrain location.

## Tests

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts/tests -p 'test_p585_apps_install*.py' -v
```

Tests use an unrelated synthetic public key and signed fixture, plus fake ADB;
no fixture private key, network access, Google credentials, or real device is
required. They exercise signature success/failure, device/package guards,
non-destructive retained-data handling, safe UI matching, anonymous-session
checks, app-op restoration, false-success detection and partial reporting.
