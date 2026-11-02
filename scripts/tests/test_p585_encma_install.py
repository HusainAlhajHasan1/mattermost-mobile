import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts' / 'p585-encma-install.py'
spec = importlib.util.spec_from_file_location('p585_encma_install', SCRIPT)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

FAKE_ADB = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
p = pathlib.Path(os.environ['FAKE_STATE'])
s = json.loads(p.read_text())
a = sys.argv[1:]
s['commands'].append(a)
def save(): p.write_text(json.dumps(s))
save()
if a[:2] != ['-s', '5203c66b523ca357']: sys.exit(9)
a = a[2:]
if a == ['get-state']: print('device'); sys.exit()
if a[:2] == ['shell', 'getprop']:
    print({'ro.product.model': s.get('model', 'SM-P585'),
           'ro.product.device': 'gtanotexllte', 'ro.build.version.incremental': 'P585JXS3CTL1',
           'ro.build.version.sdk': '27', 'ro.product.cpu.abi': 'armeabi-v7a',
           'sys.boot_completed': '1'}[a[2]]); sys.exit()
if a == ['shell', 'am', 'get-current-user']: print('0'); sys.exit()
if a[:3] == ['install', '--user', '0']:
    if 'com.encma.hxmain' in s['packages']:
        print('Failure [INSTALL_FAILED_ALREADY_EXISTS]'); sys.exit(1)
    if not s.get('false_success'): s['packages'].append('com.encma.hxmain'); save()
    print('Success'); sys.exit()
if a[:3] == ['shell', 'am', 'start']:
    if s.get('launch_failure'): print('Error: Activity not started'); sys.exit(1)
    print('Status: ok'); sys.exit()
if a == ['shell', 'pidof', 'com.encma.hxmain']:
    if s.get('no_process'): sys.exit(1)
    print('1234'); sys.exit()
if len(a) == 2 and a[0] == 'shell' and 'pm list packages' in a[1]:
    for package in s['packages']: print('package:' + package)
    if '-u --user' in a[1]:
        for package in s.get('retained', []): print('package:' + package)
    print('P585_COMMAND_FAILED' if s.get('marker_fail') else 'P585_PM_OK'); sys.exit()
