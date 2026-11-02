#!/usr/bin/env bash
# Guarded Magisk recovery-install attempt for the specific SM-P585 in this session.
# Does not format partitions, restore firmware, reinstall TWRP, or silently change flags.
set -Eeuo pipefail
umask 077

# The tablet's serial is supplied locally rather than published in this script.
# Its fingerprint preserves the original guard against selecting another device.
readonly SERIAL="${P585_SERIAL:-}"
readonly SERIAL_SHA256='072734241f69cfc548d138f9a496f532972e476926cb69e054ebda981851728d'
readonly MODEL='SM-P585'
readonly PRODUCT='gtanotexllte'
readonly BUILD='P585JXS3CTL1'
readonly MAGISK_SHA256='e0d32d2123532860f97123d927b1bb86c4e08e6fd8a48bfc6b5bee0afae9ebd5'
readonly STOCK_BOOT_SHA256='a83424bca63d5d30d2da54cfa2c952a1e21982c8c05265fb03e224e9e6fdf015'
readonly STOCK_BOOT_BYTES=18882848
readonly BASE="${P585_WORKDIR:-/root/root-p585}"
readonly ZIP="$BASE/Magisk-v30.7.zip"
ADB=(adb -s "$SERIAL")
RUN_DIR=''
PHASE='preflight'

capture_recovery_log() {
    [[ -n "$RUN_DIR" ]] || return 0
    timeout 10 "${ADB[@]}" exec-out cat /tmp/recovery.log > "$RUN_DIR/recovery.log" 2>/dev/null || true
}

fail() {
    printf '\nSTOP: %s\n' "$*" >&2
    if [[ "$PHASE" == 'installing' ]]; then
        printf '%s\n' 'The installer may still be running, even if the host command ended.' \
            'Do not reboot, disconnect, or retry until the TWRP log is inspected.' >&2
    fi
    if [[ "$PHASE" == 'installing' || "$PHASE" == 'installed' ]]; then capture_recovery_log; fi
    if [[ -n "$RUN_DIR" ]]; then
        printf 'Logs and private backups: %s\n' "$RUN_DIR" >&2
    fi
    exit 1
}

interrupted() {
    printf '\nInterrupted.\n' >&2
    if [[ "$PHASE" == 'installing' ]]; then
        printf '%s\n' 'TWRP may still be writing. Do not reboot, disconnect, or retry' \
            'until the recovery log is inspected.' >&2
    fi
    [[ -z "$RUN_DIR" ]] || printf 'Logs and private backups: %s\n' "$RUN_DIR" >&2
    exit 130
}
trap interrupted INT TERM

need() { command -v "$1" >/dev/null 2>&1 || fail "Missing host command: $1"; }
adb_text() { timeout 20 "${ADB[@]}" "$@" | tr -d '\r'; }
prop() { adb_text shell getprop "$1"; }

# Resolve only real block partitions on the expected internal eMMC.
resolve_block() {
    local path=$1 out
    [[ "$path" =~ ^/dev/block/[A-Za-z0-9_./-]+$ ]] || return 1
    out=$(adb_text shell "readlink -f '$path'") || return 1
    [[ "$out" =~ ^/dev/block/mmcblk0p[0-9]+$ ]] || return 1
    timeout 20 "${ADB[@]}" shell "test -b '$out'" >/dev/null 2>&1 || return 1
    printf '%s\n' "$out"
}

check_recovery() {
    local state model product bootloader locked
    state=$(adb_text get-state 2>/dev/null) || fail "ADB cannot reach $SERIAL. Boot the tablet into TWRP recovery."
    [[ "$state" == 'recovery' ]] || fail "Expected ADB recovery, got: ${state:-unknown}"
    model=$(prop ro.product.model) || fail 'Cannot read recovery model.'
    product=$(prop ro.product.device) || fail 'Cannot read recovery product.'
    bootloader=$(prop ro.bootloader) || fail 'Cannot read bootloader property.'
    if [[ -z "$bootloader" ]]; then bootloader=$(prop ro.boot.bootloader) || true; fi
    [[ "$model" == "$MODEL" && "$product" == "$PRODUCT" && "$bootloader" == "$BUILD" ]] || \
        fail "Device/build mismatch: model=$model product=$product bootloader=${bootloader:-unknown}"
    locked=$(prop ro.boot.flash.locked) || locked=''
    [[ "$locked" != '1' ]] || fail 'Recovery reports a locked bootloader. No installation will be attempted.'
}

