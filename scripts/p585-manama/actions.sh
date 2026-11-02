#!/system/bin/sh
# Root-side apply/restore of only the items recorded in the private snapshot.
set -u
umask 077
ACTION=${1:-}
MODDIR=${2:-}
STATE=${3:-}
TOKEN=${4:-}
PKG=io.appium.settings
LAT=26.223536
LON=50.586994
# Location.toString() uses the system locale. Accept decimal comma and common
# Persian/Arabic digit forms as well as the usual ASCII rendering.
COORD_PATTERN='(26[.,]223536,50[.,]586994|۲۶(٫|[.])۲۲۳۵۳۶,۵۰(٫|[.])۵۸۶۹۹۴|٢٦(٫|[.])٢٢٣٥٣٦,٥٠(٫|[.])٥٨٦٩٩٤)'
PROPS='gsm.sim.operator.numeric gsm.sim.operator.iso-country gsm.sim.operator.alpha gsm.operator.numeric gsm.operator.iso-country gsm.operator.alpha'

fail() { printf 'MANAMA_ERROR: %s\n' "$*" >&2; exit 1; }
[ "$MODDIR" = /data/adb/modules/p585_manama ] || fail 'unexpected module path'
case "$TOKEN" in p585-manama-????????) ;; *) fail 'bad backup token' ;; esac
[ "$STATE" = "/data/adb/p585-manama-backups/$TOKEN" ] || fail 'unexpected snapshot path'
[ -d "$MODDIR" ] && [ ! -L "$MODDIR" ] || fail 'missing module directory'
[ -d "$STATE" ] && [ ! -L "$STATE" ] || fail 'missing snapshot'
[ -d "$MODDIR/touched" ] && [ ! -L "$MODDIR/touched" ] || fail 'missing transaction markers'
[ "$(cat "$MODDIR/owner" 2>/dev/null)" = "$TOKEN" ] || fail 'module owner mismatch'
[ "$(cat "$STATE/token" 2>/dev/null)" = "$TOKEN" ] || fail 'snapshot token mismatch'
MAGISK=$(command -v magisk 2>/dev/null || true)
[ -n "$MAGISK" ] && [ -x "$MAGISK" ] || fail 'Magisk command unavailable'

# A host timeout/disconnect does not prove that an Android command stopped.
# Serialize apply and restore so a retry cannot overlap a still-running action.
LOCK="$MODDIR/action.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
    other=$(cat "$LOCK/pid" 2>/dev/null || true)
    case "$other" in ''|*[!0-9]*) fail 'action lock has no valid PID; inspect before retrying' ;; esac
    if [ -r "/proc/$other/cmdline" ] && tr '\000' ' ' < "/proc/$other/cmdline" | grep -F "$MODDIR/actions.sh" >/dev/null; then
        fail 'another apply or restore is still running; wait before retrying'
    fi
    if ! rm -f "$LOCK/pid" || ! rmdir "$LOCK" || ! mkdir "$LOCK"; then fail 'cannot clear stale action lock'; fi
fi
printf '%s\n' "$$" > "$LOCK/pid" || fail 'cannot record action lock'
release_lock() { rm -f "$LOCK/pid"; rmdir "$LOCK" 2>/dev/null || true; }
trap 'release_lock; exit 1' INT TERM
trap release_lock EXIT

read_snapshot() {
    ORIG_PROVIDERS=$(cat "$STATE/location_providers_allowed") || fail 'missing provider snapshot'
    ORIG_AUTO=$(cat "$STATE/auto_time_zone") || fail 'missing auto timezone snapshot'
    ORIG_TZ=$(cat "$STATE/timezone") || fail 'missing timezone snapshot'
    case "$ORIG_PROVIDERS" in *[!A-Za-z0-9_,.-]*) fail 'invalid original provider list' ;; esac
    case "$ORIG_AUTO" in 0|1|null) ;; *) fail 'invalid original automatic timezone setting' ;; esac
    case "$ORIG_TZ" in ''|*[!A-Za-z0-9_./+-]*) fail 'invalid original timezone' ;; esac
    for key in $PROPS; do
        [ -f "$STATE/props/$key.present" ] && [ -f "$STATE/props/$key.value" ] || fail "missing backup for $key"
        case "$(cat "$STATE/props/$key.present")" in 0|1) ;; *) fail "invalid backup for $key" ;; esac
    done
}

