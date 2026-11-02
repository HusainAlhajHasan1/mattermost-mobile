"""Offline tests: no network, Android device, or real account is required."""
import base64
import hashlib
import importlib.util
import itertools
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/p585-apps-install.py'
FIXTURES = ROOT / 'scripts/tests/fixtures/p585-apps'
spec = importlib.util.spec_from_file_location('p585_apps_install', SCRIPT)
apps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(apps)
SERIAL = '5203c66b523ca357'


def ui(package, values):
    root = ET.Element('hierarchy')
    container = ET.SubElement(root, 'node', package=package, enabled='true',
                              clickable='false', bounds='[0,0][1000,1000]')
    for index, value in enumerate(values):
        ET.SubElement(container, 'node', package=package, text=value, enabled='true',
                      clickable='true', bounds=f'[{index * 100},10][{index * 100 + 80},90]')
    return root


class TrustTests(unittest.TestCase):
    def test_official_signature_packets_are_pinned(self):
        for signature, expected in ((apps.FDROID_SIGNATURE, '541da8d024bda8324585f82d677aebe85bfd4b770428475e335845e62c9c4df2'),
                                    (apps.AURORA_SIGNATURE, '1a6bb0952e3275a55e1507b2afd003c4c1b2fb6b42dc585a588dee98c94d5b91')):
            lines = signature.splitlines()
            packet = base64.b64decode(''.join(lines[2:-2]))
            self.assertEqual(hashlib.sha256(packet).hexdigest(), expected)

    @unittest.skipUnless(shutil.which('gpgv'), 'gpgv required')
    def test_real_gpgv_accepts_valid_fixture_and_rejects_changed_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            payload = work / 'payload.apk'
            payload.write_bytes((FIXTURES / 'payload.bin').read_bytes())
            signature = (FIXTURES / 'payload.asc').read_text()
            with mock.patch.object(apps, 'SIGNING_FPR', 'CB2A5997CECF1B21FF6EA21C5C993FA25CFA668B'), \
                 mock.patch.object(apps, 'PRIMARY_FPR', '14599C9E06FDF94FCE010518B30784814F027256'):
                apps.verify_apk(payload, signature, FIXTURES / 'public-key.gpg', work)
                payload.write_bytes(b'tampered')
                with self.assertRaises(apps.Stop):
                    apps.verify_apk(payload, signature, FIXTURES / 'public-key.gpg', work)

    @unittest.skipUnless(shutil.which('gpgv'), 'gpgv required')
    def test_right_signature_but_wrong_pinned_signer_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            apk = work / 'payload.apk'
            apk.write_bytes((FIXTURES / 'payload.bin').read_bytes())
            with self.assertRaises(apps.Stop):
                apps.verify_apk(apk, (FIXTURES / 'payload.asc').read_text(),
                                FIXTURES / 'public-key.gpg', work)

    def test_zero_exit_without_validsig_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            apk = work / 'x.apk'
            apk.write_bytes(b'x')
            response = subprocess.CompletedProcess(['gpgv'], 0, b'', b'')
            with mock.patch.object(apps, 'execute', return_value=response):
                with self.assertRaises(apps.Stop):
                    apps.verify_apk(apk, apps.FDROID_SIGNATURE, work / 'key', work)

    def test_key_checksum_mismatch_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            def fake_download(_url, path, api=False):
                path.write_bytes(b'not the key')
            with mock.patch.object(apps, 'download', side_effect=fake_download):
                with self.assertRaisesRegex(apps.Stop, 'checksum'):
                    apps.get_key(work)