# Check the mounted filesystem source and options without changing it.
check_mount() {
    local mountpoint=$1 expected=$2 wanted=$3 line source fs options resolved
    line=$(adb_text shell "awk '\$2==\"$mountpoint\" {print \$1,\$3,\$4; exit}' /proc/mounts") || return 1
    read -r source fs options <<< "$line"
    [[ -n "${source:-}" && "${fs:-}" == 'ext4' ]] || return 1
    resolved=$(resolve_block "$source") || return 1
    [[ "$resolved" == "$expected" ]] || return 1
    case ",$options," in
        *",$wanted,"*) ;;
        *) return 1 ;;
    esac
}

# Magisk also reads inherited environment variables, including MAGISKTMP,
# whose config has priority over /data and /cache. Reject unexpected values.
check_recovery_environment() {
    local inherited line checked=0
    # The quoted script is executed in recovery; its variables expand there.
    # shellcheck disable=SC2016
    inherited=$(adb_text shell '
        pids="$(pidof recovery 2>/dev/null) $(pidof twrp 2>/dev/null)"
        [ -n "$(echo "$pids" | tr -d "[:space:]")" ] || exit 1
        for p in $pids; do
            [ -r "/proc/$p/environ" ] || exit 1
            env_lines=$(tr "\000" "\n" < "/proc/$p/environ") || exit 1
            printf "%s\n" "$env_lines" |
                grep -E "^(MAGISKTMP|BOOTMODE|VENDORBOOT|RECOVERYMODE|KEEPVERITY|KEEPFORCEENCRYPT)=" || true
        done
        echo P585_ENV_CHECK_OK') || fail 'Cannot inspect the TWRP process environment.'
    while IFS= read -r line; do
        case "$line" in
            P585_ENV_CHECK_OK) checked=1 ;;
            ''|MAGISKTMP=|BOOTMODE=|BOOTMODE=false|VENDORBOOT=|VENDORBOOT=false|RECOVERYMODE=|RECOVERYMODE=false|KEEPVERITY=|KEEPVERITY=false|KEEPFORCEENCRYPT=|KEEPFORCEENCRYPT=false) ;;
            *) fail "Unexpected TWRP installer environment: $line" ;;
        esac
    done <<< "$inherited"
    [[ "$checked" == 1 ]] || fail 'Cannot inspect the TWRP process environment.'
}

# Match Magisk's config priority without sourcing either file as shell code.
check_flags() {
    local data cache config key actual line
    check_recovery_environment
    check_mount /data /dev/block/mmcblk0p22 rw || fail '/data is not the expected writable ext4 partition.'
    check_mount /cache /dev/block/mmcblk0p19 rw || fail '/cache is not the expected writable ext4 partition.'
    data=$(adb_text shell 'if [ -f /data/.magisk ]; then cat /data/.magisk; fi') || fail 'Cannot read /data/.magisk.'
    cache=$(adb_text shell 'if [ -f /cache/.magisk ]; then cat /cache/.magisk; fi') || fail 'Cannot read /cache/.magisk.'
    config=$(printf '%s\n%s\n' "$data" "$cache")
    for key in KEEPVERITY KEEPFORCEENCRYPT RECOVERYMODE; do
        actual=$(printf '%s\n' "$config" | awk -F= -v key="$key" '$1==key {sub(/^[^=]*=/, ""); print; exit}')
        [[ "$actual" == 'false' ]] || fail "Unexpected Magisk setting: $key=${actual:-unset}. No flags were changed."
    done
    actual=$(printf '%s\n' "$config" | awk -F= '$1=="VENDORBOOT" {sub(/^[^=]*=/, ""); print; exit}')
    [[ -z "$actual" || "$actual" == 'false' ]] || fail "Unexpected Magisk setting: VENDORBOOT=$actual."
    # Refuse even lower-priority conflicting entries rather than guessing.
    while IFS= read -r line; do
        case "$line" in
            KEEPVERITY=*|KEEPFORCEENCRYPT=*|RECOVERYMODE=*|VENDORBOOT=*)
                [[ "$line" == *=false ]] || fail "Conflicting Magisk setting: $line" ;;
        esac
    done <<< "$config"
}

