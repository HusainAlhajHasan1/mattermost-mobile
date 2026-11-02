#!/usr/bin/env bash
# Companion to an existing Manama mock; never changes the actual SIM, radio or IP.
set -Eeuo pipefail
umask 077
readonly REPO='HusainAlhajHasan1/mattermost-mobile'
readonly SERIAL_SHA='072734241f69cfc548d138f9a496f532972e476926cb69e054ebda981851728d'
readonly BASE="${P585_WORKDIR:-/root/root-p585}"
readonly MOD='/data/adb/modules/p585_manama_privacy'
readonly BASEMOD='/data/adb/modules/p585_manama'
readonly PHONE_BACKUPS='/data/adb/p585-privacy-backups'
readonly KNOWN_BASE_SHA='2e1f81629eaafee0c88792a133f3d46eac4f8e076b446032ba95a9a40d091da6'
readonly SERVICE_BLOB='144147e022802270d630456cdf201825f9e05866'
readonly SERVICE_SHA='6b4a9957a8440027ab5b808a24820852d6dc815849c69282f2fea096e9c8e90d'
readonly TZ_BLOB='3e6676c0f64fa6406a13ddf65f581dab5ba33bd2'
readonly TZ_SHA='efd506197c5feb3052f7777d0bcf6ca02debb50aa2eb173af95c8352efd4d9ab'
readonly -a PROPS=(gsm.sim.operator.numeric gsm.sim.operator.iso-country gsm.sim.operator.alpha gsm.operator.numeric gsm.operator.iso-country gsm.operator.alpha)
SERIAL=${P585_SERIAL:-}; RUN_DIR=''; TOKEN=''; DEVICE_STATE=''; STAGE=''
STAGE_CREATED=0; MODULE_CREATED=0; APPLY_STARTED=0; COMPLETE=0; IN_CLEANUP=0
ADB=()
say() { printf '%s\n' "$*"; }
warn() { printf '%s\n' "$*" >&2; }
fail() { warn "STOP: $*"; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "Missing host command: $1"; }
adb_text() { timeout 35 "${ADB[@]}" "$@" | tr -d '\r'; }
root_script() {
    local body=$1 out
    if ! out=$(printf '%s\n%s\n%s\n' 'set -e' "$body" 'printf "\nPRIVACY_REMOTE_OK\n"' |
        timeout 180 "${ADB[@]}" shell 'su -c sh' 2>&1 | tr -d '\r'); then
        warn "Root command failed: $out"; return 1
    fi
    case "$out" in
        PRIVACY_REMOTE_OK) ;;
        *$'\n'PRIVACY_REMOTE_OK) printf '%s\n' "${out%$'\n'PRIVACY_REMOTE_OK}" ;;
        *) warn "Root command did not confirm completion: $out"; return 1 ;;
    esac
}
validate_serial() {
    local hash
    [[ -n "$SERIAL" && "$SERIAL" =~ ^[[:alnum:]]+$ ]] || fail 'Set P585_SERIAL to the tablet serial.'
    hash=$(printf '%s' "$SERIAL" | sha256sum); hash=${hash%% *}
    [[ "$hash" == "$SERIAL_SHA" ]] || fail 'This script is guarded for the identified tablet only.'
    ADB=(adb -s "$SERIAL")
}
check_device() {
    local model product build sdk boot user rootinfo magisk state
    state=$(adb_text get-state) || fail 'ADB cannot reach the tablet.'
    [[ "$state" == device ]] || fail "Boot and authorize Android first (ADB: $state)."
    model=$(adb_text shell getprop ro.product.model); product=$(adb_text shell getprop ro.product.device)
    build=$(adb_text shell getprop ro.build.version.incremental); sdk=$(adb_text shell getprop ro.build.version.sdk)
    boot=$(adb_text shell getprop sys.boot_completed); user=$(adb_text shell am get-current-user)
    [[ "$model" == SM-P585 && "$product" == gtanotexllte && "$build" == P585JXS3CTL1 && "$sdk" == 27 ]] || fail 'Device or Android build does not match.'
    [[ "$boot" == 1 && "$user" == 0 ]] || fail 'Wait for Android owner user 0 to finish booting.'
    rootinfo=$(adb_text shell 'su -c id') || fail 'ADB shell did not receive Magisk root.'
    [[ "$rootinfo" == uid=0\(* ]] || fail 'ADB shell is not root on the tablet.'
    magisk=$(root_script 'command -v magisk; magisk --list' || true)
    [[ "$magisk" == *resetprop* ]] || fail 'Magisk resetprop is unavailable.'
    root_script 'command -v sha256sum >/dev/null; command -v settings >/dev/null; command -v app_process >/dev/null; command -v am >/dev/null' >/dev/null || fail 'An Android command is unavailable.'
}
fetch() {
    local blob=$1 expected=$2 dest=$3
    curl -4 -fsSL --retry 3 --connect-timeout 20 --max-time 120 \
        -H 'Accept: application/vnd.github.raw+json' -H 'X-GitHub-Api-Version: 2022-11-28' \
        "https://api.github.com/repos/$REPO/git/blobs/$blob" -o "$dest" || fail 'Cannot download an add-on helper.'
    printf '%s  %s\n' "$expected" "$dest" | sha256sum -c - >/dev/null || fail 'Add-on helper checksum mismatch.'
}
check_remote() {
    local remote=$1 file=$2 out expected
    out=$(root_script "if [ -f '$remote' ] && [ ! -L '$remote' ]; then sha256sum '$remote'; else exit 1; fi") || return 1
    [[ "$out" =~ ^([0-9a-f]{64})[[:space:]] ]] || return 1
    expected=$(sha256sum "$file"); expected=${expected%% *}
    [[ "${BASH_REMATCH[1]}" == "$expected" ]]
}
probe_install() {
    local result hash owner
    result=$(root_script "if [ -e '$MOD' ] || [ -L '$MOD' ]; then echo PRIVACY_EXISTS; else echo PRIVACY_CLEAR; fi") || fail 'Cannot inspect the add-on module.'
    [[ "$result" == PRIVACY_CLEAR ]] || fail 'The add-on already exists. Restore it before installing again.'
    # Read-only inspection; no need to install or replace the existing mock app.
    # shellcheck disable=SC2016
    result=$(root_script 'd=$(dumpsys location 2>/dev/null); if printf "%s\n" "$d" | grep -F "Mock Providers:" >/dev/null && printf "%s\n" "$d" | grep -E "Location\\[gps (26[.,]223536,50[.,]586994|۲۶(٫|[.])۲۲۳۵۳۶,۵۰(٫|[.])۵۸۶۹۹۴|٢٦(٫|[.])٢٢٣٥٣٦,٥٠(٫|[.])٥٨٦٩٩٤) .*mock" >/dev/null; then echo PRIVACY_GPS_OK; else echo PRIVACY_GPS_MISSING; fi') || fail 'Cannot inspect the current mock GPS.'
    [[ "$result" == PRIVACY_GPS_OK ]] || fail 'Manama mock GPS was not verified. Start it first; nothing was changed.'
    result=$(root_script "if [ ! -e '$BASEMOD' ] && [ ! -L '$BASEMOD' ]; then echo none; elif [ -d '$BASEMOD' ] && [ ! -L '$BASEMOD' ] && [ ! -f '$BASEMOD/disable' ] && [ -f '$BASEMOD/service.sh' ]; then printf 'shared '; cat '$BASEMOD/owner'; sha256sum '$BASEMOD/service.sh'; else echo unknown; fi") || fail 'Cannot inspect the existing Manama module.'
    if [[ "$result" == none ]]; then CARRIER_MODE=independent; BASE_OWNER=none
    else
        owner=${result%%$'\n'*}; owner=${owner#shared }
        hash=${result#*$'\n'}; hash=${hash%% *}
        [[ "$result" == shared\ * && "$owner" =~ ^p585-manama-[[:alnum:]]{8}$ && "$hash" == "$KNOWN_BASE_SHA" ]] || fail 'An unrecognized or disabled Manama module exists; refusing to interfere.'
        CARRIER_MODE=shared; BASE_OWNER=$owner
    fi
}
write_snapshot() {
    local key value present line all setting
    mkdir -p "$RUN_DIR/state/props"
    printf '%s\n' "$SERIAL" > "$RUN_DIR/state/serial"
    printf '%s\n' "$TOKEN" > "$RUN_DIR/state/token"
    printf '%s\n' "$CARRIER_MODE" > "$RUN_DIR/state/carrier_mode"
    printf '%s\n' "$BASE_OWNER" > "$RUN_DIR/state/base_owner"
    value=$(adb_text shell getprop persist.sys.timezone) || fail 'Cannot back up timezone.'
    [[ "$value" =~ ^[A-Za-z0-9_./+\-]+$ ]] || fail 'Unexpected or empty timezone.'
    printf '%s\n' "$value" > "$RUN_DIR/state/timezone"
    for setting in auto_time_zone wifi_scan_always_enabled ble_scan_always_enabled; do
        value=$(adb_text shell settings --user 0 get global "$setting") || fail "Cannot back up $setting."
        [[ "$value" == 0 || "$value" == 1 || "$value" == null ]] || fail "Unexpected $setting value."
        printf '%s\n' "$value" > "$RUN_DIR/state/$setting"
    done
    all=$(adb_text shell getprop) || fail 'Cannot back up carrier properties.'
    for key in "${PROPS[@]}"; do
        line=$(printf '%s\n' "$all" | awk -v key="$key" 'index($0, "[" key "]: [") == 1 { print }')
        if [[ -z "$line" ]]; then present=0; value=''
        else
            [[ "$line" != *$'\n'* && "$line" == "[$key]: ["*']' ]] || fail "Unexpected $key output."
            present=1; value=${line#"[$key]: ["}; value=${value%]}
            [[ "$value" != *$'\r'* && "$value" != *$'\n'* ]] || fail "Unsafe $key output."
        fi
        printf '%s\n' "$present" > "$RUN_DIR/state/props/$key.present"
        printf '%s\n' "$value" > "$RUN_DIR/state/props/$key.value"
    done
}
make_manifest() {
    (cd "$RUN_DIR/state" && sha256sum serial token carrier_mode base_owner timezone auto_time_zone wifi_scan_always_enabled ble_scan_always_enabled tz_sha256 props/*) > "$RUN_DIR/state.sha256" || fail 'Cannot checksum snapshot.'
    (cd "$RUN_DIR/state" && sha256sum -c ../state.sha256 >/dev/null) || fail 'Snapshot checksum failed.'
}
stage_and_copy() {
    local result file key
    result=$(adb_text shell "if [ ! -e '$STAGE' ] && [ ! -L '$STAGE' ] && mkdir '$STAGE' && chmod 700 '$STAGE' && printf '%s\\n' '$TOKEN' > '$STAGE/owner'; then echo PRIVACY_STAGE_OK; else echo PRIVACY_STAGE_ERROR; fi") || fail 'Cannot create staging directory.'
    [[ "$result" == PRIVACY_STAGE_OK ]] || fail 'Staging path exists or cannot be created.'
    STAGE_CREATED=1
    result=$(adb_text shell "if mkdir '$STAGE/state' '$STAGE/state/props'; then echo PRIVACY_DIRS_OK; else echo PRIVACY_DIRS_ERROR; fi") || fail 'Cannot prepare staging.'
    [[ "$result" == PRIVACY_DIRS_OK ]] || fail 'Cannot prepare staging directories.'
    for file in serial token carrier_mode base_owner timezone auto_time_zone wifi_scan_always_enabled ble_scan_always_enabled tz_sha256; do
        timeout 40 "${ADB[@]}" push "$RUN_DIR/state/$file" "$STAGE/state/$file" >/dev/null || fail "Cannot stage $file."
        check_remote "$STAGE/state/$file" "$RUN_DIR/state/$file" || fail "Readback failed: $file."
    done
    for key in "${PROPS[@]}"; do
        for file in "$key.present" "$key.value"; do
            timeout 40 "${ADB[@]}" push "$RUN_DIR/state/props/$file" "$STAGE/state/props/$file" >/dev/null || fail "Cannot stage $file."
            check_remote "$STAGE/state/props/$file" "$RUN_DIR/state/props/$file" || fail "Readback failed: $file."
        done
    done
    for file in service.sh tzcontrol.jar; do
        timeout 40 "${ADB[@]}" push "$RUN_DIR/$file" "$STAGE/$file" >/dev/null || fail "Cannot stage $file."
        check_remote "$STAGE/$file" "$RUN_DIR/$file" || fail "Readback failed: $file."
    done
    MODULE_CREATED=1
    root_script "
        [ ! -e '$MOD' ] && [ ! -L '$MOD' ] || exit 1
        if [ -e '$PHONE_BACKUPS' ] || [ -L '$PHONE_BACKUPS' ]; then [ -d '$PHONE_BACKUPS' ] && [ ! -L '$PHONE_BACKUPS' ] || exit 1; else mkdir '$PHONE_BACKUPS'; chmod 700 '$PHONE_BACKUPS'; fi
        [ ! -e '$DEVICE_STATE' ] && [ ! -L '$DEVICE_STATE' ] || exit 1
        mkdir '$DEVICE_STATE'; chmod 700 '$DEVICE_STATE'; cp -R '$STAGE/state/.' '$DEVICE_STATE/'; chmod -R go-rwx '$DEVICE_STATE'
        mkdir '$MOD'; printf '%s\\n' '$TOKEN' > '$MOD/owner'; : > '$MOD/disable'
        mkdir '$MOD/touched'; cp '$STAGE/service.sh' '$MOD/service.sh'; cp '$STAGE/tzcontrol.jar' '$MOD/tzcontrol.jar'
        printf '%s\\n' 'id=p585_manama_privacy' 'name=Manama timezone and scan controls' 'version=1.0' 'versionCode=1' 'author=Local setup' 'description=Reversible timezone and scan-always settings' > '$MOD/module.prop'
        chmod 700 '$MOD' '$MOD/touched' '$MOD/service.sh'; chmod 600 '$MOD/owner' '$MOD/disable' '$MOD/tzcontrol.jar' '$MOD/module.prop'
    " >/dev/null || fail 'Could not copy backup and create the guarded module.'
    verify_device
}
verify_host() {
    local file token
    [[ -d "$RUN_DIR/state" && -f "$RUN_DIR/state.sha256" ]] || fail 'Host backup is missing.'
    (cd "$RUN_DIR/state" && sha256sum -c ../state.sha256 >/dev/null) || fail 'Host backup checksum mismatch.'
    for file in service.sh tzcontrol.jar; do
        if [[ "$file" == service.sh ]]; then token=$SERVICE_SHA; else token=$TZ_SHA; fi
        printf '%s  %s\n' "$token" "$RUN_DIR/$file" | sha256sum -c - >/dev/null || fail "Saved $file checksum mismatch."
    done
    TOKEN=$(cat "$RUN_DIR/state/token"); [[ "$TOKEN" =~ ^p585-privacy-[[:alnum:]]{8}$ ]] || fail 'Invalid backup token.'
    [[ "$(cat "$RUN_DIR/state/serial")" == "$SERIAL" ]] || fail 'Backup belongs to another device.'
    DEVICE_STATE="$PHONE_BACKUPS/$TOKEN"; STAGE="/data/local/tmp/$TOKEN"
}
verify_device() {
    local file key owner
    owner=$(root_script "[ -d '$MOD' ] && [ ! -L '$MOD' ]; cat '$MOD/owner'") || fail 'Matching module is missing.'
    [[ "$owner" == "$TOKEN" ]] || fail 'Module owner does not match this backup.'
    for file in serial token carrier_mode base_owner timezone auto_time_zone wifi_scan_always_enabled ble_scan_always_enabled tz_sha256; do
        check_remote "$DEVICE_STATE/$file" "$RUN_DIR/state/$file" || fail "Device snapshot differs: $file."
    done
    for key in "${PROPS[@]}"; do
        for file in "$key.present" "$key.value"; do check_remote "$DEVICE_STATE/props/$file" "$RUN_DIR/state/props/$file" || fail "Device snapshot differs: $file."; done
    done
    check_remote "$MOD/service.sh" "$RUN_DIR/service.sh" || fail 'Device service script differs.'
    check_remote "$MOD/tzcontrol.jar" "$RUN_DIR/tzcontrol.jar" || fail 'Device timezone helper differs.'
}
restore_device() {
    local result
    result=$(root_script "sh '$MOD/service.sh' restore") || return 1
    [[ "$result" == *PRIVACY_RESTORE_OK* ]] || { warn "Restore not confirmed: $result"; return 1; }
    [[ "$result" != *PRIVACY_NOTE:* ]] || warn 'Some settings had changed independently and were left alone.'
    root_script "if [ -d '$MOD' ] && [ ! -L '$MOD' ] && [ \"\$(cat '$MOD/owner' 2>/dev/null)\" = '$TOKEN' ]; then rm -rf '$MOD'; else exit 1; fi; [ ! -e '$MOD' ] && [ ! -L '$MOD' ]" >/dev/null
}
clean_stage() {
    (( STAGE_CREATED == 1 )) || return 0
    root_script "if [ -d '$STAGE' ] && [ ! -L '$STAGE' ] && [ \"\$(cat '$STAGE/owner' 2>/dev/null)\" = '$TOKEN' ]; then rm -rf '$STAGE'; fi" >/dev/null || true
}
on_exit() {
    local rc=$?
    (( IN_CLEANUP == 0 )) || return
    IN_CLEANUP=1; set +e
    if (( rc != 0 && COMPLETE == 0 && MODULE_CREATED == 1 )); then
        warn 'Setup did not complete. Attempting to disable and restore the add-on...'
        if (( APPLY_STARTED == 1 )); then
            if restore_device; then warn 'Add-on restore completed.'; else warn "Restore incomplete. Keep the backup and retry: bash '$RUN_DIR/tool.sh' restore"; fi
        else
            root_script "if [ -d '$MOD' ] && [ ! -L '$MOD' ] && [ \"\$(cat '$MOD/owner' 2>/dev/null)\" = '$TOKEN' ]; then rm -rf '$MOD'; else exit 1; fi" >/dev/null || warn 'Could not confirm partial module cleanup.'
        fi
    fi
    clean_stage
    return "$rc"
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
status_device() {
    root_script "
        CLASSPATH='$MOD/tzcontrol.jar' app_process /system/bin local.manama.TzControl read
        printf 'persist.sys.timezone='; getprop persist.sys.timezone
        for k in auto_time_zone wifi_scan_always_enabled ble_scan_always_enabled; do printf '%s=' \"\$k\"; settings --user 0 get global \"\$k\"; done
        for k in gsm.sim.operator.numeric gsm.sim.operator.iso-country gsm.sim.operator.alpha gsm.operator.numeric gsm.operator.iso-country gsm.operator.alpha; do printf '%s=' \"\$k\"; getprop \"\$k\"; done
        p=\$(cat '$MOD/worker.pid' 2>/dev/null || true); case \"\$p\" in ''|*[!0-9]*) echo PRIVACY_WATCHER_MISSING;; *) if [ -r \"/proc/\$p/cmdline\" ] && tr '\\000' ' ' < \"/proc/\$p/cmdline\" | grep -F '$MOD/service.sh' >/dev/null; then echo PRIVACY_WATCHER_ACTIVE; else echo PRIVACY_WATCHER_MISSING; fi;; esac
    "
}
main() {
    local mode=${1:-install} self result n file
    for x in adb timeout sha256sum tr grep awk; do need "$x"; done
    case "$mode" in install|restore|status) ;; *) fail 'Usage: tool.sh [install|status|restore]';; esac
    if [[ "$mode" != install ]]; then RUN_DIR=$(cd -- "$(dirname -- "$0")" && pwd -P); [[ -n "$SERIAL" ]] || SERIAL=$(cat "$RUN_DIR/state/serial" 2>/dev/null || true); fi
    validate_serial; check_device
    if [[ "$mode" != install ]]; then
        verify_host; verify_device
        if [[ "$mode" == status ]]; then status_device || fail 'Status could not be read.'; COMPLETE=1; return; fi
        say 'Restoring the add-on settings; the original mock app is not removed...'
        restore_device || fail 'Restore incomplete; reconnect and retry using this same backup.'
        COMPLETE=1; say "Add-on restore complete. Private backups remain at $RUN_DIR and $DEVICE_STATE."; return
    fi
    for x in curl mktemp cp chmod; do need "$x"; done
    probe_install
    mkdir -p "$BASE"; RUN_DIR=$(mktemp -d "$BASE/p585-privacy-XXXXXXXX") || fail 'Cannot create private backup.'; chmod 700 "$RUN_DIR"
    TOKEN=${RUN_DIR##*/}; [[ "$TOKEN" =~ ^p585-privacy-[[:alnum:]]{8}$ ]] || fail 'Unexpected backup directory name.'
    DEVICE_STATE="$PHONE_BACKUPS/$TOKEN"; STAGE="/data/local/tmp/$TOKEN"
    self=$(readlink -f -- "$0") || fail 'Cannot locate this script.'
    cp "$self" "$RUN_DIR/tool.sh"; chmod 700 "$RUN_DIR/tool.sh"
    say "Backing up timezone, scan settings and six carrier properties to $RUN_DIR ..."
    write_snapshot
    fetch "$SERVICE_BLOB" "$SERVICE_SHA" "$RUN_DIR/service.sh"
    fetch "$TZ_BLOB" "$TZ_SHA" "$RUN_DIR/tzcontrol.jar"
    printf '%s\n' "$TZ_SHA" > "$RUN_DIR/state/tz_sha256"
    make_manifest
    cat <<'WARN'

This add-on will set the Android timezone to Asia/Bahrain, turn automatic
 timezone off, and disable Wi-Fi/Bluetooth SCAN-ALWAYS settings. Wi-Fi stays on.
 Scan-always only limits scans while the corresponding radio is off; connected
 Wi-Fi and apps with permission may still scan and reveal nearby networks.
 Your internet IP is unchanged. GPS mocks can be detected and may stop.
 Bahrain operator properties do not create a SIM: apps may still see NO SIM.
 Carrier-property changes can affect calls, data, and emergency behavior.
 Keep backups private. The add-on retries after boot; this is not guaranteed.
WARN
    say 'A verified second copy of the backup will be stored on the tablet before settings change.'
    printf 'Type APPLY to continue, or press Enter to cancel: '; IFS= read -r answer || answer=''
    [[ "$answer" == APPLY ]] || fail 'Cancelled; no tablet settings were changed.'
    stage_and_copy
    APPLY_STARTED=1
    result=$(root_script "sh '$MOD/service.sh' apply") || fail 'Could not apply the add-on.'
    [[ "$result" == *PRIVACY_APPLY_OK* ]] || fail 'Add-on apply was not confirmed.'
    root_script "rm -f '$MOD/disable'; sh '$MOD/service.sh' </dev/null >'$MOD/service.log' 2>&1 &" >/dev/null || fail 'Could not start add-on watcher.'
    n=0
    while :; do
        result=$(status_device || true)
        if [[ "$result" == *'MANAMA_TZ_DEFAULT=Asia/Bahrain'* && "$result" == *'persist.sys.timezone=Asia/Bahrain'* && "$result" == *'auto_time_zone=0'* && "$result" == *'wifi_scan_always_enabled=0'* && "$result" == *'ble_scan_always_enabled=0'* && "$result" == *PRIVACY_WATCHER_ACTIVE* ]]; then break; fi
        (( n < 20 )) || fail 'Timezone, scanning flags or watcher could not be verified.'
        sleep 2; n=$((n + 1))
    done
    COMPLETE=1; clean_stage; STAGE=''
    say ''; say 'Add-on active. Current readback:'; say "$result"
    if [[ "$result" != *'gsm.sim.operator.numeric=42601'* || "$result" != *'gsm.sim.operator.iso-country=bh'* || "$result" != *'gsm.sim.operator.alpha=Batelco'* || "$result" != *'gsm.operator.numeric=42601'* || "$result" != *'gsm.operator.iso-country=bh'* || "$result" != *'gsm.operator.alpha=Batelco'* ]]; then
        warn 'Bahrain carrier properties were not all observed. The radio may overwrite them; no real SIM was created.'
    fi
    say "Private backup: $RUN_DIR"
    say "Status:  bash '$RUN_DIR/tool.sh' status"
    say "Restore: bash '$RUN_DIR/tool.sh' restore"
    [[ "$CARRIER_MODE" != shared ]] || say 'Restore this add-on BEFORE restoring the original Manama module.'
    say 'Connected Wi-Fi, IP, caches and apps may still reveal your real location.'
}
main "$@"
