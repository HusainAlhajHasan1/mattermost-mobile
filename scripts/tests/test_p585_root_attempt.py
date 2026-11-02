#!/usr/bin/env python3
"""Simulate the P585 helper, including legacy ADB shells with unreliable exit codes.

No Android device or network access is used. Firmware images, the APK, and ADB
are replaced with tiny temporary fixtures; no real partition is read or written.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / "p585-root-attempt.sh"
SERIAL = "aaaaaaaaaaaaaaaa"  # Synthetic test identifier, not a device serial.

MAGISKBOOT = r"""#!/usr/bin/env bash
set -u
if [[ "$1" == unpack ]]; then
    if [[ "$2" == *BOOT-after.img ]]; then echo after > ramdisk.cpio; else echo before > ramdisk.cpio; fi
    exit 0
fi
if [[ "$1" == cpio && "$3" == test ]]; then
    if [[ $(cat "$2") == after && "${SCENARIO:-}" != marker_missing ]]; then exit 1; fi
    exit 0
fi
if [[ "$1" == cpio && "$3" == 'extract init patched-init' ]]; then
    if [[ "${SCENARIO:-}" == init_mismatch ]]; then printf bad > patched-init; else printf mockinit > patched-init; fi
    chmod 000 patched-init
    exit 0
fi
if [[ "$1" == cpio && "$3" == 'extract .backup/.magisk patched-config' ]]; then
    printf 'KEEPVERITY=false\nKEEPFORCEENCRYPT=false\nRECOVERYMODE=false\nVENDORBOOT=false\n' > patched-config
    chmod 000 patched-config
    exit 0
