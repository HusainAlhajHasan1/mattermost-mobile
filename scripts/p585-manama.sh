#!/usr/bin/env bash
# Guarded, reversible Manama mock-location/SIM-property setup for this SM-P585.
# One host terminal; never touches EFS, the modem, IMEI, partitions, or /system.
set -Eeuo pipefail
umask 077

readonly SERIAL_SHA256='072734241f69cfc548d138f9a496f532972e476926cb69e054ebda981851728d'
readonly BASE="${P585_WORKDIR:-/root/root-p585}"
readonly PKG='io.appium.settings'
readonly MOD='/data/adb/modules/p585_manama'
readonly PHONE_BACKUPS='/data/adb/p585-manama-backups'
readonly REPO='HusainAlhajHasan1/mattermost-mobile'
readonly APPIUM_TGZ_SHA='b09f0f2fde18290976eef5bc1d8c99a62479b140d25f777263d6fd42178961d0'
readonly APPIUM_APK_SHA='cfe14349395d7b22aa9cb2d0e4f32618aa4b5d02bb823a35a11da6d66fe4467a'
readonly APPIUM_LICENSE_SHA='ae9194687e7f3b7d85e8eeb5c973e22d52c4653b1e4332fa0e5c6e82b038ab29'
readonly NOTICE_BLOB='6158b547d653bd820a7b7c03ad6a9aa2c9aa0113'
readonly NOTICE_SHA='e5fbe88a4b1367dfff7908d4a3f9b3c551c1d882819f43df343d2e7ca5a56fa5'
readonly REPACK_BLOB='8163c44730cdfb904547f110aa5cd8cd2123d800'
readonly REPACK_SHA='c2723e3ada945ad37cddd85bf21a1b21c4b48f6bca874a277d12fceb5a8e4cfe'
readonly ACTIONS_BLOB='68b9c2f021df5fae5b85dc79b6765a128a0cff76'
readonly ACTIONS_SHA='8ff5dee74fdffe25aed1bcc8f5673f0aa724a6b8962cfdabd6cee4b78e149836'
readonly SERVICE_BLOB='419eff3ca1f85353c6801a9075447907413655f4'
readonly SERVICE_SHA='2e1f81629eaafee0c88792a133f3d46eac4f8e076b446032ba95a9a40d091da6'
readonly -a PROPS=(gsm.sim.operator.numeric gsm.sim.operator.iso-country gsm.sim.operator.alpha
    gsm.operator.numeric gsm.operator.iso-country gsm.operator.alpha)
SERIAL="${P585_SERIAL:-}"
RUN_DIR=''
TOKEN=''
DEVICE_STATE=''
STAGE=''
MODULE_CREATED=0
STAGE_CREATED=0
PACKAGE_MAY_EXIST=0
APPLY_STARTED=0
COMPLETE=0
IN_CLEANUP=0
ADB=()

say() { printf '%s\n' "$*"; }
warn() { printf '%s\n' "$*" >&2; }
fail() { warn "STOP: $*"; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "Missing host command: $1"; }
adb_text() { timeout 35 "${ADB[@]}" "$@" | tr -d '\r'; }

# Commands sent to Android's root shell must emit a marker. This avoids trusting
# ADB's exit status when a remote command fails or an older adbd masks it.
root_script() {
    local body=$1 out
    if ! out=$(printf '%s\n%s\n%s\n' 'set -e' "$body" 'printf "\nMANAMA_REMOTE_OK\n"' |
        timeout 180 "${ADB[@]}" shell 'su -c sh' 2>&1 | tr -d '\r'); then
        warn "Root ADB command failed: $out"
        return 1
    fi
    case "$out" in
        MANAMA_REMOTE_OK) ;;
        *$'\n'MANAMA_REMOTE_OK) printf '%s\n' "${out%$'\n'MANAMA_REMOTE_OK}" ;;
        *) warn "Root ADB command did not confirm completion: $out"; return 1 ;;
    esac
}

validate_serial() {
    local hash
    [[ -n "$SERIAL" && "$SERIAL" =~ ^[[:alnum:]]+$ ]] || fail 'Set P585_SERIAL to the tablet serial.'
    hash=$(printf '%s' "$SERIAL" | sha256sum); hash=${hash%% *}
    [[ "$hash" == "$SERIAL_SHA256" ]] || fail 'This script is guarded for the previously identified tablet only.'
    ADB=(adb -s "$SERIAL")
}