class DeviceSafetyTests(unittest.TestCase):
    def test_serial_guard_and_model_guard(self):
        for serial in ('', 'bad serial', 'someotherdevice'):
            with self.subTest(serial=serial), self.assertRaises(apps.Stop):
                apps.Device(serial)
        dev = apps.Device(SERIAL)
        values = ['device', 'OTHER', 'gtanotexllte', 'P585JXS3CTL1', '27', 'armeabi-v7a', '1']
        with mock.patch.object(dev, 'text', side_effect=values):
            with self.assertRaisesRegex(apps.Stop, 'does not match'):
                dev.preflight()

    def test_no_marker_means_failure_even_when_adb_exit_is_zero(self):
        dev = apps.Device(SERIAL)
        with mock.patch.object(dev, 'text', return_value='some output'):
            with self.assertRaisesRegex(apps.Stop, 'did not confirm'):
                dev.marked('true')

    def test_package_query_and_retained_package_block_install(self):
        dev = apps.Device(SERIAL)
        with mock.patch.object(dev, 'marked', return_value='package:org.fdroid.fdroid\npackage:some.other'):
            self.assertIn(apps.FDROID, dev.packages(True))
            with mock.patch.object(dev, 'run') as run:
                with self.assertRaisesRegex(apps.Stop, 'left untouched'):
                    dev.install(Path('/tmp/no.apk'), apps.FDROID)
                run.assert_not_called()

    def test_install_requires_success_and_postcondition(self):
        dev = apps.Device(SERIAL)
        result = subprocess.CompletedProcess([], 0, b'Success\n', b'')
        with mock.patch.object(dev, 'packages', side_effect=[set(), set()]), \
             mock.patch.object(dev, 'run', return_value=result) as run:
            with self.assertRaisesRegex(apps.Stop, 'not installed'):
                dev.install(Path('/tmp/client.apk'), apps.FDROID)
            self.assertNotIn('-r', run.call_args.args)
            self.assertEqual(run.call_args.args[:3], ('install', '--user', '0'))
        with mock.patch.object(dev, 'packages', return_value=set()), \
             mock.patch.object(dev, 'run', return_value=subprocess.CompletedProcess([], 0, b'', b'')):
            with self.assertRaisesRegex(apps.Stop, 'did not confirm'):
                dev.install(Path('/tmp/client.apk'), apps.FDROID)

    def test_appop_read_parses_only_one_known_operation(self):
        dev = apps.Device(SERIAL)
        for value, expected in [('No operations.', 'default'),
                                ('REQUEST_INSTALL_PACKAGES: ignore; time=1s ago', 'ignore'),
                                ('REQUEST_INSTALL_PACKAGES: allow', 'allow')]:
            with self.subTest(value=value), mock.patch.object(dev, 'marked', return_value=value):
                self.assertEqual(dev.appop(), expected)
        with mock.patch.object(dev, 'marked', return_value='Error: unknown operation'):
            with self.assertRaises(apps.Stop):
                dev.appop()


class UiTests(unittest.TestCase):
    def test_target_is_unique_enabled_clickable_and_in_foreground_package(self):
        root = ui(apps.AURORA, ['Instagram', 'Manual download', 'Install'])
        self.assertTrue(apps.instagram_visible(root, apps.AURORA))
        self.assertEqual(apps.tap_target(root, apps.AURORA, apps.INSTALL), (240, 50))
        node = list(root.iter('node'))[-1]
        node.set('enabled', 'false')
        self.assertIsNone(apps.tap_target(root, apps.AURORA, apps.INSTALL))
        self.assertIsNone(apps.tap_target(ui('other.package', ['Instagram', 'Install']),
                                          apps.AURORA, apps.INSTALL))
        self.assertIsNone(apps.tap_target(ui(apps.AURORA, ['Install', 'Install']),
                                          apps.AURORA, apps.INSTALL))
        # A matching Aurora node behind an unrelated foreground window must not be tapped.
        overlay = ui('com.android.systemui', ['Other'])
        overlay.append(root[0])
        self.assertIsNone(apps.tap_target(overlay, apps.AURORA, apps.INSTALL))

    def test_exact_languages_and_uppercase_installer(self):
        for name in ('Install', 'INSTALL', 'نصب', 'ثبِّت', 'تثبيت'):
            with self.subTest(name=name):
                self.assertIsNotNone(apps.tap_target(ui('com.android.packageinstaller', [name]),
                                                     'com.android.packageinstaller', apps.INSTALL))