prop_present() { getprop | grep -F "[$1]:" >/dev/null; }
verify_unchanged() {
    [ "$(settings --user 0 get secure location_providers_allowed)" = "$ORIG_PROVIDERS" ] || fail 'location setting changed since backup'
    [ "$(settings --user 0 get global auto_time_zone)" = "$ORIG_AUTO" ] || fail 'auto timezone changed since backup'
    [ "$(getprop persist.sys.timezone)" = "$ORIG_TZ" ] || fail 'timezone changed since backup'
    for key in $PROPS; do
        present=$(cat "$STATE/props/$key.present")
        if [ "$present" = 1 ]; then
            prop_present "$key" || fail "$key disappeared since backup"
            [ "$(getprop "$key")" = "$(cat "$STATE/props/$key.value")" ] || fail "$key changed since backup"
        else
            ! prop_present "$key" || fail "$key appeared since backup"
        fi
    done
}
mock_is_active() {
    dump=$(dumpsys location 2>/dev/null) || return 1
    printf '%s\n' "$dump" | grep -F 'Mock Providers:' >/dev/null || return 1
    printf '%s\n' "$dump" | grep -E "Location\\[gps $COORD_PATTERN .*mock" >/dev/null || return 1
    printf '%s\n' "$dump" | grep -E "Location\\[network $COORD_PATTERN .*mock" >/dev/null || return 1
}
set_provider() {
    name=$1 enabled=$2
    if [ "$enabled" = yes ]; then toggle=+$name; else toggle=-$name; fi
    settings --user 0 put secure location_providers_allowed "$toggle" || return 1
    val=$(settings --user 0 get secure location_providers_allowed) || return 1
    case ",$val," in
        *",$name,"*) [ "$enabled" = yes ] ;;
        *) [ "$enabled" = no ] ;;
    esac
}
set_timezone() {
    # Avoid a raw Binder transaction number: Samsung may use a different
    # IAlarmManager layout. The property is persistent; running apps may need
    # a restart even after the broadcast.
    setprop persist.sys.timezone "$1" || return 1
    [ "$(getprop persist.sys.timezone)" = "$1" ] || return 1
    am broadcast -a android.intent.action.TIMEZONE_CHANGED --es time-zone "$1" >/dev/null 2>&1 || true
}
restore_property() {
    key=$1 spoofed=$2
    # Do not overwrite a newer value published by the radio or another actor.
    if [ "$(getprop "$key")" != "$spoofed" ]; then
        printf 'MANAMA_NOTE: %s changed independently; preserving its current value.\n' "$key"
        return 0
    fi
    present=$(cat "$STATE/props/$key.present") || return 1
    if [ "$present" = 1 ]; then
        value=$(cat "$STATE/props/$key.value") || return 1
        "$MAGISK" resetprop -n "$key" "$value" || return 1
        [ "$(getprop "$key")" = "$value" ] && prop_present "$key"
    else
        if prop_present "$key"; then "$MAGISK" resetprop --delete "$key" || return 1; fi
        ! prop_present "$key"
    fi
}
stop_worker() {
    : > "$MODDIR/disable" || return 1
    pid=$(cat "$MODDIR/worker.pid" 2>/dev/null || true)
    case "$pid" in
        ''|*[!0-9]*) return 0 ;;
        *)
            if [ -r "/proc/$pid/cmdline" ] && tr '\000' ' ' < "/proc/$pid/cmdline" | grep -F "$MODDIR/service.sh" >/dev/null; then
                kill "$pid" 2>/dev/null || return 1
                n=0
                while [ -r "/proc/$pid/cmdline" ] && tr '\000' ' ' < "/proc/$pid/cmdline" | grep -F "$MODDIR/service.sh" >/dev/null; do
                    [ "$n" -lt 20 ] || return 1
                    sleep 1
                    n=$((n + 1))
                done
            fi ;;
    esac
}