check_device() {
    local state model product build sdk boot user rootinfo magisk
    state=$(adb_text get-state) || fail 'ADB cannot reach the tablet.'
    [[ "$state" == device ]] || fail "Android must be booted and authorized; ADB state: $state"
    model=$(adb_text shell getprop ro.product.model) || fail 'Cannot read the model.'
    product=$(adb_text shell getprop ro.product.device) || fail 'Cannot read the device name.'
    build=$(adb_text shell getprop ro.build.version.incremental) || fail 'Cannot read the Android build.'
    sdk=$(adb_text shell getprop ro.build.version.sdk) || fail 'Cannot read the Android SDK.'
    boot=$(adb_text shell getprop sys.boot_completed) || fail 'Cannot read Android boot state.'
    user=$(adb_text shell am get-current-user) || fail 'Cannot determine the current Android user.'
    [[ "$model" == SM-P585 && "$product" == gtanotexllte && "$build" == P585JXS3CTL1 && "$sdk" == 27 ]] || \
        fail "Device/build mismatch: $model / $product / $build / Android SDK $sdk"
    [[ "$boot" == 1 && "$user" == 0 ]] || fail "Wait for Android to boot as owner user 0 (boot=$boot user=$user)."
    rootinfo=$(adb_text shell 'su -c id') || fail 'Magisk su is not available to ADB shell.'
    [[ "$rootinfo" == uid=0\(* ]] || fail 'ADB shell did not receive root. Approve the Magisk prompt if shown.'
    magisk=$(root_script 'command -v magisk; magisk --list' || true)
    [[ "$magisk" == *resetprop* ]] || fail 'Magisk resetprop is unavailable; nothing was changed.'
    root_script 'command -v sha256sum >/dev/null; command -v settings >/dev/null; command -v appops >/dev/null; command -v setprop >/dev/null' >/dev/null || \
        fail 'Required Android commands are unavailable.'
}

probe_clean_start() {
    local result
    result=$(root_script "if [ -e '$MOD' ] || [ -L '$MOD' ]; then echo MANAMA_MODULE_EXISTS; else echo MANAMA_MODULE_CLEAR; fi") || fail 'Cannot inspect the Magisk module path.'
    [[ "$result" == MANAMA_MODULE_CLEAR ]] || fail 'A Manama module already exists. Restore it before running setup again.'
    result=$(adb_text shell "p=\$(pm list packages -u '$PKG') || { echo MANAMA_PM_ERROR; exit; }; case \"\$p\" in *package:$PKG*) echo MANAMA_APP_EXISTS;; *) echo MANAMA_APP_CLEAR;; esac") || fail 'Cannot inspect installed packages.'
    [[ "$result" == MANAMA_APP_CLEAR ]] || fail 'io.appium.settings already exists or package inspection failed; refusing to overwrite it.'
    result=$(adb_text shell 'if appops query-op --user 0 android:mock_location allow; then echo MANAMA_QUERY_OK; else echo MANAMA_QUERY_ERROR; fi') || fail 'Cannot inspect existing mock-location permissions.'
    case "$result" in
        MANAMA_QUERY_OK|$'No operations.\nMANAMA_QUERY_OK') ;;
        *) fail 'Another mock-location app may already be allowed, or appops inspection failed. No appops were changed.' ;;
    esac
    # The dollar expressions in this quoted script must expand on Android.
    # shellcheck disable=SC2016
    result=$(adb_text shell 'd=$(dumpsys location 2>/dev/null) || { echo MANAMA_DUMP_ERROR; exit; }; case "$d" in *"Mock Providers:"*) echo MANAMA_MOCK_EXISTS;; *) echo MANAMA_MOCK_CLEAR;; esac') || fail 'Cannot inspect existing mock providers.'
    [[ "$result" == MANAMA_MOCK_CLEAR ]] || fail 'A mock provider is already active or location inspection failed; refusing to replace it.'
}

