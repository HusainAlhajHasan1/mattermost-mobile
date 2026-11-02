#!/system/bin/sh
# Magisk late_start worker: no early-boot system.prop and no /system or EFS writes.
set -u
umask 077
MODDIR=${0%/*}
PKG=io.appium.settings
LAT=26.223536
LON=50.586994
COORD_PATTERN='(26[.,]223536,50[.,]586994|۲۶(٫|[.])۲۲۳۵۳۶,۵۰(٫|[.])۵۸۶۹۹۴|٢٦(٫|[.])٢٢٣٥٣٦,٥٠(٫|[.])٥٨٦٩٩٤)'
[ -f "$MODDIR/disable" ] && exit 0
[ -f "$MODDIR/owner" ] && [ -f "$MODDIR/backup-path" ] || exit 1
STATE=$(cat "$MODDIR/backup-path") || exit 1
case "$STATE" in /data/adb/p585-manama-backups/p585-manama-????????) ;; *) exit 1 ;; esac
[ -d "$STATE" ] && [ ! -L "$STATE" ] || exit 1
[ -d "$MODDIR/touched" ] && [ ! -L "$MODDIR/touched" ] || exit 1

MAGISK=$(command -v magisk 2>/dev/null || true)
[ -n "$MAGISK" ] && [ -x "$MAGISK" ] || exit 1
BOOT_ID=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null) || exit 1
case "$BOOT_ID" in ''|*[!A-Fa-f0-9-]*) exit 1 ;; esac

# Avoid a second watcher. Never kill a process solely based on a stale PID.
if [ -f "$MODDIR/worker.pid" ]; then
    oldpid=$(cat "$MODDIR/worker.pid" 2>/dev/null || true)
    case "$oldpid" in
        ''|*[!0-9]*) ;;
        *) if [ -r "/proc/$oldpid/cmdline" ] &&
              tr '\000' ' ' < "/proc/$oldpid/cmdline" | grep -F "$MODDIR/service.sh" >/dev/null; then
               exit 0
           fi ;;
    esac
fi
printf '%s\n' "$$" > "$MODDIR/worker.pid"
cleanup() {
    if [ "$(cat "$MODDIR/worker.pid" 2>/dev/null || true)" = "$$" ]; then
        rm -f "$MODDIR/worker.pid"
    fi
}
trap 'cleanup; exit 0' TERM INT
trap cleanup EXIT

mock_is_active() {
    dump=$(dumpsys location 2>/dev/null) || return 1
    printf '%s\n' "$dump" | grep -F 'Mock Providers:' >/dev/null || return 1
    printf '%s\n' "$dump" | grep -E "Location\\[gps $COORD_PATTERN .*mock" >/dev/null || return 1
    printf '%s\n' "$dump" | grep -E "Location\\[network $COORD_PATTERN .*mock" >/dev/null || return 1
}

# Six Android properties only: never write SIM data, IMEI, EFS, or modem firmware.
restore_one_if_still_spoofed() {
    key=$1 wanted=$2
    [ "$(getprop "$key")" = "$wanted" ] || return 0
    present=$(cat "$STATE/props/$key.present") || return 1
    if [ "$present" = 1 ]; then
        value=$(cat "$STATE/props/$key.value") || return 1
        "$MAGISK" resetprop -n "$key" "$value"
    elif [ "$present" = 0 ]; then
        "$MAGISK" resetprop --delete "$key"
    else
        return 1
    fi
}
restore_if_needed() {
    # Only undo values written in this boot. After a reboot the modem may have
    # reported new real values that coincidentally match the Bahrain preset.
    [ "$(cat "$MODDIR/spoof-boot" 2>/dev/null || true)" = "$BOOT_ID" ] || return 0
    failed=0
    restore_one_if_still_spoofed gsm.sim.operator.numeric 42601 || failed=1
    restore_one_if_still_spoofed gsm.sim.operator.iso-country bh || failed=1
    restore_one_if_still_spoofed gsm.sim.operator.alpha Batelco || failed=1
    restore_one_if_still_spoofed gsm.operator.numeric 42601 || failed=1
    restore_one_if_still_spoofed gsm.operator.iso-country bh || failed=1
    restore_one_if_still_spoofed gsm.operator.alpha Batelco || failed=1
    [ "$failed" = 1 ] || rm -f "$MODDIR/spoof-boot"
}
set_one() {
    [ "$(getprop "$1")" = "$2" ] || "$MAGISK" resetprop -n "$1" "$2"
}
helper_matches_snapshot() {
    package_path=$(pm path "$PKG" 2>/dev/null) || return 1
    case "$package_path" in package:/data/app/*/base.apk) ;; *) return 1 ;; esac
    apk_path=${package_path#package:}
    [ -f "$apk_path" ] && [ ! -L "$apk_path" ] || return 1
    apk_hash=$(sha256sum "$apk_path" 2>/dev/null) || return 1
    apk_hash=${apk_hash%% *}
    [ "$apk_hash" = "$(cat "$STATE/helper_apk.sha256")" ]
}
set_spoof() {
    : > "$MODDIR/touched/carrier" || return 1
    printf '%s\n' "$BOOT_ID" > "$MODDIR/spoof-boot" || return 1
    set_one gsm.sim.operator.numeric 42601 &&
    set_one gsm.sim.operator.iso-country bh &&
    set_one gsm.sim.operator.alpha Batelco &&
    set_one gsm.operator.numeric 42601 &&
    set_one gsm.operator.iso-country bh &&
    set_one gsm.operator.alpha Batelco
}

# Start late, only after Android is up. Retry while the user is locked or the
# service is temporarily unavailable. This is not a guarantee against leaks.
while [ "$(getprop sys.boot_completed)" != 1 ]; do
    [ -f "$MODDIR/disable" ] && exit 0
    sleep 5
done
last_status=''
while [ ! -f "$MODDIR/disable" ]; do
    if helper_matches_snapshot; then
        am start-foreground-service --user 0 -n "$PKG/.LocationService" \
            --es latitude "$LAT" --es longitude "$LON" --es altitude 5.0 >/dev/null 2>&1 || true
        # Allow the asynchronous Appium timer to emit a fix.
        sleep 3
        [ -f "$MODDIR/disable" ] && break
        if mock_is_active && set_spoof; then
            [ "$last_status" = active ] || printf '%s\n' 'Manama mock providers active; property spoof running.'
            last_status=active
        else
            restore_if_needed
            [ "$last_status" = unavailable ] || printf '%s\n' 'Mock providers not verified; Bahrain property spoof paused.'
            last_status=unavailable
        fi
    else
        restore_if_needed
        [ "$last_status" = missing ] || printf '%s\n' 'Mock helper is missing or changed; Bahrain property spoof paused.'
        last_status=missing
    fi
    n=0
    while [ "$n" -lt 9 ] && [ ! -f "$MODDIR/disable" ]; do
        sleep 3
        n=$((n + 1))
    done
done