read_snapshot
case "$ACTION" in
    disable)
        stop_worker || fail 'worker could not be stopped'
        printf '%s\n' 'MANAMA_DISABLE_OK'
        ;;
    apply)
        [ ! -e "$MODDIR/disable" ] || fail 'module is disabled'
        verify_unchanged
        # Mark before each category of writes so rollback never overwrites a
        # changed radio setting if the pre-write snapshot check failed.
        : > "$MODDIR/touched/service" || fail 'cannot mark helper changes'
        pm grant --user 0 "$PKG" android.permission.ACCESS_FINE_LOCATION || fail 'fine location permission failed'
        pm grant --user 0 "$PKG" android.permission.ACCESS_COARSE_LOCATION || fail 'coarse location permission failed'
        appops set --user 0 "$PKG" android:mock_location allow || fail 'mock-location appop failed'
        appops get --user 0 "$PKG" android:mock_location | grep -E 'MOCK_LOCATION: allow' >/dev/null || fail 'mock-location appop was not granted'
        appops write-settings >/dev/null || fail 'could not persist mock-location appop'
        started=$(am start-foreground-service --user 0 -n "$PKG/.LocationService" \
            --es latitude "$LAT" --es longitude "$LON" --es altitude 5.0 2>&1) || fail "cannot start mock service: $started"
        case "$started" in *'Starting service:'*) ;; *) fail "mock service did not start: $started" ;; esac
        n=0
        until mock_is_active; do
            [ "$n" -lt 20 ] || fail 'could not verify both Manama mock providers'
            sleep 1
            n=$((n + 1))
        done
        # Enable the *test* providers for location clients only after they replace
        # the normal Android providers. Other location channels may still leak.
        : > "$MODDIR/touched/providers" || fail 'cannot mark provider changes'
        set_provider gps yes || fail 'could not enable mock GPS provider'
        set_provider network yes || fail 'could not enable mock network provider'
        : > "$MODDIR/touched/timezone" || fail 'cannot mark timezone changes'
        settings --user 0 put global auto_time_zone 0 || fail 'could not disable auto timezone'
        [ "$(settings --user 0 get global auto_time_zone)" = 0 ] || fail 'auto timezone did not change'
        set_timezone Asia/Bahrain || fail 'could not set Bahrain timezone'
        printf '%s\n' 'MANAMA_APPLY_OK'
        ;;
    restore)
        stop_worker || fail 'worker did not stop; no settings were restored'
        incomplete=0
        if [ -f "$MODDIR/touched/service" ]; then
            # Stop the foreground service while its mock appop is still allowed.
            am stopservice --user 0 -n "$PKG/.LocationService" >/dev/null 2>&1 || true
            n=0
            while :; do
                dump=$(dumpsys location 2>/dev/null) || fail 'cannot inspect location providers during restore'
                if ! printf '%s\n' "$dump" | grep -F 'Mock Providers:' >/dev/null; then break; fi
                [ "$n" -lt 10 ] || break
                sleep 1
                n=$((n + 1))
            done
            if printf '%s\n' "$dump" | grep -F 'Mock Providers:' >/dev/null; then
                printf '%s\n' 'MANAMA_WARNING: A mock provider remains; reboot and retry restore.' >&2
                incomplete=1
            fi
            # Appium asks Play services to leave fused mock mode asynchronously.
            # Give that request a moment; cached app locations may still remain.
            sleep 2
        fi
        if [ -f "$MODDIR/touched/providers" ]; then
            case ",$ORIG_PROVIDERS," in *,gps,*) gps=yes ;; *) gps=no ;; esac
            case ",$ORIG_PROVIDERS," in *,network,*) net=yes ;; *) net=no ;; esac
            set_provider gps "$gps" || incomplete=1
            set_provider network "$net" || incomplete=1
        fi
        if [ -f "$MODDIR/touched/timezone" ]; then
            settings --user 0 put global auto_time_zone 0 || incomplete=1
            set_timezone "$ORIG_TZ" || incomplete=1
            if [ "$ORIG_AUTO" = null ]; then
                settings --user 0 delete global auto_time_zone || incomplete=1
            else
                settings --user 0 put global auto_time_zone "$ORIG_AUTO" || incomplete=1
            fi
            [ "$(settings --user 0 get global auto_time_zone)" = "$ORIG_AUTO" ] || incomplete=1
        fi
        if [ -f "$MODDIR/touched/carrier" ]; then
            restore_property gsm.sim.operator.numeric 42601 || incomplete=1
            restore_property gsm.sim.operator.iso-country bh || incomplete=1
            restore_property gsm.sim.operator.alpha Batelco || incomplete=1
            restore_property gsm.operator.numeric 42601 || incomplete=1
            restore_property gsm.operator.iso-country bh || incomplete=1
            restore_property gsm.operator.alpha Batelco || incomplete=1
        fi
        [ "$incomplete" = 0 ] || fail 'restore incomplete; backup retained and module disabled'
        printf '%s\n' 'MANAMA_RESTORE_OK'
        ;;
    *) fail 'unknown action' ;;
esac