write_state() {
    local values line key value present providers auto tz
    mkdir -p "$RUN_DIR/state/props"
    printf '%s\n' "$SERIAL" > "$RUN_DIR/state/serial"
    printf '%s\n' "$TOKEN" > "$RUN_DIR/state/token"
    providers=$(adb_text shell settings --user 0 get secure location_providers_allowed) || fail 'Cannot back up location providers.'
    [[ "$providers" =~ ^[A-Za-z0-9_,.\-]*$ ]] || fail 'Unexpected original location-provider value.'
    auto=$(adb_text shell settings --user 0 get global auto_time_zone) || fail 'Cannot back up automatic timezone.'
    [[ "$auto" == 0 || "$auto" == 1 || "$auto" == null ]] || fail "Unexpected automatic timezone setting: $auto"
    tz=$(adb_text shell getprop persist.sys.timezone) || fail 'Cannot back up the timezone.'
    [[ "$tz" =~ ^[A-Za-z0-9_./+\-]+$ ]] || fail 'Unexpected or empty original timezone.'
    printf '%s\n' "$providers" > "$RUN_DIR/state/location_providers_allowed"
    printf '%s\n' "$auto" > "$RUN_DIR/state/auto_time_zone"
    printf '%s\n' "$tz" > "$RUN_DIR/state/timezone"
    # Keep the full property listing in memory only; write only the six values changed.
    values=$(adb_text shell getprop) || fail 'Cannot back up Android properties.'
    for key in "${PROPS[@]}"; do
        line=$(printf '%s\n' "$values" | awk -v key="$key" 'index($0, "[" key "]: [") == 1 { print }')
        if [[ -z "$line" ]]; then
            present=0; value=''
        else
            [[ "$line" != *$'\n'* && "$line" == "[$key]: ["*']' ]] || fail "Unexpected property listing for $key."
            present=1
            value=${line#"[$key]: ["}; value=${value%]}
            [[ "$value" != *$'\r'* && "$value" != *$'\n'* ]] || fail "Unsafe property value for $key."
        fi
        printf '%s\n' "$present" > "$RUN_DIR/state/props/$key.present"
        printf '%s\n' "$value" > "$RUN_DIR/state/props/$key.value"
    done
}

fetch_helper() {
    local blob=$1 hash=$2 dest=$3
    curl -4 -fsSL --retry 3 --connect-timeout 20 --max-time 120 \
        -H 'Accept: application/vnd.github.raw+json' -H 'X-GitHub-Api-Version: 2022-11-28' \
        "https://api.github.com/repos/$REPO/git/blobs/$blob" -o "$dest" || fail 'Could not download a required helper from GitHub.'
    printf '%s  %s\n' "$hash" "$dest" | sha256sum -c - >/dev/null || fail 'A helper checksum did not match.'
}

prepare_apk() {
    say 'Downloading and verifying the Appium-based mock-location helper...'
    fetch_helper "$REPACK_BLOB" "$REPACK_SHA" "$RUN_DIR/repack.py"
    fetch_helper "$NOTICE_BLOB" "$NOTICE_SHA" "$RUN_DIR/NOTICE-Appium.txt"
    fetch_helper "$ACTIONS_BLOB" "$ACTIONS_SHA" "$RUN_DIR/actions.sh"
    fetch_helper "$SERVICE_BLOB" "$SERVICE_SHA" "$RUN_DIR/service.sh"
    curl -4 -fsSL --retry 3 --connect-timeout 20 --max-time 120 \
        'https://registry.npmjs.org/io.appium.settings/-/io.appium.settings-5.7.5.tgz' \
        -o "$RUN_DIR/appium.tgz" || fail 'Could not download Appium Settings from npm.'
    printf '%s  %s\n' "$APPIUM_TGZ_SHA" "$RUN_DIR/appium.tgz" | sha256sum -c - >/dev/null || fail 'Appium download checksum did not match.'
    tar -xOzf "$RUN_DIR/appium.tgz" package/apks/settings_apk-debug.apk > "$RUN_DIR/upstream.apk" || fail 'Cannot extract the Appium APK.'
    printf '%s  %s\n' "$APPIUM_APK_SHA" "$RUN_DIR/upstream.apk" | sha256sum -c - >/dev/null || fail 'Appium APK checksum did not match.'
    tar -xOzf "$RUN_DIR/appium.tgz" package/LICENSE > "$RUN_DIR/LICENSE-Appium.txt" || fail 'Cannot extract the Appium license.'
    printf '%s  %s\n' "$APPIUM_LICENSE_SHA" "$RUN_DIR/LICENSE-Appium.txt" | sha256sum -c - >/dev/null || fail 'Appium license checksum did not match.'
    python3 "$RUN_DIR/repack.py" "$RUN_DIR/upstream.apk" "$RUN_DIR/helper.apk" "$RUN_DIR/NOTICE-Appium.txt" "$RUN_DIR/LICENSE-Appium.txt" || fail 'Cannot prepare the locally signed helper APK.'
    sha256sum "$RUN_DIR/helper.apk" | awk '{print $1}' > "$RUN_DIR/state/helper_apk.sha256"
}

make_manifest() {
    (cd "$RUN_DIR/state" && sha256sum serial token location_providers_allowed auto_time_zone timezone helper_apk.sha256 props/*) > "$RUN_DIR/state.sha256" || fail 'Cannot checksum the backup.'
    (cd "$RUN_DIR/state" && sha256sum -c ../state.sha256 >/dev/null) || fail 'Backup checksum failed.'
}

# Compare a device file's content with its host copy, not just adb push status.
check_remote_file() {
    local remote=$1 localfile=$2 out actual expected
    out=$(root_script "if [ -f '$remote' ] && [ ! -L '$remote' ]; then sha256sum '$remote'; else exit 1; fi") || return 1
    [[ "$out" =~ ^([0-9a-f]{64})[[:space:]] ]] || return 1
    actual=${BASH_REMATCH[1]}
    expected=$(sha256sum "$localfile"); expected=${expected%% *}
    [[ "$actual" == "$expected" ]]
}

stage_and_copy() {
    local result file key
    result=$(adb_text shell "if [ ! -e '$STAGE' ] && [ ! -L '$STAGE' ] && mkdir '$STAGE' && chmod 700 '$STAGE' && printf '%s\\n' '$TOKEN' > '$STAGE/owner'; then echo MANAMA_STAGE_OK; else echo MANAMA_STAGE_ERROR; fi") || fail 'Cannot create Android staging directory.'
    [[ "$result" == MANAMA_STAGE_OK ]] || fail 'Android staging directory already exists or could not be created.'
    STAGE_CREATED=1
    result=$(adb_text shell "if mkdir '$STAGE/state' '$STAGE/state/props'; then echo MANAMA_DIRS_OK; else echo MANAMA_DIRS_ERROR; fi") || fail 'Cannot create staging subdirectories.'
    [[ "$result" == MANAMA_DIRS_OK ]] || fail 'Cannot prepare staging subdirectories.'
    for file in serial token location_providers_allowed auto_time_zone timezone helper_apk.sha256; do
        timeout 40 "${ADB[@]}" push "$RUN_DIR/state/$file" "$STAGE/state/$file" >/dev/null || fail "Cannot stage backup item $file."
        check_remote_file "$STAGE/state/$file" "$RUN_DIR/state/$file" || fail "Backup readback failed for $file."
    done
    for key in "${PROPS[@]}"; do
        for file in "$key.present" "$key.value"; do
            timeout 40 "${ADB[@]}" push "$RUN_DIR/state/props/$file" "$STAGE/state/props/$file" >/dev/null || fail "Cannot stage backup item $file."
            check_remote_file "$STAGE/state/props/$file" "$RUN_DIR/state/props/$file" || fail "Backup readback failed for $file."
        done
    done
    for file in actions.sh service.sh; do
        timeout 40 "${ADB[@]}" push "$RUN_DIR/$file" "$STAGE/$file" >/dev/null || fail "Cannot stage $file."
        check_remote_file "$STAGE/$file" "$RUN_DIR/$file" || fail "Readback failed for $file."
    done
    # The module owner marker is written immediately after creation, so cleanup
    # can remove only this installation and never a pre-existing module.
    MODULE_CREATED=1
    root_script "
        if [ -e '$MOD' ] || [ -L '$MOD' ]; then echo 'Module collision'; exit 1; fi
        if [ -e '$PHONE_BACKUPS' ] || [ -L '$PHONE_BACKUPS' ]; then
            [ -d '$PHONE_BACKUPS' ] && [ ! -L '$PHONE_BACKUPS' ] || exit 1
        else
            mkdir '$PHONE_BACKUPS'; chmod 700 '$PHONE_BACKUPS'
        fi
        [ ! -e '$DEVICE_STATE' ] && [ ! -L '$DEVICE_STATE' ] || exit 1
        mkdir '$DEVICE_STATE'; chmod 700 '$DEVICE_STATE'
        cp -R '$STAGE/state/.' '$DEVICE_STATE/'
        chmod -R go-rwx '$DEVICE_STATE'
        mkdir '$MOD'; printf '%s\\n' '$TOKEN' > '$MOD/owner'
        mkdir '$MOD/touched'; chmod 700 '$MOD/touched'
        cp '$STAGE/actions.sh' '$MOD/actions.sh'
        cp '$STAGE/service.sh' '$MOD/service.sh'
        chmod 700 '$MOD' '$MOD/actions.sh' '$MOD/service.sh'
        printf '%s\\n' '$DEVICE_STATE' > '$MOD/backup-path'
        printf '%s\\n' 'id=p585_manama' 'name=Manama Mock Location' 'version=1.0' 'versionCode=1' 'author=Local setup' 'description=Reversible Android location testing and carrier property simulation' > '$MOD/module.prop'
        chmod 600 '$MOD/owner' '$MOD/backup-path' '$MOD/module.prop'
    " >/dev/null || fail 'Cannot copy the snapshot and create the guarded Magisk module.'
    for file in serial token location_providers_allowed auto_time_zone timezone helper_apk.sha256; do
        check_remote_file "$DEVICE_STATE/$file" "$RUN_DIR/state/$file" || fail "Device backup verification failed for $file."
    done
    for key in "${PROPS[@]}"; do
        for file in "$key.present" "$key.value"; do
            check_remote_file "$DEVICE_STATE/props/$file" "$RUN_DIR/state/props/$file" || fail "Device backup verification failed for $file."
        done
    done
    check_remote_file "$MOD/actions.sh" "$RUN_DIR/actions.sh" || fail 'Device action script readback failed.'
    check_remote_file "$MOD/service.sh" "$RUN_DIR/service.sh" || fail 'Device service script readback failed.'
}

# Return absent, matching, or different. Only our exact locally generated APK
# may be removed; a pre-existing or subsequently replaced app is never removed.
helper_state() {
    local list path hash expected
    list=$(root_script "x=\$(pm path '$PKG' 2>/dev/null || true); if [ -z \"\$x\" ]; then echo MANAMA_APP_ABSENT; else printf '%s\\n' \"\$x\"; fi") || return 1
    if [[ "$list" == MANAMA_APP_ABSENT ]]; then say absent; return 0; fi
    [[ "$list" != *$'\n'* && "$list" == package:/data/app/*/base.apk ]] || { say different; return 0; }
    path=${list#package:}
    [[ "$path" =~ ^/data/app/[A-Za-z0-9_./+=~-]+/base\.apk$ ]] || { say different; return 0; }
    hash=$(root_script "if [ -f '$path' ] && [ ! -L '$path' ]; then sha256sum '$path'; else exit 1; fi") || return 1
    [[ "$hash" =~ ^([0-9a-f]{64})[[:space:]] ]] || return 1
    expected=$(cat "$RUN_DIR/state/helper_apk.sha256") || return 1
    if [[ "${BASH_REMATCH[1]}" == "$expected" ]]; then say matching; else say different; fi
}

uninstall_helper() {
    local state result
    state=$(helper_state) || { warn 'Cannot verify the installed helper APK; it was not removed.'; return 1; }
    case "$state" in
        absent) return 0 ;;
        different) warn 'The installed Appium package differs from this backup; it was not removed.'; return 1 ;;
        matching) ;;
        *) return 1 ;;
    esac
    result=$(root_script "pm uninstall '$PKG'") || return 1
    [[ "$result" == Success ]] || { warn "Uninstall was not confirmed: $result"; return 1; }
    [[ "$(helper_state)" == absent ]]
}

