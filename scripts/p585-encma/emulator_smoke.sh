#!/usr/bin/env bash
# CI only: installs the x86 sibling build on a fresh API-27 emulator.
# Does not attempt a VPN connection or contact a real Mattermost server.
set -euo pipefail

apk=${1:?Usage: emulator_smoke.sh X86_APK}
test -s "$apk"
serial=$(adb get-serialno | tr -d '\r')
if [[ ! "$serial" =~ ^emulator-[0-9]+$ ]]; then
    echo 'Refusing to run: no single Android emulator was selected.' >&2
    exit 1
fi
# Target only the emulator after checking its serial; never touch an attached tablet.
adb() { command adb -s "$serial" "$@"; }
adb wait-for-device
qemu=$(adb shell getprop ro.kernel.qemu | tr -d '\r')
sdk=$(adb shell getprop ro.build.version.sdk | tr -d '\r')
abis=$(adb shell getprop ro.product.cpu.abilist | tr -d '\r')
if [[ "$qemu" != 1 || "$sdk" != 27 || ",$abis," != *,x86,* ]]; then
    echo "Refusing smoke test on unexpected emulator: qemu=$qemu sdk=$sdk abis=$abis" >&2
    exit 1
fi

adb shell input keyevent 82 || true
adb shell settings put system screen_off_timeout 600000
adb install "$apk"
adb logcat -c
launch=$(adb shell am start -W -n com.encma.hxmain/com.mattermost.rnbeta.MainActivity | tr -d '\r')
printf '%s\n' "$launch"
if ! grep -Fq 'Status: ok' <<< "$launch"; then
    echo 'App did not launch successfully.' >&2
    exit 1
fi

# Wait for the React Native VPN entry screen, not merely a live splash process.
# This is a fresh emulator; the script never enters VPN credentials or connects.
seen=0
for _ in $(seq 1 24); do
    if adb shell uiautomator dump --compressed /sdcard/p585-window.xml >/dev/null 2>&1; then
        xml=$(adb exec-out cat /sdcard/p585-window.xml 2>/dev/null | tr -d '\r' || true)
        if grep -Fq 'VPN connection' <<< "$xml" && grep -Fq 'WireGuard configuration' <<< "$xml"; then
            seen=1
            break
        fi
    fi
    sleep 5
done
if [[ "$seen" != 1 ]] || ! adb shell pidof com.encma.hxmain >/dev/null; then
    echo 'The API-27 emulator did not reach a live WireGuard configuration screen.' >&2
    adb logcat -d -v brief 'AndroidRuntime:E' 'ReactNativeJS:E' '*:S' | tail -180 || true
    exit 1
fi
printf '%s\n' 'API-27 x86 emulator: app installed, process alive, and WireGuard entry screen rendered.'
printf '%s\n' 'ARM hardware, VPN negotiation, server login, and tablet behavior remain untested.'