class FakeDevice:
    serial = SERIAL

    def __init__(self, installer_package='com.android.packageinstaller', fail_open=False):
        self.mode = 'ignore'
        self.mode_history = []
        self.taps = []
        self.installed = {apps.FDROID, apps.AURORA}
        self.current = ''
        self.fail_open = fail_open
        self.queue = [
            ('welcome', ui(apps.AURORA, ['Welcome', 'Next', 'Skip'])),
            ('login', ui(apps.AURORA, ['Google', 'Anonymous'])),
            ('main', ui(apps.AURORA, ['Apps', 'Games', 'Updates'])),
            ('details', ui(apps.AURORA, ['Instagram', 'Manual download', 'Install'])),
            ('installer', ui(installer_package, ['Instagram', 'INSTALL'])),
        ]
        self.last = self.queue[-1]

    def start_aurora(self):
        pass

    def appop(self):
        return self.mode

    def set_appop(self, mode):
        self.mode_history.append(mode)
        self.mode = mode

    def open_instagram(self):
        if self.fail_open:
            raise apps.Stop('test failure')

    def packages(self, include_uninstalled=False):
        return set(self.installed)

    def dump_ui(self):
        if self.queue:
            self.last = self.queue.pop(0)
        self.current, root = self.last
        return root

    def tap(self, xy):
        self.taps.append((self.current, xy))
        if self.current == 'installer' and apps.foreground_package(self.last[1]) in apps.INSTALLERS:
            self.installed.add(apps.INSTAGRAM)


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, HOME=self.temp.name)
        self.env.start()
        self.sleep = mock.patch.object(apps.time, 'sleep', return_value=None)
        self.sleep.start()
        self.clock = mock.patch.object(apps.time, 'monotonic', side_effect=itertools.count())
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        self.sleep.stop()
        self.env.stop()
        self.temp.cleanup()

    def test_one_click_flow_and_restore(self):
        dev = FakeDevice()
        self.assertTrue(apps.instagram_via_aurora(dev, seconds=20))
        self.assertIn(apps.INSTAGRAM, dev.installed)
        self.assertEqual([x[0] for x in dev.taps], ['welcome', 'login', 'details', 'installer'])
        self.assertEqual(dev.mode_history, ['allow', 'ignore'])
        backups = list((Path(self.temp.name) / 'root-p585').glob('p585-apps-backup-*/aurora-appop-before.json'))
        self.assertEqual(len(backups), 1)
        self.assertIn('"previous_mode": "ignore"', backups[0].read_text())
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)

    def test_failure_restores_original_mode(self):
        dev = FakeDevice(fail_open=True)
        with self.assertRaisesRegex(apps.Stop, 'test failure'):
            apps.instagram_via_aurora(dev, seconds=10)
        self.assertEqual(dev.mode_history, ['allow', 'ignore'])
        self.assertNotIn(apps.INSTAGRAM, dev.installed)

    def test_interruption_restores_original_mode(self):
        dev = FakeDevice()
        with mock.patch.object(dev, 'open_instagram', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                apps.instagram_via_aurora(dev, seconds=10)
        self.assertEqual(dev.mode_history, ['allow', 'ignore'])

    def test_failed_permission_confirmation_still_restores(self):
        dev = FakeDevice()
        real_set = dev.set_appop
        def set_mode(mode):
            real_set(mode)
            if mode == 'allow':
                raise apps.Stop('lost confirmation')
        with mock.patch.object(dev, 'set_appop', side_effect=set_mode):
            with self.assertRaisesRegex(apps.Stop, 'lost confirmation'):
                apps.instagram_via_aurora(dev, seconds=10)
        self.assertEqual(dev.mode_history, ['allow', 'ignore'])

    def test_failed_restoration_reports_manual_command(self):
        dev = FakeDevice(fail_open=True)
        real_set = dev.set_appop
        def set_mode(mode):
            if mode == 'ignore':
                raise apps.Stop('disconnected')
            real_set(mode)
        with mock.patch.object(dev, 'set_appop', side_effect=set_mode), \
             mock.patch.object(apps, 'say') as say:
            with self.assertRaisesRegex(apps.Stop, 'manual restoration'):
                apps.instagram_via_aurora(dev, seconds=10)
        messages = '\n'.join(str(call) for call in say.call_args_list)
        self.assertIn('REQUEST_INSTALL_PACKAGES ignore', messages)
        self.assertIn('Original permission backup', messages)

    def test_foreign_installer_is_never_approved_and_partial_is_reported(self):
        dev = FakeDevice(installer_package='com.example.unknowninstaller')
        with self.assertRaisesRegex(apps.Stop, 'not confirmed installed'):
            apps.instagram_via_aurora(dev, seconds=8)
        self.assertNotIn('installer', [x[0] for x in dev.taps])
        self.assertEqual(dev.mode, 'ignore')

    def test_unrecognized_login_does_not_guess_coordinates(self):
        dev = FakeDevice()
        dev.queue = [('unknown', ui('com.android.systemui', ['Anonymous', 'Google']))]
        dev.last = dev.queue[0]
        with self.assertRaises(apps.Stop):
            apps.login_aurora(dev, seconds=5)
        self.assertEqual(dev.taps, [])

    def test_already_allowed_does_not_change_permission(self):
        dev = FakeDevice()
        dev.mode = 'allow'
        self.assertTrue(apps.instagram_via_aurora(dev, seconds=20))
        self.assertEqual(dev.mode_history, [])

    def test_existing_session_must_show_anonymous_account(self):
        for account, expected in [('Anonymous', True), ('Private account', False)]:
            with self.subTest(account=account):
                dev = FakeDevice()
                dev.queue = [
                    ('main', ui(apps.AURORA, ['Apps', 'Games', 'Updates', 'More'])),
                    ('account', ui(apps.AURORA, [account, 'Manage your account'])),
                ]
                dev.last = dev.queue[-1]
                if expected:
                    self.assertTrue(apps.login_aurora(dev, seconds=10))
                else:
                    with self.assertRaisesRegex(apps.Stop, 'not confirmed anonymous'):
                        apps.login_aurora(dev, seconds=10)
                self.assertEqual([x[0] for x in dev.taps], ['main'])
                self.assertEqual(dev.mode_history, [])

    def test_partial_result_is_nonzero_and_never_claims_success(self):
        with mock.patch.object(apps, 'say') as say:
            result = apps.summarize({apps.FDROID: 'installed and verified',
                                     apps.AURORA: 'installed and verified',
                                     apps.INSTAGRAM: 'not installed'})
        self.assertEqual(result, 1)
        self.assertIn('not installed', '\n'.join(str(x) for x in say.call_args_list))


if __name__ == '__main__':
    unittest.main()
