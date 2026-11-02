import hashlib
import importlib.util
import io
import os
import pathlib
import struct
import subprocess
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare = load("p585_encma_prepare", ROOT / "scripts/p585-encma/prepare_source.py")
inspect = load("p585_encma_inspect", ROOT / "scripts/p585-encma/inspect_apks.py")


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.path = self.root / "android/build.gradle"
        self.path.parent.mkdir()
        self.content = b"buildscript {\n    ext {\n" + prepare.OLD_SETTING + b"    }\n}\n"
        self.path.write_bytes(self.content)

    def expected_hash(self, content):
        return mock.patch.object(prepare, "BUILD_GRADLE_SHA256", hashlib.sha256(content).hexdigest())

    def test_only_the_exact_setting_changes(self):
        with self.expected_hash(self.content):
            result = prepare.prepare(self.root)
        expected = self.content.replace(prepare.OLD_SETTING, prepare.NEW_SETTING)
        self.assertEqual(self.path.read_bytes(), expected)
        self.assertEqual(result, hashlib.sha256(expected).hexdigest())

    def test_unexpected_source_is_not_modified(self):
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            prepare.prepare(self.root)
        self.assertEqual(self.path.read_bytes(), self.content)

    def test_ambiguous_setting_is_not_modified_even_if_hash_matches(self):
        content = self.content + prepare.OLD_SETTING
        self.path.write_bytes(content)
        with self.expected_hash(content), self.assertRaisesRegex(ValueError, "single"):
            prepare.prepare(self.root)
        self.assertEqual(self.path.read_bytes(), content)

    def test_already_modified_source_is_not_changed(self):
        content = self.content.replace(prepare.OLD_SETTING, prepare.NEW_SETTING)
        self.path.write_bytes(content)
        with self.expected_hash(content), self.assertRaisesRegex(ValueError, "single"):
            prepare.prepare(self.root)
        self.assertEqual(self.path.read_bytes(), content)

    def test_missing_source_fails_without_creating_file(self):
        self.path.unlink()
        with self.assertRaises(FileNotFoundError):
            prepare.prepare(self.root)
        self.assertFalse(self.path.exists())


def elf(abi):
    elf_class, machine = inspect.ABIS[abi]
    data = bytearray(32)
    data[:4] = b"\x7fELF"
    data[4], data[5] = elf_class, 1
    struct.pack_into("<H", data, 18, machine)
    return bytes(data)


def make_apk(path, abi, libs=None, include_bundle=True, wrong_elf=None, extra_abi=None):
    if libs is None:
        libs = inspect.REQUIRED_LIBS
    with zipfile.ZipFile(path, "w") as apk:
        apk.writestr("AndroidManifest.xml", b"synthetic test manifest")
        if include_bundle:
            apk.writestr("assets/index.android.bundle", b"synthetic bundle")
        for name in libs:
            apk.writestr("lib/{}/{}".format(abi, name), elf(wrong_elf or abi))
        if extra_abi:
            apk.writestr("lib/{}/extra.so".format(extra_abi), elf(extra_abi))


class ApkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = pathlib.Path(self.temp.name)
        self.arm = self.dir / "arm.apk"
        self.x86 = self.dir / "x86.apk"
        make_apk(self.arm, "armeabi-v7a")
        make_apk(self.x86, "x86_64")

    def test_compatible_synthetic_splits_pass(self):
        with redirect_stdout(io.StringIO()):
            inspect.main(["inspect_apks.py", str(self.arm), str(self.x86)])
        self.assertEqual(inspect.inspect(self.arm, "armeabi-v7a"), inspect.REQUIRED_LIBS)

    def test_missing_wireguard_library_fails(self):
        make_apk(self.arm, "armeabi-v7a", inspect.REQUIRED_LIBS - {"libwg-go.so"})
        with self.assertRaisesRegex(ValueError, "libwg-go"):
            inspect.inspect(self.arm, "armeabi-v7a")

    def test_missing_rn_083_hermes_vm_fails(self):
        make_apk(self.arm, "armeabi-v7a", inspect.REQUIRED_LIBS - {"libhermesvm.so"})
        with self.assertRaisesRegex(ValueError, "libhermesvm"):
            inspect.inspect(self.arm, "armeabi-v7a")

    def test_wrong_elf_class_and_machine_fail(self):
        make_apk(self.arm, "armeabi-v7a", wrong_elf="x86_64")
        with self.assertRaisesRegex(ValueError, "ELF"):
            inspect.inspect(self.arm, "armeabi-v7a")

    def test_mixed_abi_split_fails(self):
        make_apk(self.arm, "armeabi-v7a", extra_abi="x86_64")
        with self.assertRaisesRegex(ValueError, "unexpected native ABIs"):
            inspect.inspect(self.arm, "armeabi-v7a")

    def test_missing_js_bundle_fails(self):
        make_apk(self.arm, "armeabi-v7a", include_bundle=False)
        with self.assertRaisesRegex(ValueError, "index.android.bundle"):
            inspect.inspect(self.arm, "armeabi-v7a")

    def test_missing_arm_library_fails_parity_check(self):
        make_apk(self.x86, "x86_64", inspect.REQUIRED_LIBS | {"libneeded.so"})
        with self.assertRaisesRegex(SystemExit, "missing on ARM"):
            inspect.main(["inspect_apks.py", str(self.arm), str(self.x86)])

    def test_non_zip_fails(self):
        self.arm.write_bytes(b"not an APK")
        with self.assertRaisesRegex(SystemExit, "APK content verification failed"):
            inspect.main(["inspect_apks.py", str(self.arm), str(self.x86)])


class EmulatorSmokeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = pathlib.Path(self.temp.name)
        self.apk = self.dir / "x86.apk"
        self.apk.write_bytes(b"synthetic")
        self.log = self.dir / "commands.txt"
        fake_adb = self.dir / "adb"
        fake_adb.write_text('''#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKE_LOG"
case "$*" in
    'get-serialno') echo "${FAKE_SERIAL:-emulator-5554}" ;;
    *'shell getprop ro.kernel.qemu') echo "${FAKE_QEMU:-1}" ;;
    *'shell getprop ro.build.version.sdk') echo "${FAKE_SDK:-27}" ;;
    *'shell getprop ro.product.cpu.abilist') echo "${FAKE_ABIS:-x86_64,x86}" ;;
    *'shell am start -W '*) echo 'Status: ok' ;;
    *'shell uiautomator dump '*) [[ "${FAKE_SCREEN:-1}" = 1 ]] ;;
    *'exec-out cat '*) echo '<hierarchy><node text="VPN connection"/><node text="WireGuard configuration"/></hierarchy>' ;;
    *'shell pidof '*) echo 1234 ;;
esac
''')
        fake_adb.chmod(0o755)
        fake_sleep = self.dir / "sleep"
        fake_sleep.write_text("#!/usr/bin/env bash\nexit 0\n")
        fake_sleep.chmod(0o755)
        self.env = os.environ.copy()
        self.env.update({"PATH": str(self.dir) + os.pathsep + self.env.get("PATH", ""),
                         "FAKE_LOG": str(self.log)})
        self.script = ROOT / "scripts/p585-encma/emulator_smoke.sh"

    def run_smoke(self, **extra):
        env = self.env.copy()
        env.update(extra)
        result = subprocess.run(["bash", str(self.script), str(self.apk)], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, timeout=15)
        commands = self.log.read_text().splitlines() if self.log.exists() else []
        return result, commands

    def test_fake_emulator_launches_without_attempting_vpn_connection(self):
        result, commands = self.run_smoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("entry screen rendered", result.stdout)
        self.assertEqual(commands[0], "get-serialno")
        self.assertTrue(all(c.startswith("-s emulator-5554 ") for c in commands[1:]))
        self.assertTrue(any(" install " in c for c in commands))
        self.assertFalse(any("vpn" in c.lower() for c in commands))

    def test_physical_device_serial_is_rejected_before_any_install(self):
        result, commands = self.run_smoke(FAKE_SERIAL="5203c66b523ca357")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(commands, ["get-serialno"])

    def test_non_emulator_and_wrong_android_are_rejected_before_install(self):
        for variables in ({"FAKE_QEMU": "0"}, {"FAKE_SDK": "30"}, {"FAKE_ABIS": "arm64-v8a"}):
            with self.subTest(variables=variables):
                self.log.unlink(missing_ok=True)
                result, commands = self.run_smoke(**variables)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(" install " in c for c in commands))

    def test_screen_must_render_to_pass(self):
        result, commands = self.run_smoke(FAKE_SCREEN="0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("did not reach", result.stderr)
        self.assertTrue(any(" logcat -d " in c for c in commands))


if __name__ == "__main__":
    unittest.main()
