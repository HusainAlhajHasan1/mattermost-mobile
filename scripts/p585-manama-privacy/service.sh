#!/system/bin/sh
# Late-start add-on for an already-running Manama mock. No /system, SIM or EFS writes.
set -u
umask 077
MODDIR=${0%/*}
ACTION=${1:-worker}
PKGBASE=/data/adb/modules/p585_manama
COORD='(26[.,]223536,50[.,]586994|۲۶(٫|[.])۲۲۳۵۳۶,۵۰(٫|[.])۵۸۶۹۹۴|٢٦(٫|[.])٢٢٣٥٣٦,٥٠(٫|[.])٥٨٦٩٩٤)'
PROPS='gsm.sim.operator.numeric gsm.sim.operator.iso-country gsm.sim.operator.alpha gsm.operator.numeric gsm.operator.iso-country gsm.operator.alpha'
fail() { printf 'PRIVACY_ERROR: %s\n' "$*" >&2; exit 1; }
[ "$MODDIR" = /data/adb/modules/p585_manama_privacy ] || fail 'unexpected module path'
[ -d "$MODDIR" ] && [ ! -L "$MODDIR" ] || fail 'missing module'
[ -d "$MODDIR/touched" ] && [ ! -L "$MODDIR/touched" ] || fail 'missing markers'
TOKEN=$(cat "$MODDIR/owner" 2>/dev/null) || fail 'missing owner'
case "$TOKEN" in p585-privacy-????????) ;; *) fail 'invalid owner' ;; esac
STATE=/data/adb/p585-privacy-backups/$TOKEN
[ -d "$STATE" ] && [ ! -L "$STATE" ] || fail 'missing backup'
[ "$(cat "$STATE/token" 2>/dev/null)" = "$TOKEN" ] || fail 'backup owner mismatch'
MODE=$(cat "$STATE/carrier_mode") || fail 'missing carrier mode'
case "$MODE" in independent|shared) ;; *) fail 'invalid carrier mode' ;; esac
ORIG_BASE=$(cat "$STATE/base_owner") || fail 'missing base owner'
ORIG_TZ=$(cat "$STATE/timezone") || fail 'missing timezone'
ORIG_AUTO=$(cat "$STATE/auto_time_zone") || fail 'missing auto timezone'
ORIG_WIFI=$(cat "$STATE/wifi_scan_always_enabled") || fail 'missing Wi-Fi scan setting'
ORIG_BLE=$(cat "$STATE/ble_scan_always_enabled") || fail 'missing Bluetooth scan setting'
case "$ORIG_TZ" in ''|*[!A-Za-z0-9_./+-]*) fail 'invalid backup timezone' ;; esac
for item in "$ORIG_AUTO" "$ORIG_WIFI" "$ORIG_BLE"; do
    case "$item" in 0|1|null) ;; *) fail 'invalid setting backup' ;; esac
done
case "$MODE:$ORIG_BASE" in independent:none|shared:p585-manama-????????) ;; *) fail 'invalid base owner' ;; esac
MAGISK=$(command -v magisk 2>/dev/null || true)
[ -n "$MAGISK" ] && [ -x "$MAGISK" ] || fail 'Magisk unavailable'
BOOT_ID=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null) || fail 'cannot determine boot ID'
case "$BOOT_ID" in ''|*[!A-Fa-f0-9-]*) fail 'invalid boot ID' ;; esac

jar_matches() {
    [ -f "$MODDIR/tzcontrol.jar" ] && [ ! -L "$MODDIR/tzcontrol.jar" ] || return 1
    x=$(sha256sum "$MODDIR/tzcontrol.jar" 2>/dev/null) || return 1
    [ "${x%% *}" = "$(cat "$STATE/tz_sha256")" ]
}
base_matches() {
    [ -d "$PKGBASE" ] && [ ! -L "$PKGBASE" ] && [ ! -f "$PKGBASE/disable" ] &&
        [ "$(cat "$PKGBASE/owner" 2>/dev/null)" = "$ORIG_BASE" ]
}
mock_gps() {
    dump=$(dumpsys location 2>/dev/null) || return 1
    printf '%s\n' "$dump" | grep -F 'Mock Providers:' >/dev/null || return 1
    printf '%s\n' "$dump" | grep -E "Location\\[gps $COORD .*mock" >/dev/null
}
prop_present() { getprop | grep -F "[$1]:" >/dev/null; }
verify_snapshot() {
    [ "$(getprop persist.sys.timezone)" = "$ORIG_TZ" ] || fail 'timezone changed since backup'
    [ "$(settings --user 0 get global auto_time_zone)" = "$ORIG_AUTO" ] || fail 'auto timezone changed since backup'
    [ "$(settings --user 0 get global wifi_scan_always_enabled)" = "$ORIG_WIFI" ] || fail 'Wi-Fi scan setting changed since backup'
    [ "$(settings --user 0 get global ble_scan_always_enabled)" = "$ORIG_BLE" ] || fail 'Bluetooth scan setting changed since backup'
    if [ "$MODE" = shared ]; then
        base_matches || fail 'existing Manama module changed since backup'
    else
        [ ! -e "$PKGBASE" ] && [ ! -L "$PKGBASE" ] || fail 'a Manama module appeared since backup'
        for key in $PROPS; do
            present=$(cat "$STATE/props/$key.present") || fail "missing $key presence"
            case "$present" in
                1) prop_present "$key" && [ "$(getprop "$key")" = "$(cat "$STATE/props/$key.value")" ] || fail "$key changed since backup" ;;
                0) ! prop_present "$key" || fail "$key appeared since backup" ;;
                *) fail "invalid $key backup" ;;
            esac
        done
    fi
}
check_timezone() {
    jar_matches || return 1
    result=$(CLASSPATH="$MODDIR/tzcontrol.jar" app_process /system/bin local.manama.TzControl validate "$1" 2>&1) || return 1
    [ "$result" = "MANAMA_TZ_VALID=$1" ]
}
set_timezone() {
    jar_matches || return 1
    result=$(CLASSPATH="$MODDIR/tzcontrol.jar" app_process /system/bin local.manama.TzControl set "$1" 2>&1) || { printf '%s\n' "$result" >&2; return 1; }
    [ "$result" = "MANAMA_TZ_DEFAULT=$1" ] && [ "$(getprop persist.sys.timezone)" = "$1" ] || return 1
    result=$(am broadcast --user all -a android.intent.action.TIMEZONE_CHANGED --es time-zone "$1" 2>&1) || return 1
    case "$result" in *'Broadcast completed: result='*) return 0 ;; *) return 1 ;; esac
}
set_global() {
    if [ "$(settings --user 0 get global "$1")" != "$2" ]; then
        settings --user 0 put global "$1" "$2" || return 1
    fi
    [ "$(settings --user 0 get global "$1")" = "$2" ]
}
restore_global() {
    key=$1 original=$2
    current=$(settings --user 0 get global "$key") || return 1
    if [ "$current" != 0 ]; then
        printf 'PRIVACY_NOTE: %s changed independently; leaving it alone.\n' "$key"
        return 0
    fi
    if [ "$original" = null ]; then settings --user 0 delete global "$key" || return 1
    else settings --user 0 put global "$key" "$original" || return 1; fi
    [ "$(settings --user 0 get global "$key")" = "$original" ]
}
set_one() { [ "$(getprop "$1")" = "$2" ] || "$MAGISK" resetprop -n "$1" "$2"; }
set_carrier() {
    [ "$MODE" = independent ] || return 0
    [ ! -e "$PKGBASE" ] && [ ! -L "$PKGBASE" ] || return 1
    : > "$MODDIR/touched/carrier" || return 1
    printf '%s\n' "$BOOT_ID" > "$MODDIR/spoof-boot" || return 1
    set_one gsm.sim.operator.numeric 42601 && set_one gsm.sim.operator.iso-country bh &&
        set_one gsm.sim.operator.alpha Batelco && set_one gsm.operator.numeric 42601 &&
        set_one gsm.operator.iso-country bh && set_one gsm.operator.alpha Batelco
}
restore_one() {
    key=$1 target=$2
    [ "$(getprop "$key")" = "$target" ] || return 0
    present=$(cat "$STATE/props/$key.present") || return 1
    case "$present" in
        1) value=$(cat "$STATE/props/$key.value") || return 1
           "$MAGISK" resetprop -n "$key" "$value" || return 1
           [ "$(getprop "$key")" = "$value" ] && prop_present "$key" ;;
        0) if prop_present "$key"; then "$MAGISK" resetprop --delete "$key" || return 1; fi
           ! prop_present "$key" ;;
        *) return 1 ;;
    esac
}
restore_carrier() {
    [ "$MODE" = independent ] || return 0
    # A reboot can cause a real radio update that happens to match the preset.
    [ "$(cat "$MODDIR/spoof-boot" 2>/dev/null || true)" = "$BOOT_ID" ] || return 0
    failed=0
    restore_one gsm.sim.operator.numeric 42601 || failed=1
    restore_one gsm.sim.operator.iso-country bh || failed=1
    restore_one gsm.sim.operator.alpha Batelco || failed=1
    restore_one gsm.operator.numeric 42601 || failed=1
    restore_one gsm.operator.iso-country bh || failed=1
    restore_one gsm.operator.alpha Batelco || failed=1
    [ "$failed" = 0 ] || return 1
    rm -f "$MODDIR/spoof-boot"
}
stop_worker() {
    : > "$MODDIR/disable" || return 1
    pid=$(cat "$MODDIR/worker.pid" 2>/dev/null || true)
    case "$pid" in ''|*[!0-9]*) return 0 ;; esac
    if [ -r "/proc/$pid/cmdline" ] && tr '\000' ' ' < "/proc/$pid/cmdline" | grep -F "$MODDIR/service.sh" >/dev/null; then
        kill "$pid" 2>/dev/null || return 1
        n=0
        while [ -r "/proc/$pid/cmdline" ] && tr '\000' ' ' < "/proc/$pid/cmdline" | grep -F "$MODDIR/service.sh" >/dev/null; do
            [ "$n" -lt 20 ] || return 1
            sleep 1; n=$((n + 1))
        done
    fi
}

case "$ACTION" in
    apply|restore|disable)
        LOCK="$MODDIR/control.lock"
        if ! mkdir "$LOCK" 2>/dev/null; then
            other=$(cat "$LOCK/pid" 2>/dev/null || true)
            case "$other" in ''|*[!0-9]*) fail 'uncertain action lock; inspect before retrying' ;; esac
            if [ -r "/proc/$other/cmdline" ] && tr '\000' ' ' < "/proc/$other/cmdline" | grep -F "$MODDIR/service.sh" >/dev/null; then
                fail 'another add-on action is running'
            fi
            if ! rm -f "$LOCK/pid" || ! rmdir "$LOCK" || ! mkdir "$LOCK"; then fail 'cannot clear stale lock'; fi
        fi
        printf '%s\n' "$$" > "$LOCK/pid" || fail 'cannot write action lock'
        unlock() { rm -f "$LOCK/pid"; rmdir "$LOCK" 2>/dev/null || true; }
        trap 'unlock; exit 1' INT TERM
        trap unlock EXIT
        ;;
    worker) ;;
    *) fail 'unknown action' ;;
esac

case "$ACTION" in
    apply)
        verify_snapshot
        mock_gps || fail 'Manama mock GPS is not currently verified'
        if ! check_timezone "$ORIG_TZ" || ! check_timezone Asia/Bahrain; then fail 'timezone helper or original zone could not be validated'; fi
        : > "$MODDIR/touched/timezone" || fail 'cannot mark timezone'
        set_global auto_time_zone 0 || fail 'cannot disable automatic timezone'
        set_timezone Asia/Bahrain || fail 'system timezone service did not confirm Bahrain'
        : > "$MODDIR/touched/wifi" || fail 'cannot mark Wi-Fi setting'
        set_global wifi_scan_always_enabled 0 || fail 'cannot disable Wi-Fi scan-always'
        : > "$MODDIR/touched/ble" || fail 'cannot mark Bluetooth setting'
        set_global ble_scan_always_enabled 0 || fail 'cannot disable BLE scan-always'
        set_carrier || fail 'Bahrain property setup failed'
        printf '%s\n' 'PRIVACY_APPLY_OK'
        ;;
    disable)
        stop_worker || fail 'watcher could not be stopped'
        printf '%s\n' 'PRIVACY_DISABLE_OK'
        ;;
    restore)
        stop_worker || fail 'watcher could not be stopped'
        incomplete=0
        [ ! -f "$MODDIR/touched/ble" ] || restore_global ble_scan_always_enabled "$ORIG_BLE" || incomplete=1
        [ ! -f "$MODDIR/touched/wifi" ] || restore_global wifi_scan_always_enabled "$ORIG_WIFI" || incomplete=1
        if [ -f "$MODDIR/touched/timezone" ]; then
            current=$(getprop persist.sys.timezone)
            if [ "$MODE" = shared ] && ! base_matches; then
                printf '%s\n' 'PRIVACY_NOTE: Original Manama module changed; leaving timezone untouched.'
            else
                if [ "$current" = Asia/Bahrain ]; then
                    set_timezone "$ORIG_TZ" || incomplete=1
                elif [ "$current" != "$ORIG_TZ" ]; then
                    printf '%s\n' 'PRIVACY_NOTE: Timezone changed independently; leaving timezone untouched.'
                fi
                restore_global auto_time_zone "$ORIG_AUTO" || incomplete=1
            fi
        fi
        [ ! -f "$MODDIR/touched/carrier" ] || restore_carrier || incomplete=1
        [ "$incomplete" = 0 ] || fail 'restore incomplete; backup retained and module disabled'
        printf '%s\n' 'PRIVACY_RESTORE_OK'
        ;;
    worker)
        [ ! -f "$MODDIR/disable" ] || exit 0
        if [ -f "$MODDIR/worker.pid" ]; then
            pid=$(cat "$MODDIR/worker.pid" 2>/dev/null || true)
            case "$pid" in ''|*[!0-9]*) ;; *)
                if [ -r "/proc/$pid/cmdline" ] && tr '\000' ' ' < "/proc/$pid/cmdline" | grep -F "$MODDIR/service.sh" >/dev/null; then exit 0; fi ;;
            esac
        fi
        printf '%s\n' "$$" > "$MODDIR/worker.pid" || exit 1
        cleanup() { [ "$(cat "$MODDIR/worker.pid" 2>/dev/null || true)" != "$$" ] || rm -f "$MODDIR/worker.pid"; }
        trap 'cleanup; exit 0' TERM INT
        trap cleanup EXIT
        while [ "$(getprop sys.boot_completed)" != 1 ]; do [ ! -f "$MODDIR/disable" ] || exit 0; sleep 5; done
        last=''
        while [ ! -f "$MODDIR/disable" ]; do
            if ! jar_matches; then printf '%s\n' 'Timezone helper changed; watcher stopped.'; restore_carrier || true; exit 1; fi
            if [ "$MODE" = shared ] && ! base_matches; then printf '%s\n' 'Original Manama module changed; watcher stopped.'; exit 1; fi
            set_global auto_time_zone 0 || true
            set_global wifi_scan_always_enabled 0 || true
            set_global ble_scan_always_enabled 0 || true
            if [ "$(getprop persist.sys.timezone)" != Asia/Bahrain ]; then set_timezone Asia/Bahrain || true; fi
            if mock_gps && set_carrier; then
                [ "$last" = active ] || printf '%s\n' 'Manama mock GPS observed; add-on is active.'
                last=active
            else
                restore_carrier || true
                [ "$last" = paused ] || printf '%s\n' 'Mock GPS not verified; independent carrier spoof paused.'
                last=paused
            fi
            n=0
            while [ "$n" -lt 10 ] && [ ! -f "$MODDIR/disable" ]; do sleep 3; n=$((n + 1)); done
        done
        ;;
esac