# Reproduce the legacy target search used by this Magisk version, then check
# its resolved path against TWRP's boot partition. Reject slot-based layouts.
check_magisk_target() {
    local bootopts token value candidate
    bootopts=$(adb_text shell 'cat /proc/cmdline; if [ -r /proc/bootconfig ]; then cat /proc/bootconfig; fi') || fail 'Cannot read boot options.'
    while IFS= read -r token; do
        [[ -n "$token" ]] || continue
        value=${token#*=}
        value=$(printf '%s' "$value" | tr -d '[:space:]"')
        [[ -z "$value" || "$value" == 'normal' ]] || fail "Unexpected boot slot: $value"
    done < <(printf '%s\n' "$bootopts" | grep -oE 'androidboot\.slot(_suffix)?[[:space:]]*=[[:space:]]*"?[[:alnum:]_-]*"?' || true)

    # The quoted script expands variables on the tablet, not on the host.
    # shellcheck disable=SC2016
    candidate=$(adb_text shell '
        if [ -e /dev/block/by-name/boot ]; then
            readlink -f /dev/block/by-name/boot
        else
            for n in ramdisk kern-a android_boot kernel bootimg boot lnx boot_a; do
                p=$(find /dev/block \( -type b -o -type c -o -type l \) -iname "$n" 2>/dev/null | head -n 1)
                if [ -n "$p" ]; then readlink -f "$p"; break; fi
            done
        fi') || fail 'Cannot determine the Magisk boot target.'
    [[ "$candidate" == "$BOOT_DEV" && "$candidate" == '/dev/block/mmcblk0p9' ]] || \
        fail "Magisk boot-target check failed: ${candidate:-not found}"
}

backup_partition() {
    local label=$1 dev=$2 bytes=$3 outfile local_hash reread_hash size
    outfile="$RUN_DIR/${label}-before.img"
    printf '  Backing up %s (%s bytes), then checking a second read...\n' "$label" "$bytes" >&2
    if ! timeout 600 "${ADB[@]}" exec-out cat "$dev" > "$outfile"; then
        fail "Could not read $label; no installation attempted."
    fi
    size=$(stat -c '%s' "$outfile") || fail "Cannot stat backup of $label."
    [[ "$size" == "$bytes" ]] || fail "Incomplete $label backup: $size of $bytes bytes."
    local_hash=$(sha256sum "$outfile"); local_hash=${local_hash%% *}
    reread_hash=$(timeout 600 "${ADB[@]}" exec-out cat "$dev" | sha256sum) || fail "Could not reread $label."
    reread_hash=${reread_hash%% *}
    [[ "$local_hash" == "$reread_hash" ]] || fail "$label changed or the backup read did not match."
    printf '%s\n' "$local_hash"
}

usage() {
    printf '%s\n' \
        'Usage: P585_SERIAL=<tablet-adb-serial> bash p585-root-attempt.sh' \
        'Uses /root/root-p585/Magisk-v30.7.zip by default.' \
        'Optional: P585_WORKDIR=/path/to/root-p585 P585_SERIAL=<serial> bash p585-root-attempt.sh' \
        'Requires the specified tablet connected in existing TWRP recovery.'
}

main() {
    if (( $# )); then
        [[ "$#" == 1 && "$1" == '--help' ]] && { usage; return 0; }
        usage >&2; exit 2
    fi
    local command hash version battery locked fstab probe sys_build sys_abi line
    local i label mountpoint path resolved sectors bytes prefix_hash data_hash expected_init_hash tmp_root
    local needed_bytes available_kib
    local tool rc_test target target_resolved output manager_installed deadline state complete root_output root_rc after_hash
    local -a labels=(BOOT RECOVERY EFS m9kefs1 m9kefs2 m9kefs3)
    local -a mountpoints=(/boot /recovery /efs /m9kefs1 /m9kefs2 /m9kefs3)
    local -a devs=() sizes=() hashes=()
    local seen=' '

    printf '%s\n' \
        'SM-P585 guarded Magisk attempt — existing TWRP only' \
        'The known boot log reports a /system mount failure; Magisk may NOT fix it.' \
        'This script will not intentionally format data or flash a full firmware.' \
        'It will ask before staging and installing anything on the tablet.'
    for command in adb timeout sha256sum unzip awk grep sed tr head tail stat mktemp tee cp chmod mkdir sleep uname df; do need "$command"; done
    [[ "$SERIAL" =~ ^[0-9a-f]{16}$ ]] || fail 'Set P585_SERIAL to the tablet ADB serial.'
    hash=$(printf '%s' "$SERIAL" | sha256sum); hash=${hash%% *}
    [[ "$hash" == "$SERIAL_SHA256" ]] || fail 'The supplied ADB serial is not the intended tablet.'
    [[ -d "$BASE" && -r "$ZIP" ]] || fail "Missing directory or verified ZIP: $ZIP"
    [[ "$(uname -m)" == 'x86_64' ]] || fail 'The post-install verification tool requires an x86_64 Linux host.'
    hash=$(sha256sum "$ZIP"); hash=${hash%% *}
    [[ "$hash" == "$MAGISK_SHA256" ]] || fail 'Magisk ZIP SHA-256 does not match the official v30.7 APK.'
    unzip -tq "$ZIP" >/dev/null || fail 'Magisk ZIP failed its archive test.'

    check_recovery
    version=$(adb_text shell twrp -v) || fail 'Cannot query TWRP.'
    [[ "$version" == *'3.3.1-0'* ]] || fail "Expected TWRP 3.3.1-0; got: $version"
    battery=$(adb_text shell 'cat /sys/class/power_supply/battery/capacity 2>/dev/null') || fail 'Cannot read battery level.'
    [[ "$battery" =~ ^[0-9]{1,3}$ ]] || fail "Cannot interpret battery level: ${battery:-unknown}."
    (( 10#$battery >= 50 && 10#$battery <= 100 )) || \
        fail "Charge the battery to at least 50% first (current: $battery)."
    locked=$(prop ro.boot.flash.locked) || locked=''
    printf 'Device: %s / %s / %s; battery %s%%; bootloader lock: %s\n' \
        "$MODEL" "$PRODUCT" "$BUILD" "$battery" "${locked:-unknown}"
    [[ -n "$locked" ]] || printf '%s\n' 'Warning: bootloader unlock status could not be established.'

    fstab=$(adb_text shell cat /etc/recovery.fstab) || fail 'Cannot read TWRP fstab.'
    for i in "${!labels[@]}"; do
        label=${labels[$i]}; mountpoint=${mountpoints[$i]}
        path=$(printf '%s\n' "$fstab" | awk -v mp="$mountpoint" '$1==mp {print $3; exit}')
        [[ "$path" == "/dev/block/bootdevice/by-name/$label" ]] || \
            fail "Unexpected TWRP fstab entry for $mountpoint: ${path:-missing}"
        resolved=$(resolve_block "$path") || fail "Cannot resolve $path to an internal block partition."
        case "$seen" in *" $resolved "*) fail "Two fstab entries resolve to $resolved." ;; esac
        seen+="$resolved "
        sectors=$(adb_text shell "cat /sys/class/block/${resolved##*/}/size") || fail "Cannot read $label partition size."
        [[ "$sectors" =~ ^[0-9]{1,6}$ ]] || fail "Invalid partition size for $label: $sectors sectors."
        (( 10#$sectors > 0 && 10#$sectors <= 524288 )) || \
            fail "Unreasonable partition size for $label: $sectors sectors."
        bytes=$((10#$sectors * 512))
        devs+=("$resolved"); sizes+=("$bytes")
    done
    BOOT_DEV=${devs[0]}; RECOVERY_DEV=${devs[1]}
    [[ "$BOOT_DEV" == '/dev/block/mmcblk0p9' ]] || fail "Unexpected BOOT: $BOOT_DEV"
    (( sizes[0] >= STOCK_BOOT_BYTES )) || fail 'BOOT is smaller than the known stock image.'
    needed_bytes=$((268435456 + 3 * sizes[0]))
    for bytes in "${sizes[@]}"; do needed_bytes=$((needed_bytes + bytes)); done
    available_kib=$(df -Pk "$BASE" | awk 'NR > 1 {value=$4} END {print value}') || fail 'Cannot check host free space.'
    [[ "$available_kib" =~ ^[0-9]{1,15}$ ]] || fail 'Cannot interpret host free space.'
    (( 10#$available_kib >= (needed_bytes + 1023) / 1024 )) || \
        fail 'Not enough free host disk space for private backups and BOOT verification.'
    path=$(printf '%s\n' "$fstab" | awk '$1=="/system" {print $3; exit}')
    [[ "$path" == '/dev/block/bootdevice/by-name/SYSTEM' ]] || fail 'Unexpected /system fstab entry.'
    resolved=$(resolve_block "$path") || fail 'Cannot resolve SYSTEM partition.'
    [[ "$resolved" == '/dev/block/mmcblk0p18' ]] || fail "Unexpected SYSTEM: $resolved"

    if ! timeout 20 "${ADB[@]}" shell 'grep -q " /system " /proc/mounts' >/dev/null 2>&1; then
        printf '%s\n' 'Checking /system using a read-only mount without journal replay...'
        timeout 30 "${ADB[@]}" shell 'mount -t ext4 -o ro,noload /dev/block/bootdevice/by-name/SYSTEM /system' || \
            fail 'Could not mount /system read-only; this script will not repair or format it.'
    fi
    check_mount /system /dev/block/mmcblk0p18 ro || fail '/system is not the expected read-only ext4 mount.'
    sys_build=$(adb_text shell "grep '^ro.build.version.incremental=' /system/build.prop | head -n 1") || fail 'Cannot read system build.'
    [[ "$sys_build" == "ro.build.version.incremental=$BUILD" ]] || fail "Unexpected installed system build: $sys_build"
    sys_abi=$(adb_text shell "grep '^ro.product.cpu.abi=' /system/build.prop | head -n 1") || fail 'Cannot read system ABI.'
    sys_abi=${sys_abi#ro.product.cpu.abi=}
    [[ "$sys_abi" == 'armeabi-v7a' || "$sys_abi" == 'arm64-v8a' ]] || fail "Unexpected system ABI: $sys_abi"
    check_flags
    check_magisk_target

    RUN_DIR=$(mktemp -d "$BASE/p585-root-attempt-XXXXXXXX") || fail 'Cannot create private backup directory.'
    timeout 30 "${ADB[@]}" exec-out cat /proc/last_kmsg > "$RUN_DIR/last_kmsg.bin" 2>/dev/null || true
    printf 'Private backup/log directory: %s\n' "$RUN_DIR"
    printf '%s\n' 'Probing the TWRP command interface...'
    probe=$(timeout 20 "${ADB[@]}" shell twrp print P585_PREFLIGHT_OK 2>&1) || fail 'TWRP command interface timed out or failed.'
    [[ "$probe" == *'P585_PREFLIGHT_OK'* ]] || fail "TWRP did not confirm an idle command interface: $probe"
    [[ "$probe" != *'operation in progress'* && "$probe" != *'Another threaded action'* ]] || \
        fail 'TWRP reports another action is running.'

    printf '%s\n' 'Making BOOT, RECOVERY, EFS and m9kefs backups on the host.'
    for i in "${!labels[@]}"; do
        hash=$(backup_partition "${labels[$i]}" "${devs[$i]}" "${sizes[$i]}") || fail "Backup failed for ${labels[$i]}."
        hashes+=("$hash")
        printf '  %s backup readback matched.\n' "${labels[$i]}"
    done
    (cd "$RUN_DIR" && sha256sum ./*-before.img > SHA256SUMS) || fail 'Cannot save the backup manifest.'
    printf '%s\n' 'Backups matched two reads, but have NOT been restore-tested. EFS images are sensitive: keep them private.'
    prefix_hash=$(head -c "$STOCK_BOOT_BYTES" "$RUN_DIR/BOOT-before.img" | sha256sum); prefix_hash=${prefix_hash%% *}
    [[ "$prefix_hash" == "$STOCK_BOOT_SHA256" ]] || fail 'BOOT does not begin with the known matching stock image; refusing to patch.'

    tool="$RUN_DIR/magiskboot"
    unzip -p "$ZIP" lib/x86_64/libmagiskboot.so > "$tool" || fail 'Cannot extract the verified host MagiskBoot.'
    chmod 700 "$tool" || fail 'Cannot make MagiskBoot executable.'
    unzip -p "$ZIP" "lib/$sys_abi/libmagiskinit.so" > "$RUN_DIR/expected-magiskinit" || fail 'Cannot extract the matching Magisk init binary.'
    [[ -s "$RUN_DIR/expected-magiskinit" ]] || fail 'The matching Magisk init binary is empty.'
    expected_init_hash=$(sha256sum "$RUN_DIR/expected-magiskinit"); expected_init_hash=${expected_init_hash%% *}
    mkdir "$RUN_DIR/unpack-before" || fail 'Cannot create unpack directory.'
    (cd "$RUN_DIR/unpack-before" && "$tool" unpack "$RUN_DIR/BOOT-before.img" > "$RUN_DIR/unpack-before.log" 2>&1) || \
        fail 'Cannot unpack the BOOT backup with MagiskBoot.'
    [[ -f "$RUN_DIR/unpack-before/ramdisk.cpio" ]] || fail 'The stock BOOT has no ramdisk; recovery installation is not appropriate.'
    rc_test=0
    (cd "$RUN_DIR/unpack-before" && "$tool" cpio ramdisk.cpio test > "$RUN_DIR/ramdisk-before.log" 2>&1) || rc_test=$?
    [[ "$rc_test" == 0 ]] || fail "Stock ramdisk test returned $rc_test, not 0."

    printf '\n%s\n' \
        'BEFORE CONTINUING:' \
        '  * Magisk recovery install will modify BOOT and may change /data, /system or /cache.' \
        '  * Your current config requests KEEPVERITY=false and KEEPFORCEENCRYPT=false:' \
        '    integrity verification and forced-encryption flags may be disabled.' \
        '    This does not necessarily decrypt data that is already encrypted.' \
        '  * It may still boot-loop. A full firmware restore or wipe may be necessary.' \
        '    After reboot, ADB might be unavailable and hardware keys may be needed for recovery.' \
        '  * The script uses the existing TWRP; it does not reinstall it. Knox may already be tripped.'
    printf 'Type INSTALL to accept these risks and attempt Magisk installation and reboot: '
    IFS= read -r output || fail 'No confirmation received; installation cancelled.'
    [[ "$output" == 'INSTALL' ]] || fail 'Not confirmed; installation cancelled.'

    check_recovery
    check_flags
    check_magisk_target
    local remote_zip="/tmp/p585-magisk-${RUN_DIR##*/}.zip"
    tmp_root=$(adb_text shell 'readlink -f /tmp') || fail 'Cannot verify recovery temporary storage.'
    [[ "$tmp_root" == /* && "$tmp_root" != '/dev/tmp' && "$tmp_root" != /dev/tmp/* ]] || \
        fail 'Recovery /tmp overlaps Magisk installer temporary storage; no ZIP was staged.'
    if timeout 20 "${ADB[@]}" shell "test -e '$remote_zip'" >/dev/null 2>&1; then
        fail 'The chosen temporary ZIP already exists; refusing to overwrite it.'
    fi
    printf '\n%s\n' 'Staging the verified Magisk ZIP in recovery temporary storage...'
    timeout 180 "${ADB[@]}" push "$ZIP" "$remote_zip" || fail 'Could not stage the ZIP; no install was started.'
    data_hash=$(timeout 180 "${ADB[@]}" exec-out cat "$remote_zip" | sha256sum) || fail 'Cannot verify the staged ZIP.'
    data_hash=${data_hash%% *}
    [[ "$data_hash" == "$MAGISK_SHA256" ]] || fail 'Staged ZIP hash does not match; no install was started.'
    check_recovery
    check_flags
    check_magisk_target

    printf '%s\n' 'Starting the official Magisk recovery installer. Do not unplug the tablet.'
    PHASE='installing'
    if ! timeout 900 "${ADB[@]}" shell twrp install "$remote_zip" 2>&1 | tee "$RUN_DIR/install.log"; then
        fail 'TWRP installation command failed or timed out.'
    fi
    capture_recovery_log
    if grep -aiE 'Another threaded action|operation in progress|Error installing zip|Updater process ended with ERROR' "$RUN_DIR/install.log" >/dev/null; then
        fail 'TWRP reported that installation failed or was busy.'
    fi
    grep -aF -- '- Flashing new boot image' "$RUN_DIR/install.log" >/dev/null || fail 'Installer did not report flashing BOOT.'
    grep -aF -- '- Done' "$RUN_DIR/install.log" >/dev/null || fail 'Installer did not report completion.'
    target=$(sed -nE 's/^.*Target image:[[:space:]]*([^[:space:]]+).*$/\1/p' "$RUN_DIR/install.log" | tail -n 1 | tr -d '\r')
    [[ "$target" =~ ^/dev/block/[A-Za-z0-9_./-]+$ ]] || fail 'Installer did not report a recognizable target.'
    target_resolved=$(resolve_block "$target") || fail 'Cannot resolve the installer-reported target.'
    [[ "$target_resolved" == "$BOOT_DEV" ]] || fail "Installer reported an unexpected target: $target_resolved"
    PHASE='installed'

    printf '%s\n' 'Reading back BOOT and checking that RECOVERY is unchanged...'
    timeout 600 "${ADB[@]}" exec-out cat "$BOOT_DEV" > "$RUN_DIR/BOOT-after.img" || fail 'Cannot read back BOOT.'
    [[ "$(stat -c '%s' "$RUN_DIR/BOOT-after.img")" == "${sizes[0]}" ]] || fail 'BOOT readback is incomplete.'
    hash=$(sha256sum "$RUN_DIR/BOOT-after.img"); hash=${hash%% *}
    after_hash=$(timeout 600 "${ADB[@]}" exec-out cat "$BOOT_DEV" | sha256sum) || fail 'Cannot reread BOOT.'
    after_hash=${after_hash%% *}
    [[ "$after_hash" == "$hash" ]] || fail 'The two BOOT reads differ; not rebooting.'
    after_hash=$(timeout 600 "${ADB[@]}" exec-out cat "$RECOVERY_DEV" | sha256sum) || fail 'Cannot read back RECOVERY.'
    after_hash=${after_hash%% *}
    [[ "$after_hash" == "${hashes[1]}" ]] || fail 'RECOVERY changed during installation. No automatic restore or reboot was attempted.'
    mkdir "$RUN_DIR/unpack-after" || fail 'Cannot create post-install unpack directory.'
    (cd "$RUN_DIR/unpack-after" && "$tool" unpack "$RUN_DIR/BOOT-after.img" > "$RUN_DIR/unpack-after.log" 2>&1) || \
        fail 'The new BOOT image could not be unpacked; not rebooting.'
    [[ -f "$RUN_DIR/unpack-after/ramdisk.cpio" ]] || fail 'Patched BOOT has no readable ramdisk; not rebooting.'
    rc_test=0
    (cd "$RUN_DIR/unpack-after" && "$tool" cpio ramdisk.cpio test > "$RUN_DIR/ramdisk-after.log" 2>&1) || rc_test=$?
    [[ "$rc_test" == 1 ]] || fail "Magisk ramdisk marker test returned $rc_test, not 1; not rebooting."
    (cd "$RUN_DIR/unpack-after" && "$tool" cpio ramdisk.cpio 'extract init patched-init' > "$RUN_DIR/init-extract.log" 2>&1) || \
        fail 'Cannot inspect the patched init binary; not rebooting.'
    chmod u+r "$RUN_DIR/unpack-after/patched-init" || fail 'Cannot read the patched init binary; not rebooting.'
    hash=$(sha256sum "$RUN_DIR/unpack-after/patched-init"); hash=${hash%% *}
    [[ "$hash" == "$expected_init_hash" ]] || fail 'Patched init does not match the verified Magisk APK; not rebooting.'
    (cd "$RUN_DIR/unpack-after" && "$tool" cpio ramdisk.cpio 'extract .backup/.magisk patched-config' > "$RUN_DIR/config-extract.log" 2>&1) || \
        fail 'Cannot inspect the patched ramdisk configuration; not rebooting.'
    chmod u+r "$RUN_DIR/unpack-after/patched-config" || fail 'Cannot read the patched configuration; not rebooting.'
    for line in KEEPVERITY=false KEEPFORCEENCRYPT=false RECOVERYMODE=false VENDORBOOT=false; do
        grep -Fx -- "$line" "$RUN_DIR/unpack-after/patched-config" >/dev/null || fail "Patched config is missing $line; not rebooting."
    done
    printf '%s\n' 'BOOT has Magisk markers and the expected flags; this does not yet prove working root.'
    sleep 4
    check_recovery
    printf '%s\n' 'Rebooting to Android. ADB may disappear; waiting up to 5 minutes for an authorized Android boot...'
    PHASE='rebooting'
    timeout 20 "${ADB[@]}" reboot || fail 'ADB reboot command failed; Android boot is unverified.'
    deadline=$((SECONDS + 300)); complete=''
    while (( SECONDS < deadline )); do
        state=$(timeout 5 "${ADB[@]}" get-state 2>/dev/null | tr -d '\r') || state=''
        if [[ "$state" == 'device' ]]; then
            complete=$(timeout 5 "${ADB[@]}" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r') || complete=''
            [[ "$complete" == '1' ]] && break
        fi
        sleep 3
    done
    [[ "$complete" == '1' ]] || fail 'Android boot was not verified. It may still be looping, or Android ADB may be disabled/unauthorized. Do not claim root; inspect recovery logs.'
    [[ "$(prop ro.product.model)" == "$MODEL" ]] || fail 'The booted Android model does not match.'

    # The verified APK and ZIP have identical bytes; use an .apk extension for adb install.
    cp "$ZIP" "$RUN_DIR/Magisk-v30.7.apk" || fail 'Cannot prepare the verified Magisk APK.'
    manager_installed=1
    if ! timeout 120 "${ADB[@]}" install -r "$RUN_DIR/Magisk-v30.7.apk"; then
        manager_installed=0
        printf '%s\n' 'Magisk app installation failed; attempting a root check anyway.'
    fi
    printf '%s\n' 'Checking su. If Android shows a Magisk authorization prompt for ADB shell, approve it.'
    root_rc=0
    root_output=$(timeout 45 "${ADB[@]}" shell 'su -c id' 2>&1 | tr -d '\r') || root_rc=$?
    printf '%s\n' "$root_output"
    if (( root_rc != 0 )) || ! grep -Eq '(^|[[:space:]])uid=0(\(|[[:space:]]|$)' <<< "$root_output"; then
        fail 'Android booted, but root was not verified. A Magisk authorization prompt or app setup may still be needed.'
    fi
    printf '%s\n' 'Root verified: su returned uid=0.'
    (( manager_installed == 1 )) || printf '%s\n' 'Note: install the verified Magisk app when Android ADB/app installation is available.'

    after_hash=$(timeout 600 "${ADB[@]}" exec-out "su -c 'cat $RECOVERY_DEV'" | sha256sum) || \
        fail 'Root works, but the recovery partition could not be verified after Android boot.'
    after_hash=${after_hash%% *}
    [[ "$after_hash" == "${hashes[1]}" ]] || fail 'Root works, but recovery no longer matches its TWRP-time backup. No restore was attempted.'
    PHASE='finished'
    printf '\n%s\n' 'SUCCESS: Android booted, su returned uid=0, and RECOVERY matches the backup made in TWRP.'
    printf 'Private backups and logs: %s\n' "$RUN_DIR"
}

main "$@"
