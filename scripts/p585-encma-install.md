# Experimental ENCMA installer for SM-P585

This host-side installer is restricted to the previously identified Samsung
SM-P585 (`gtanotexllte`, `P585JXS3CTL1`, Android 8.1/API 27, `armeabi-v7a`,
serial `5203c66b523ca357`) and owner user 0. Android must be booted and USB
ADB authorized. The Linux host needs Python 3.6+, `adb` and `curl`; the
published download-and-run command also uses `sha256sum` and sets the device
serial through `P585_SERIAL`. No root access or touchscreen installation is
required.

## Source and experimental status

The original requested [Actions run](https://github.com/HusainAlhajHasan1/mattermost-mobile/actions/runs/41048323747)
built an Android 11+ arm64 version. Its exact source commit is
`4a9471207b14d41f5ffec2bf6b547061e8df7949`. The experimental compatibility
workflow uses that same source, changes the minimum SDK from 30 to 27, guards
Android 9/10-only navigation-bar APIs against older devices, and builds
separate `armeabi-v7a` and x86 variants without suppressing Android library
manifest checks. It verifies package metadata, APK signature, native ELF
architectures, and that ARM has the important libraries present in the x86 build.

In [Actions run 41503802436](https://github.com/HusainAlhajHasan1/mattermost-mobile/actions/runs/41503802436),
the x86 sibling installed and rendered the WireGuard entry screen on a fresh
Android 8.1 emulator before the ARM artifact was uploaded. The ARM APK has
**not** been tested on the user's tablet. The emulator test does not prove ARM
runtime, VPN negotiation, server access, login, calling, notifications, or other
features will work. Lowering the declared minimum SDK is not, by itself, proof
of compatibility.

The release build uses the repository's **public debug signing key**, with
certificate SHA-256
`1431eedaa27b30fd846283f083e52a5fa40c0261e1a039a337a37174d699e750`.
Anyone with that key can sign updates. This build is for testing, **not a
trusted production release**; do not rely on it for sensitive data.

## Installation and VPN warning

The installer downloads only the artifact pinned in the script and checks its
ZIP SHA-256, its sole APK's SHA-256, and its exact APK size. It checks the
model/build/serial/ABI/user and package list before download, then checks again
before installation. If ENCMA is already installed or has retained package data,
it is preserved and never updated, removed, cleared, or downgraded. If already
installed, the existing app is opened without verifying which build it is.

After confirming that ENCMA is installed for owner user 0, the installer
**opens it at the user's request** and checks that its process is alive. A
successful launch request and a running process do not prove that its UI, VPN,
login or other features work. It never enters a VPN configuration or explicitly
connects one. However, **opening ENCMA may automatically reconnect a saved
WireGuard configuration and replace the current VPN**. The user explicitly
accepted that risk before receiving this version of the command. Do not provide
or publish private keys, passwords, or VPN configuration in chat.

Downloads use the **computer's network**, not necessarily the tablet's VPN.
The pinned ZIP is also available as an [experimental public release asset](https://github.com/HusainAlhajHasan1/mattermost-mobile/releases/download/encma-android81-armv7-experimental-41503802436/encma-experimental-android81-armeabi-v7a.zip).
Its ZIP and APK hashes match the Actions build. The original Actions artifact
expires on **2026-12-02 at 19:10 UTC**; the separate release asset remains until
removed by the repository owner. The release asset was verified anonymously,
without a GitHub login.

## Testing boundary

The host tests use fake ADB and synthetic archives. They check preflight,
package preservation and races, missing pins, digest/size failures, unexpected
archive members, missing package-list markers, false ADB success, and launch
results. They do not prove actual Samsung behavior, VPN negotiation or login.
The workflow's Android emulator is x86, not ARM and not the user's tablet.