remove_owned_module() {
    root_script "if [ -d '$MOD' ] && [ ! -L '$MOD' ] && [ \"\$(cat '$MOD/owner' 2>/dev/null)\" = '$TOKEN' ]; then rm -rf '$MOD'; else echo 'Module owner mismatch'; exit 1; fi; [ ! -e '$MOD' ] && [ ! -L '$MOD' ]" >/dev/null
}

clean_stage() {
    (( STAGE_CREATED == 1 )) || return 0
    [[ -n "$STAGE" && -n "$TOKEN" ]] || return 0
    root_script "if [ -d '$STAGE' ] && [ ! -L '$STAGE' ] && [ \"\$(cat '$STAGE/owner' 2>/dev/null)\" = '$TOKEN' ]; then rm -rf '$STAGE'; fi" >/dev/null || true
}

disable_owned_watcher() {
    local result
    result=$(root_script "sh '$MOD/actions.sh' disable '$MOD' '$DEVICE_STATE' '$TOKEN'") || return 1
    [[ "$result" == *MANAMA_DISABLE_OK* ]]
}

restore_device() {
    local result state
    state=$(helper_state) || { warn 'Cannot verify the installed helper before restoring.'; return 1; }
    if [[ "$state" == different ]]; then
        # A subsequently installed app with the same package name is not ours:
        # stop future watcher work, but do not stop or uninstall its service.
        if disable_owned_watcher; then
            warn 'The helper changed. The watcher was stopped; the other app and settings were left alone.'
        else
            warn 'The helper changed and stopping the watcher could not be confirmed. Do not uninstall the other app.'
        fi
        return 1
    fi
    if [[ "$state" == absent ]]; then
        # If the helper was removed while a mock provider still exists, its
        # owner is unknown. Do not disturb a possible replacement mock app.
        # This quoted script is expanded by the Android shell, not the host.
        # shellcheck disable=SC2016
        result=$(root_script 'd=$(dumpsys location 2>/dev/null); case "$d" in *"Mock Providers:"*) echo MANAMA_MOCK_EXISTS;; *) echo MANAMA_MOCK_CLEAR;; esac') || return 1
        if [[ "$result" != MANAMA_MOCK_CLEAR ]]; then
            if disable_owned_watcher; then
                warn 'The helper is absent and a mock provider remains. The watcher was stopped; reboot or inspect before restoring.'
            else
                warn 'The helper is absent and a mock provider remains; stopping the watcher could not be confirmed.'
            fi
            return 1
        fi
    fi
    result=$(root_script "sh '$MOD/actions.sh' restore '$MOD' '$DEVICE_STATE' '$TOKEN'") || return 1
    [[ "$result" == *MANAMA_RESTORE_OK* ]] || { warn "Restoration not confirmed: $result"; return 1; }
    if [[ "$result" == *MANAMA_NOTE:* ]]; then
        warn 'Some carrier properties changed independently; their newer values were left intact.'
    fi
    uninstall_helper || return 1
    remove_owned_module || { warn 'Settings were restored, but the disabled module could not be removed.'; return 1; }
    return 0
}