sys.exit(7)
'''

FAKE_CURL = r'''#!/usr/bin/env python3
import os, shutil, sys
args = sys.argv[1:]
shutil.copyfile(os.environ['FAKE_ARCHIVE'], args[args.index('--output') + 1])
'''


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        fakebin = self.root / 'bin'
        fakebin.mkdir()
        for name, source in [('adb', FAKE_ADB), ('curl', FAKE_CURL)]:
            path = fakebin / name
            path.write_text(source)
            path.chmod(0o755)
        self.state = self.root / 'state.json'
        self.save({'packages': ['android'], 'retained': [], 'commands': []})
        self.apk = b'synthetic APK bytes for tests only'
        self.archive = self.root / 'artifact.zip'
        self.write_archive(app.APK_FILENAME, self.apk)
        env = {'PATH': str(fakebin) + os.pathsep + os.environ.get('PATH', ''),
               'FAKE_STATE': str(self.state), 'FAKE_ARCHIVE': str(self.archive),
               'P585_SERIAL': '5203c66b523ca357'}
        patchers = [mock.patch.dict(os.environ, env), mock.patch.object(app, 'ARTIFACT_ID', 123),
                    mock.patch.object(app, 'ARCHIVE_SHA256', app.file_hash(self.archive)),
                    mock.patch.object(app, 'APK_SIZE', len(self.apk)),
                    mock.patch.object(app, 'APK_SHA256', hashlib.sha256(self.apk).hexdigest()),
                    mock.patch.object(app.time, 'sleep', return_value=None)]
        for patcher in patchers:
            patcher.start()
        self.addCleanup(lambda: [patcher.stop() for patcher in reversed(patchers)])

    def write_archive(self, name, payload):
        with zipfile.ZipFile(self.archive, 'w', zipfile.ZIP_DEFLATED) as data:
            data.writestr(name, payload)

    def save(self, state):
        self.state.write_text(json.dumps(state))

    def load(self):
        return json.loads(self.state.read_text())

    def run_main(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = app.main()
        return result, output.getvalue(), self.load()

    def assert_no_install(self, state):
        self.assertFalse(any(command[2:3] == ['install'] for command in state['commands']))

    def test_pinned_artifact_installs_and_opens_without_vpn_commands(self):
        result, output, state = self.run_main()
        self.assertEqual(result, 0, output)
        self.assertIn('installed and confirmed for owner user 0', output)
        self.assertIn('ENCMA process is running', output)
        commands = [command[2:] for command in state['commands']]
        self.assertEqual(sum(command[:1] == ['install'] for command in commands), 1)
        self.assertTrue(any(command[:3] == ['shell', 'am', 'start'] for command in commands))
        self.assertFalse(any('vpn' in ' '.join(command).lower() for command in commands))
        self.assertFalse(any('-r' in command or 'uninstall' in command for command in commands))

    def test_already_installed_is_preserved_and_opened(self):
        state = self.load()
        state['packages'].append(app.PACKAGE)
        self.save(state)
        result, output, state = self.run_main()
        self.assertEqual(result, 0, output)
        self.assertIn('already installed', output)
        self.assert_no_install(state)
        self.assertTrue(any(command[2:5] == ['shell', 'am', 'start'] for command in state['commands']))

    def test_retained_data_is_preserved_and_not_launched(self):
        state = self.load()
        state['retained'] = [app.PACKAGE]
        self.save(state)
        result, output, state = self.run_main()
        self.assertEqual(result, 1, output)
        self.assertIn('Retained ENCMA', output)
        self.assert_no_install(state)
        self.assertFalse(any(command[2:5] == ['shell', 'am', 'start'] for command in state['commands']))

    def test_wrong_device_stops_before_install(self):
        state = self.load()
        state['model'] = 'SM-P999'
        self.save(state)
        result, _, state = self.run_main()
        self.assertEqual(result, 1)
        self.assert_no_install(state)

    def test_missing_and_wrong_serial_stop_before_adb(self):
        for serial in ('', 'wrongserial'):
            with self.subTest(serial=serial), mock.patch.dict(os.environ, {'P585_SERIAL': serial}):
                result, _, state = self.run_main()
                self.assertEqual(result, 1)
                self.assertEqual(state['commands'], [])

    def test_incomplete_package_list_stops_before_install(self):
        state = self.load()
        state['packages'] = []
        self.save(state)
        result, output, state = self.run_main()
        self.assertEqual(result, 1)
        self.assertIn('package list was incomplete', output)
        self.assert_no_install(state)

    def test_existing_package_appearing_during_download_is_preserved(self):
        real_download = app.download_archive
        def racing_download(path):
            real_download(path)
            state = self.load()
            state['packages'].append(app.PACKAGE)
            self.save(state)
        with mock.patch.object(app, 'download_archive', side_effect=racing_download):
            result, output, state = self.run_main()
        self.assertEqual(result, 1, output)
        self.assertIn('appeared during setup', output)
        self.assert_no_install(state)

    def test_curl_errors_do_not_disclose_signed_urls(self):
        failure = app.subprocess.CompletedProcess(['curl'], 22, b'', b'failed https://cdn.example/a?secret=token')
        with mock.patch.object(app.subprocess, 'run', return_value=failure):
            with self.assertRaises(app.Stop) as error:
                app.run(['curl', 'https://example.test'])
        self.assertNotIn('secret', str(error.exception))
        self.assertNotIn('token', str(error.exception))

    def test_untrusted_archive_never_installs(self):
        with mock.patch.object(app, 'ARCHIVE_SHA256', '0' * 64):
            result, output, state = self.run_main()
        self.assertEqual(result, 1)
        self.assertIn('SHA-256', output)
        self.assert_no_install(state)

    def test_bad_apk_digest_never_installs(self):
        with mock.patch.object(app, 'APK_SHA256', '0' * 64):
            result, output, state = self.run_main()
        self.assertEqual(result, 1)
        self.assertIn('APK SHA-256', output)
        self.assert_no_install(state)

    def test_bad_apk_size_never_installs(self):
        with mock.patch.object(app, 'APK_SIZE', len(self.apk) + 1):
            result, output, state = self.run_main()
        self.assertEqual(result, 1)
        self.assertIn('APK size', output)
        self.assert_no_install(state)

    def test_unexpected_and_multiple_zip_entries_are_rejected(self):
        for multiple in (False, True):
            with self.subTest(multiple=multiple):
                self.write_archive(app.APK_FILENAME if multiple else '../wrong.apk', self.apk)
                if multiple:
                    with zipfile.ZipFile(self.archive, 'a') as data:
                        data.writestr('unexpected.txt', b'extra')
                with mock.patch.object(app, 'ARCHIVE_SHA256', app.file_hash(self.archive)):
                    result, output, state = self.run_main()
                self.assertEqual(result, 1)
                self.assertIn('unexpected files', output)
                self.assert_no_install(state)

    def test_false_adb_success_is_not_confirmed_or_launched(self):
        state = self.load()
        state['false_success'] = True
        self.save(state)
        result, output, state = self.run_main()
        self.assertEqual(result, 1)
        self.assertIn('installation not confirmed', output)
        self.assertFalse(any(command[2:5] == ['shell', 'am', 'start'] for command in state['commands']))

    def test_bad_remote_marker_stops_before_install(self):
        state = self.load()
        state['marker_fail'] = True
        self.save(state)
        result, _, state = self.run_main()
        self.assertEqual(result, 1)
        self.assert_no_install(state)

    def test_missing_pins_fail_closed(self):
        with mock.patch.object(app, 'ARTIFACT_ID', None):
            result, output, state = self.run_main()
        self.assertEqual(result, 1)
        self.assertEqual(state['commands'], [])
        self.assertIn('No published artifact', output)

    def test_launch_failure_does_not_claim_install_failed(self):
        state = self.load()
        state['launch_failure'] = True
        self.save(state)
        result, output, state = self.run_main()
        self.assertEqual(result, 1)
        self.assertIn('installed and confirmed', output)
        self.assertIn('installed/present, but launch is not confirmed', output)
        self.assertIn(app.PACKAGE, state['packages'])

    def test_crashed_process_is_not_reported_running(self):
        state = self.load()
        state['no_process'] = True
        self.save(state)
        result, output, state = self.run_main()
        self.assertEqual(result, 1)
        self.assertIn('launch is not confirmed', output)
        self.assertNotIn('process is running', output)
        self.assertIn(app.PACKAGE, state['packages'])


if __name__ == '__main__':
    unittest.main()