fi
exit 1
"""

ADB_MOCK = r'''#!/usr/bin/env python3
import json, os, pathlib, re, shutil, subprocess, sys

root = pathlib.Path(os.environ['FAKE_ADB_DIR'])
scenario = os.environ['SCENARIO']
args = sys.argv[1:]
with (root / 'calls.jsonl').open('a') as f:
    f.write(json.dumps(args) + '\n')
if args[:2] != ['-s', os.environ['P585_SERIAL']] or len(args) < 3:
    sys.exit(2)
a = args[2:]

def emit(message):
    print(message)

def devfile(path):
    return root / (path.rsplit('/', 1)[-1] + '.bin')

def remote_result(code=0, stdout='', stderr=''):
    # Legacy recovery ADB can report 0 even when the remote shell failed.
    sys.stdout.write(stdout)
    sys.stderr.write(stderr)
    sys.exit(code if os.environ.get('SHELL_V2') == '1' else 0)

def run_remote(command):
    proc = subprocess.run(['/bin/sh', '-c', command], text=True, capture_output=True)
    remote_result(proc.returncode, proc.stdout, proc.stderr)

if a[0] == 'get-state':
    emit((root / 'state').read_text().strip())
    sys.exit(0)
if a[0] == 'push':
    shutil.copyfile(a[1], root / 'staged.zip')
    if scenario == 'bad_staging':
        with (root / 'staged.zip').open('ab') as f:
            f.write(b'corrupt')
    emit('pushed')
    sys.exit(0)
if a[0] == 'reboot':
    (root / 'state').write_text('recovery' if scenario == 'no_boot' else 'device')
    if scenario == 'recovery_after_reboot':
        (root / 'mmcblk0p10.bin').write_bytes(b'Z' * 4096)
    sys.exit(0)
if a[0] == 'install':
    emit('Failure [INSTALL_FAILED]' if scenario == 'manager_failure' else 'Success')
    sys.exit(0)
if a[0] == 'exec-out':
    if a[1] == 'cat':
        path = a[2]
        if path == '/proc/last_kmsg':
            sys.stdout.buffer.write(b'init: failed to mount /system\n')
            sys.exit(0)
        if path == '/tmp/recovery.log':
            sys.stdout.buffer.write(b'TWRP recovery log\n')
            sys.exit(0)
        if path.startswith('/tmp/'):
            sys.stdout.buffer.write((root / 'staged.zip').read_bytes())
            sys.exit(0)
        data = devfile(path).read_bytes()
        if scenario == 'short_backup' and path.endswith('mmcblk0p9'):
            data = data[:1024]
        sys.stdout.buffer.write(data)
        sys.exit(0)
    match = re.search(r'cat (/dev/block/mmcblk0p\d+)', a[1])
    if match:
        sys.stdout.buffer.write(devfile(match.group(1)).read_bytes())
        sys.exit(0)
    sys.exit(2)
if a[0] != 'shell':
    sys.exit(2)
cmd = ' '.join(a[1:])
if cmd.startswith('getprop '):
    name = cmd.split()[1]
    props = {'ro.product.model': 'WRONG' if scenario == 'wrong_model' else 'SM-P585',
             'ro.product.device': 'gtanotexllte', 'ro.bootloader': 'P585JXS3CTL1',
             'ro.boot.flash.locked': '', 'sys.boot_completed': '1'}
    remote_result(stdout=props.get(name, '') + '\n')
if cmd == 'twrp -v':
    remote_result(stdout='TWRP openrecoveryscript command line tool, TWRP version 3.3.1-0\n')
if cmd.startswith('twrp print '):
    remote_result(stdout='Failed, operation in progress\n' if scenario == 'busy_probe' else 'P585_PREFLIGHT_OK\n')
if cmd.startswith('twrp install '):
    if scenario == 'install_error':
        remote_result(1, 'Error installing zip file\n')
    (root / 'mmcblk0p9.bin').write_bytes(b'A' * 4096)
    if scenario == 'recovery_changed':
        (root / 'mmcblk0p10.bin').write_bytes(b'Z' * 4096)
    lines = '- Target image: /dev/block/mmcblk0p9\n- Flashing new boot image\n'
    if scenario != 'no_installer_done':
        lines += '- Done\n'
    remote_result(stdout=lines)
if cmd == 'cat /etc/recovery.fstab':
    entries = [('/boot','emmc','BOOT'), ('/recovery','emmc','RECOVERY'),
               ('/efs','ext4','EFS'), ('/m9kefs1','emmc','m9kefs1'),
               ('/m9kefs2','emmc','m9kefs2'), ('/m9kefs3','emmc','m9kefs3'),
               ('/system','ext4','SYSTEM')]
    remote_result(stdout=''.join(f'{p} {fs} /dev/block/bootdevice/by-name/{n}\n' for p, fs, n in entries))
if cmd.startswith('readlink -f '):
    path = cmd.split(' ', 2)[2].strip("'\"")
    if path == '/tmp':
        remote_result(stdout=('/dev/tmp' if scenario == 'tmp_overlap' else '/tmp') + '\n')
    name = path.rsplit('/', 1)[-1]
    name = {'BOOT':'mmcblk0p9', 'RECOVERY':'mmcblk0p10', 'EFS':'mmcblk0p11',
            'm9kefs1':'mmcblk0p12', 'm9kefs2':'mmcblk0p13', 'm9kefs3':'mmcblk0p14',
            'SYSTEM':'mmcblk0p18'}.get(name, name)
    remote_result(stdout='/dev/block/' + name + '\n')
if 'P585_BLOCK_OK' in cmd:
    match = re.search(r"\[ -b '([^']+)' \]", cmd)
    if not match:
        remote_result(1, stderr='invalid block test\n')
    # Execute the actual remote shell syntax against a harmless regular file.
    candidate = root / 'mock-block'
    if scenario != 'not_block' or not match.group(1).endswith('mmcblk0p9'):
        candidate.write_text('block fixture')
    else:
        candidate.unlink(missing_ok=True)
    run_remote(cmd.replace("[ -b '" + match.group(1) + "' ]", "[ -f '" + str(candidate) + "' ]"))
if cmd.startswith('cat /sys/class/block/'):
    match = re.search(r'/sys/class/block/(mmcblk0p\d+)/size', cmd)
    remote_result(stdout=str(devfile(match.group(1)).stat().st_size // 512) + '\n')
if cmd.startswith('cat /sys/class/power_supply/battery/capacity'):
    remote_result(stdout='100\n')
if cmd.startswith('awk ') and '/proc/mounts' in cmd:
    for mount, dev, mode in [('/system','mmcblk0p17' if scenario == 'wrong_system' else 'mmcblk0p18',
                               (root / 'system-mode').read_text().strip()),
                              ('/data','mmcblk0p22','rw'), ('/cache','mmcblk0p19','rw')]:
        if '"' + mount + '"' not in cmd:
            continue
        if mode == 'unmounted':
            remote_result()
        if '{print}' in cmd:
            line = f'/dev/block/{dev} {mount} ext4 {mode},seclabel 0 0\n'
            if scenario == 'multiple_system':
                line += line
        else:
            line = f'/dev/block/{dev} ext4 {mode},seclabel\n'
        remote_result(stdout=line)
    remote_result(1)
if 'P585_MOUNT_OK' in cmd:
    if scenario == 'mount_failure':
        remote_result(1, 'P585_MOUNT_FAILED\n')
    (root / 'system-mode').write_text('ro')
    remote_result(stdout='P585_MOUNT_OK\n')
if cmd.startswith("grep '^ro.build.version.incremental='"):
    remote_result(stdout='ro.build.version.incremental=P585JXS3CTL1\n')
if cmd.startswith("grep '^ro.product.cpu.abi='"):
    remote_result(stdout='ro.product.cpu.abi=armeabi-v7a\n')
if 'pidof recovery' in cmd and '/environ' in cmd:
    if scenario == 'unsafe_env':
        remote_result(stdout='VENDORBOOT=true\nP585_ENV_CHECK_OK\n')
    if scenario == 'no_env_access':
        remote_result(1)
    remote_result(stdout='P585_ENV_CHECK_OK\n')
if 'P585_CONFIG_OK' in cmd:
    if scenario == 'data_config_error' and '/data/.magisk' in cmd:
        remote_result(1, '\nP585_CONFIG_ERROR:/data/.magisk\n')
    # Execute the real quoting and printf logic against temporary configs.
    cmd = cmd.replace('/data/.magisk', str(root / 'data.magisk'))
    cmd = cmd.replace('/cache/.magisk', str(root / 'cache.magisk'))
    run_remote(cmd)
if cmd.startswith('cat /proc/cmdline;'):
    suffix = ' androidboot.slot_suffix=_a' if scenario == 'slot_a' else ''
    remote_result(stdout='console=ttySAC2' + suffix + '\n')
if 'for n in ramdisk kern-a' in cmd:
    remote_result(stdout=('/dev/block/mmcblk0p10' if scenario == 'wrong_target' else '/dev/block/mmcblk0p9') + '\n')
if 'P585_PATH_EXISTS' in cmd:
    if scenario == 'unknown_temp':
        remote_result(1)
    match = re.search(r"\[ -e '([^']+)' \]", cmd)
    if not match:
        remote_result(1, stderr='invalid temp test\n')
    real = root / 'remote-tmp' / pathlib.Path(match.group(1)).name
    if scenario == 'temp_collision':
        real.write_text('existing file')
    elif scenario == 'temp_symlink':
        real.symlink_to(root / 'missing-target')
    cmd = cmd.replace("'" + match.group(1) + "'", "'" + str(real) + "'")
    run_remote(cmd)
if cmd == 'su -c id':
    if scenario == 'su_denied':
        remote_result(1, 'su: Permission denied\n')
    remote_result(stdout='uid=0(root) gid=0(root)\n')
remote_result(1, stderr='unknown fake adb shell command: ' + repr(cmd) + '\n')
'''


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class P585HelperTest(unittest.TestCase):
    def run_scenario(self, scenario: str, confirmation: str = "INSTALL", *, shell_v2: bool = False):
        with tempfile.TemporaryDirectory(prefix="p585-helper-test-") as directory:
            root = Path(directory)
            fake = root / "fake"
            base = root / "base"
            binary = root / "bin"
            for path in (fake, base, binary, fake / "remote-tmp"):
                path.mkdir()
            (fake / "state").write_text("recovery")
            (fake / "system-mode").write_text("unmounted" if scenario in {"unmounted", "mount_failure"} else "rw" if scenario == "rw" else "ro")
            data_flags = "KEEPVERITY=false\nKEEPFORCEENCRYPT=false\nRECOVERYMODE=" + ("true" if scenario == "bad_config" else "false") + "\n"
            if scenario == "no_config_newline":
                data_flags = data_flags.rstrip("\n")
            (fake / "data.magisk").write_text(data_flags)
            (fake / "cache.magisk").write_text("KEEPVERITY=false\nKEEPFORCEENCRYPT=false\n")
            if scenario == "cache_absent":
                (fake / "cache.magisk").unlink()
            images = {"mmcblk0p9": b"S" * 4096, "mmcblk0p10": b"R" * 4096,
                      "mmcblk0p11": b"E" * 2048, "mmcblk0p12": b"1" * 1024,
                      "mmcblk0p13": b"2" * 1024, "mmcblk0p14": b"3" * 1024}
            for name, content in images.items():
                (fake / (name + ".bin")).write_bytes(content)
            (binary / "adb").write_text(ADB_MOCK)
            (binary / "adb").chmod(0o755)
            (binary / "sleep").write_text("#!/bin/sh\nexit 0\n")
            (binary / "sleep").chmod(0o755)
            (binary / "uname").write_text("#!/bin/sh\necho x86_64\n")
            (binary / "uname").chmod(0o755)
            (binary / "df").write_text("#!/bin/sh\nprintf 'Filesystem 1024-blocks Used Available Capacity Mounted on\\nfixture 9999999 0 9999999 0%% /\\n'\n")
            (binary / "df").chmod(0o755)
            archive = base / "Magisk-v30.7.zip"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("lib/x86_64/libmagiskboot.so", MAGISKBOOT)
                z.writestr("lib/armeabi-v7a/libmagiskinit.so", b"mockinit")
            text = SCRIPT.read_text()
            text = re.sub(r"readonly SERIAL_SHA256='[0-9a-f]+'", f"readonly SERIAL_SHA256='{digest(SERIAL.encode())}'", text)
            text = text.replace("e0d32d2123532860f97123d927b1bb86c4e08e6fd8a48bfc6b5bee0afae9ebd5", digest(archive.read_bytes()))
            text = text.replace("a83424bca63d5d30d2da54cfa2c952a1e21982c8c05265fb03e224e9e6fdf015", digest(b"S" * 1024))
            text = text.replace("readonly STOCK_BOOT_BYTES=18882848", "readonly STOCK_BOOT_BYTES=1024")
            if scenario == "no_boot":
                text = text.replace("deadline=$((SECONDS + 300))", "deadline=$((SECONDS + 1))")
            test_script = root / "helper.sh"
            test_script.write_text(text)
            env = os.environ.copy()
            env.update(PATH=str(binary) + os.pathsep + env["PATH"], P585_SERIAL=SERIAL,
                       P585_WORKDIR=str(base), FAKE_ADB_DIR=str(fake), SCENARIO=scenario,
                       SHELL_V2="1" if shell_v2 else "0")
            result = subprocess.run(["bash", str(test_script)], input=confirmation + "\n", env=env,
                                    text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
            calls_path = fake / "calls.jsonl"
            calls = [json.loads(line)[2:] for line in calls_path.read_text().splitlines()] if calls_path.exists() else []
            return result.returncode, result.stdout, calls

    def assert_flow(self, scenario, expected_status, expected_text, push, reboot, *, confirmation="INSTALL", shell_v2=False):
        status, output, calls = self.run_scenario(scenario, confirmation, shell_v2=shell_v2)
        self.assertEqual(status, expected_status, output)
        self.assertIn(expected_text, output)
        self.assertEqual(any(c[0] == "push" for c in calls), push, output)
        self.assertEqual(any(c[0] == "reboot" for c in calls), reboot, output)
        return output, calls

    def test_legacy_absent_temp_and_v2_shell_complete(self):
        for shell_v2 in (False, True):
            with self.subTest(shell_v2=shell_v2):
                self.assert_flow("success", 0, "SUCCESS: Android booted", True, True, shell_v2=shell_v2)

    def test_existing_file_and_dangling_symlink_stop_before_push(self):
        for scenario in ("temp_collision", "temp_symlink"):
            with self.subTest(scenario=scenario):
                self.assert_flow(scenario, 1, "temporary ZIP already exists", False, False)

    def test_unknown_temp_result_stops_before_push(self):
        self.assert_flow("unknown_temp", 1, "Cannot determine whether the temporary ZIP path is safe", False, False)

    def test_block_check_uses_marker_not_legacy_shell_status(self):
        self.assert_flow("not_block", 1, "Cannot resolve /dev/block/bootdevice/by-name/BOOT", False, False)

    def test_unreadable_priority_config_is_not_silently_skipped(self):
        self.assert_flow("data_config_error", 1, "Cannot safely read Magisk config files", False, False)

    def test_config_concatenation_matches_magisk_without_final_newline(self):
        self.assert_flow("no_config_newline", 1, "Unexpected Magisk setting: RECOVERYMODE=", False, False)

    def test_absent_cache_config_is_distinguished_from_read_failure(self):
        self.assert_flow("cache_absent", 0, "SUCCESS: Android booted", True, True)

    def test_failed_readonly_mount_stops_despite_successful_adb_status(self):
        self.assert_flow("mount_failure", 1, "Could not mount /system read-only", False, False)

    def test_existing_rw_and_new_readonly_mount(self):
        for scenario in ("rw", "unmounted"):
            with self.subTest(scenario=scenario):
                self.assert_flow(scenario, 0, "SUCCESS: Android booted", True, True)

    def test_rejects_wrong_identity_target_and_flags(self):
        for scenario, message in (("wrong_model", "Device/build mismatch"),
                                  ("wrong_system", "Unexpected /system source"),
                                  ("multiple_system", "Multiple /system mounts"),
                                  ("bad_config", "RECOVERYMODE=true"),
                                  ("unsafe_env", "VENDORBOOT=true"),
                                  ("no_env_access", "Cannot inspect the TWRP process environment"),
                                  ("slot_a", "Unexpected boot slot"),
                                  ("wrong_target", "boot-target check failed")):
            with self.subTest(scenario=scenario):
                self.assert_flow(scenario, 1, message, False, False)

    def test_no_consent_or_unverified_zip_never_installs(self):
        self.assert_flow("success", 1, "Not confirmed", False, False, confirmation="NO")
        self.assert_flow("bad_staging", 1, "Staged ZIP hash does not match", True, False)
        self.assert_flow("tmp_overlap", 1, "overlaps Magisk installer", False, False)
        self.assert_flow("short_backup", 1, "Incomplete BOOT backup", False, False)

    def test_failure_or_incomplete_installer_never_reboots(self):
        for scenario, message in (("busy_probe", "TWRP did not confirm"),
                                  ("install_error", "installation failed or was busy"),
                                  ("no_installer_done", "Installer did not report completion"),
                                  ("recovery_changed", "RECOVERY changed during installation"),
                                  ("marker_missing", "marker test returned 0"),
                                  ("init_mismatch", "Patched init does not match")):
            with self.subTest(scenario=scenario):
                self.assert_flow(scenario, 1, message, scenario != "busy_probe", False)

    def test_no_false_root_success(self):
        self.assert_flow("no_boot", 1, "Android boot was not verified", True, True)
        self.assert_flow("su_denied", 1, "root was not verified", True, True)
        self.assert_flow("recovery_after_reboot", 1, "recovery no longer matches", True, True)

    def test_manager_failure_is_not_reported_as_confirmed(self):
        output, _ = self.assert_flow("manager_failure", 0, "Root verified: su returned uid=0", True, True)
        self.assertIn("Magisk app installation was not confirmed", output)
        self.assertIn("install the verified Magisk app", output)


if __name__ == "__main__":
    unittest.main()