on_exit() {
    local code=$?
    trap - EXIT INT TERM
    [[ "$IN_CLEANUP" == 0 ]] || exit "$code"
    IN_CLEANUP=1
    if (( code != 0 && COMPLETE == 0 && MODULE_CREATED == 1 )); then
        warn 'Setup stopped. Attempting to disable the module and undo changes...'
        if (( APPLY_STARTED == 1 )); then
            if restore_device; then warn 'Automatic restore completed.'; else warn 'Automatic restore incomplete; keep the backup and run the restore command after reconnecting.'; fi
        else
            root_script "if [ -d '$MOD' ] && [ ! -L '$MOD' ] && [ \"\$(cat '$MOD/owner' 2>/dev/null)\" = '$TOKEN' ]; then : > '$MOD/disable'; fi" >/dev/null || true
            if (( PACKAGE_MAY_EXIST == 1 )); then uninstall_helper || warn 'Helper cleanup was not confirmed.'; fi
            remove_owned_module 2>/dev/null || warn 'Module cleanup was not confirmed; it may need inspection.'
        fi
    fi
    clean_stage
    if [[ -n "$RUN_DIR" && "$code" != 0 ]]; then
        warn "Private backup/log directory: $RUN_DIR"
        if (( MODULE_CREATED == 1 || PACKAGE_MAY_EXIST == 1 || APPLY_STARTED == 1 )); then
            warn "If automatic restore was incomplete, reconnect and run: bash '$RUN_DIR/tool.sh' restore"
        else
            warn 'No changes were made to the tablet.'
        fi
    fi
    exit "$code"
}
trap on_exit EXIT
trap 'exit 130' INT TERM

verify_host_backup() {
    [[ -d "$RUN_DIR/state" && -f "$RUN_DIR/state.sha256" ]] || fail 'Backup is missing.'
    (cd "$RUN_DIR/state" && sha256sum -c ../state.sha256 >/dev/null) || fail 'Backup checksum mismatch; no restore was attempted.'
    printf '%s  %s\n' "$ACTIONS_SHA" "$RUN_DIR/actions.sh" | sha256sum -c - >/dev/null || fail 'Saved restore script checksum mismatch.'
    TOKEN=$(cat "$RUN_DIR/state/token")
    [[ "$TOKEN" =~ ^p585-manama-[[:alnum:]]{8}$ ]] || fail 'Invalid backup token.'
    [[ "$(cat "$RUN_DIR/state/serial")" == "$SERIAL" ]] || fail 'Backup belongs to a different tablet.'
    DEVICE_STATE="$PHONE_BACKUPS/$TOKEN"
}

verify_device_backup() {
    local file key owner
    owner=$(root_script "[ -d '$MOD' ] && [ ! -L '$MOD' ]; cat '$MOD/owner'") || fail 'This backup does not have a matching active/disabled module.'
    [[ "$owner" == "$TOKEN" ]] || fail 'Module does not belong to this backup.'
    for file in serial token location_providers_allowed auto_time_zone timezone helper_apk.sha256; do
        check_remote_file "$DEVICE_STATE/$file" "$RUN_DIR/state/$file" || fail "Device backup differs: $file"
    done
    for key in "${PROPS[@]}"; do
        for file in "$key.present" "$key.value"; do
            check_remote_file "$DEVICE_STATE/props/$file" "$RUN_DIR/state/props/$file" || fail "Device backup differs: $file"
        done
    done
    check_remote_file "$MOD/actions.sh" "$RUN_DIR/actions.sh" || fail 'Device restore script differs from the saved copy.'
}

status_device() {
    local result
    result=$(root_script "d=\$(dumpsys location 2>/dev/null)
        if printf '%s\\n' \"\$d\" | grep -F 'Mock Providers:' >/dev/null && printf '%s\\n' \"\$d\" | grep -E 'Location\\[gps (26[.,]223536,50[.,]586994|۲۶(٫|[.])۲۲۳۵۳۶,۵۰(٫|[.])۵۸۶۹۹۴|٢٦(٫|[.])٢٢٣٥٣٦,٥٠(٫|[.])٥٨٦٩٩٤) .*mock' >/dev/null && printf '%s\\n' \"\$d\" | grep -E 'Location\\[network (26[.,]223536,50[.,]586994|۲۶(٫|[.])۲۲۳۵۳۶,۵۰(٫|[.])۵۸۶۹۹۴|٢٦(٫|[.])٢٢٣٥٣٦,٥٠(٫|[.])٥٨٦٩٩٤) .*mock' >/dev/null; then echo MANAMA_LOCATION_OBSERVED; else echo MANAMA_LOCATION_NOT_VERIFIED; fi
        carrier=ok
        for p in gsm.sim.operator.numeric gsm.operator.numeric; do [ \"\$(getprop \"\$p\")\" = 42601 ] || carrier=no; done
        for p in gsm.sim.operator.iso-country gsm.operator.iso-country; do [ \"\$(getprop \"\$p\")\" = bh ] || carrier=no; done
        for p in gsm.sim.operator.alpha gsm.operator.alpha; do [ \"\$(getprop \"\$p\")\" = Batelco ] || carrier=no; done
        [ \"\$carrier\" = ok ] && echo MANAMA_CARRIER_OBSERVED || echo MANAMA_CARRIER_NOT_VERIFIED
        [ \"\$(getprop persist.sys.timezone)\" = Asia/Bahrain ] && echo MANAMA_TIMEZONE_OBSERVED || echo MANAMA_TIMEZONE_NOT_VERIFIED
        pid=\$(cat '$MOD/worker.pid' 2>/dev/null || true)
        case \"\$pid\" in ''|*[!0-9]*) echo MANAMA_WATCHER_NOT_VERIFIED;; *)
            if [ -r \"/proc/\$pid/cmdline\" ] && tr '\\000' ' ' < \"/proc/\$pid/cmdline\" | grep -F '$MOD/service.sh' >/dev/null; then echo MANAMA_WATCHER_OBSERVED; else echo MANAMA_WATCHER_NOT_VERIFIED; fi;; esac") || return 1
    printf '%s\n' "$result"
}

main() {
    local mode=${1:-install} result state self n
    for x in adb timeout sha256sum tr grep awk; do need "$x"; done
    case "$mode" in install|restore|status) ;; *) fail 'Usage: tool.sh [install|status|restore]';; esac
    if [[ "$mode" != install ]]; then
        RUN_DIR=$(cd -- "$(dirname -- "$0")" && pwd -P)
        [[ -n "$SERIAL" ]] || SERIAL=$(cat "$RUN_DIR/state/serial" 2>/dev/null || true)
    fi
    validate_serial
    check_device
    if [[ "$mode" != install ]]; then
        verify_host_backup
        verify_device_backup
        if [[ "$mode" == status ]]; then status_device || fail 'Cannot inspect the simulation.'; COMPLETE=1; return; fi
        say 'Restoring the saved Android settings and removing only this helper...'
        restore_device || fail 'Restore was incomplete. The backup is retained; reconnect and retry.'
        COMPLETE=1
        say "Restore completed. Private backups remain on the host in $RUN_DIR and on the tablet in $DEVICE_STATE."
        return
    fi
    for x in curl tar python3 openssl mktemp cp chmod; do need "$x"; done
    python3 -c 'import sys; assert sys.version_info >= (3,8)' || fail 'Python 3.8 or newer is required.'
    probe_clean_start
    mkdir -p "$BASE"
    RUN_DIR=$(mktemp -d "$BASE/p585-manama-XXXXXXXX") || fail 'Cannot create private backup directory.'
    chmod 700 "$RUN_DIR"
    TOKEN=${RUN_DIR##*/}
    [[ "$TOKEN" =~ ^p585-manama-[[:alnum:]]{8}$ ]] || fail 'Unexpected backup directory name.'
    DEVICE_STATE="$PHONE_BACKUPS/$TOKEN"
    STAGE="/data/local/tmp/$TOKEN"
    self=$(readlink -f -- "$0") || fail 'Cannot locate this script.'
    if ! cp "$self" "$RUN_DIR/tool.sh" || ! chmod 700 "$RUN_DIR/tool.sh"; then fail 'Cannot preserve the restore command.'; fi
    say "Backing up current location, timezone, and six carrier properties to $RUN_DIR ..."
    write_state
    prepare_apk
    make_manifest
    say 'Host snapshot checksums verified. A second copy will be stored privately on the tablet before settings change.'
    cat <<'WARN'

BEFORE CONTINUING:
  * Manama is a MOCK location. It may be detectable, rejected, stale, or ignored.
    Android GPS/network providers are replaced while active; direct hardware,
    cell, Wi-Fi, cached locations, and other channels may still reveal reality.
  * Apps and emergency services may receive the wrong location. Do not rely on
    this while navigating, calling emergency services, or where safety matters.
  * Bahrain/Batelco carrier properties can affect calls, data or emergency
    behavior. The actual SIM, mobile network, IMEI and IP do NOT change.
  * No Bahrain VPN is configured. Internet IP and account region are unchanged.
  * A locally re-signed, reduced-permission Appium helper and a Magisk startup
    module will be installed. Simulation will retry after reboot; no reboot is
    performed by this script. Failures will trigger a best-effort restore.
  * Backups contain sensitive settings. Keep them private. No EFS/modem/partition
    is modified. A recovery-screen problem or unexpected boot issue is possible.
WARN
    printf 'Type APPLY to accept and continue, or press Enter to cancel: '
    IFS= read -r answer || answer=''
    [[ "$answer" == APPLY ]] || fail 'Cancelled; no tablet changes were made.'
    say 'Copying and verifying the snapshot on the tablet...'
    stage_and_copy
    say 'Installing the locally signed location helper...'
    PACKAGE_MAY_EXIST=1
    result=$(timeout 120 "${ADB[@]}" install --user 0 "$RUN_DIR/helper.apk" 2>&1 | tr -d '\r') || fail "Helper installation failed: $result"
    [[ "$result" == *$'\n'Success || "$result" == Success ]] || fail "Helper installation was not confirmed: $result"
    state=$(helper_state) || fail 'Cannot verify the helper on the tablet.'
    [[ "$state" == matching ]] || fail 'Installed helper does not match the locally signed APK.'
    say 'Starting and verifying Manama mock providers...'
    APPLY_STARTED=1
    result=$(root_script "sh '$MOD/actions.sh' apply '$MOD' '$DEVICE_STATE' '$TOKEN'") || fail 'Could not apply or verify the mock provider.'
    [[ "$result" == *MANAMA_APPLY_OK* ]] || fail 'Mock-provider setup did not confirm success.'
    say 'Starting the after-boot watcher and checking the carrier properties...'
    root_script "sh '$MOD/service.sh' </dev/null >'$MOD/service.log' 2>&1 &" >/dev/null || fail 'Cannot start the watcher.'
    n=0
    while :; do
        result=$(status_device || true)
        if [[ "$result" == *MANAMA_LOCATION_OBSERVED* && "$result" == *MANAMA_CARRIER_OBSERVED* && "$result" == *MANAMA_TIMEZONE_OBSERVED* && "$result" == *MANAMA_WATCHER_OBSERVED* ]]; then break; fi
        (( n < 20 )) || fail 'The requested mock location, carrier properties, or timezone could not all be verified.'
        sleep 2
        n=$((n + 1))
    done
    COMPLETE=1
    clean_stage
    STAGE=''
    say ''
    say 'Manama mock GPS/network providers and Bahrain carrier properties were observed.'
    say 'This does NOT prove every app is spoofed or that real location/IP cannot leak.'
    say "Private backup: $RUN_DIR"
    say "Status:  bash '$RUN_DIR/tool.sh' status"
    say "Restore: bash '$RUN_DIR/tool.sh' restore"
}

main "$@"
