"""Hermetic tests for the Manama Android mock-location helper (no device needed)."""
import base64
import copy
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOL_DIR = ROOT / 'scripts' / 'p585-manama'
PROPS = ('gsm.sim.operator.numeric', 'gsm.sim.operator.iso-country', 'gsm.sim.operator.alpha',
         'gsm.operator.numeric', 'gsm.operator.iso-country', 'gsm.operator.alpha')


def sha(data):
    return hashlib.sha256(data).hexdigest()


class SourceIntegrityTests(unittest.TestCase):
    def test_pinned_helper_blobs_and_checksums_match_sources(self):
        main = (ROOT / 'scripts' / 'p585-manama.sh').read_text()
        for key, filename in (('REPACK', 'repack.py'), ('ACTIONS', 'actions.sh'),
                              ('SERVICE', 'service.sh'), ('NOTICE', 'NOTICE-Appium.txt')):
            data = (TOOL_DIR / filename).read_bytes()
            blob = hashlib.sha1(b'blob ' + str(len(data)).encode('ascii') + b'\0' + data).hexdigest()
            for suffix, expected in (('BLOB', blob), ('SHA', sha(data))):
                match = re.search(rf"readonly {key}_{suffix}='([^']+)'", main)
                self.assertIsNotNone(match)
                self.assertEqual(match.group(1), expected, f'{filename} {suffix} is stale')


class RepackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        spec = importlib.util.spec_from_file_location('manama_repack_test', TOOL_DIR / 'repack.py')
        self.repack = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.repack)
        self.original = self.root / 'upstream.apk'
        self.output = self.root / 'helper.apk'
        self.notice = self.root / 'NOTICE'
        self.license = self.root / 'LICENSE'
        self.notice.write_bytes(b'Appium attribution fixture')
        self.license.write_bytes(b'Appium Apache license fixture')
        self.repack.NOTICE_SHA256 = sha(self.notice.read_bytes())
        self.repack.LICENSE_SHA256 = sha(self.license.read_bytes())
        self.long_name = 'assets/' + 'x' * 78 + '.txt'
        with zipfile.ZipFile(self.original, 'w') as z:
            z.writestr('AndroidManifest.xml', b'original manifest')
            z.writestr('classes.dex', b'dex\n035\0real-code-placeholder')
            z.writestr('resources.arsc', b'resources')
            z.writestr(self.long_name, b'long-name-payload')
            z.writestr('META-INF/LICENSE', b'license to keep')
            z.writestr('META-INF/CERT.SF', b'old signature')
            z.writestr('META-INF/CERT.RSA', b'old cert')
            z.writestr('META-INF/MANIFEST.MF', b'old manifest')
        self.repack.UPSTREAM_SHA256 = sha(self.original.read_bytes())

    def tearDown(self):
        self.temp.cleanup()

    @unittest.skipUnless(shutil.which('openssl'), 'openssl is required')
    def test_repack_preserves_dex_and_resources_and_has_a_valid_signature(self):
        self.repack.repack(self.original, self.output, self.notice, self.license)
        with zipfile.ZipFile(self.original) as src, zipfile.ZipFile(self.output) as out:
            self.assertEqual(out.read('classes.dex'), src.read('classes.dex'))
            self.assertEqual(out.read('resources.arsc'), src.read('resources.arsc'))
            self.assertEqual(out.read(self.long_name), src.read(self.long_name))
            self.assertEqual(out.read('META-INF/LICENSE'), src.read('META-INF/LICENSE'))
            self.assertEqual(out.read('assets/manama-notices/NOTICE-Appium.txt'), self.notice.read_bytes())
            self.assertEqual(out.read('assets/manama-notices/LICENSE-Appium.txt'), self.license.read_bytes())
            self.assertEqual(sha(out.read('AndroidManifest.xml')), self.repack.MANIFEST_SHA256)
            self.assertNotEqual(out.read('META-INF/CERT.SF'), b'old signature')
            mf, sf, rsa = (out.read('META-INF/' + name) for name in ('MANIFEST.MF', 'CERT.SF', 'CERT.RSA'))
            self.assertIn(base64.b64encode(hashlib.sha256(mf).digest()), sf)
            self.assertIn(base64.b64encode(hashlib.sha256(src.read(self.long_name)).digest()), mf)
        (self.root / 'sf').write_bytes(sf)
        (self.root / 'signature').write_bytes(rsa)
        subprocess.run(['openssl', 'smime', '-verify', '-inform', 'DER', '-noverify',
                        '-in', str(self.root / 'signature'), '-content', str(self.root / 'sf'),
                        '-out', os.devnull], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertFalse(list(self.root.glob('.manama-sign-*')), 'ephemeral signing key must be deleted')

    def test_bad_upstream_or_existing_output_is_not_overwritten(self):
        self.repack.UPSTREAM_SHA256 = '0' * 64
        with self.assertRaises(ValueError):
            self.repack.repack(self.original, self.output, self.notice, self.license)
        self.assertFalse(self.output.exists())
        self.repack.UPSTREAM_SHA256 = sha(self.original.read_bytes())
        self.output.write_bytes(b'existing user file')
        with self.assertRaises(ValueError):
            self.repack.repack(self.original, self.output, self.notice, self.license)
        self.assertEqual(self.output.read_bytes(), b'existing user file')

    def test_bad_notice_or_license_fails_closed(self):
        self.notice.write_bytes(b'tampered notice')
        with self.assertRaises(ValueError):
            self.repack.repack(self.original, self.output, self.notice, self.license)
        self.assertFalse(self.output.exists())
        self.notice.write_bytes(b'Appium attribution fixture')
        self.license.write_bytes(b'tampered license')
        with self.assertRaises(ValueError):
            self.repack.repack(self.original, self.output, self.notice, self.license)
        self.assertFalse(self.output.exists())

    def test_bad_compiled_manifest_fails_closed(self):
        self.repack.MANIFEST_SHA256 = '0' * 64
        with self.assertRaises(ValueError):
            self.repack.repack(self.original, self.output, self.notice, self.license)
        self.assertFalse(self.output.exists())


FAKE_ANDROID = r'''#!/usr/bin/env python3
import fcntl, json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
path = os.environ['SIM_STATE']
with open(path, 'r+', encoding='utf8') as f:
    fcntl.flock(f, fcntl.LOCK_EX)
    s=json.load(f)
    s.setdefault('calls', []).append([name] + args)
    code=0; out=''
    if name == 'getprop':
        if args:
            out=s['properties'].get(args[0], '') + '\n'
        else:
            out=''.join('['+k+']: ['+v+']\n' for k,v in sorted(s['properties'].items()))
    elif name == 'settings':
        if args[:2] == ['--user','0']: args=args[2:]
        op, domain, key = args[:3]
        k=domain+':'+key
        if op == 'get': out=s['settings'].get(k,'null')+'\n'
        elif op == 'put':
            val=args[3]
            if key == 'location_providers_allowed' and val[:1] in '+-':
                old=s['settings'].get(k,'')
                items=[] if old in ('null','') else old.split(',')
                if val[0]=='+' and val[1:] not in items: items.append(val[1:])
                if val[0]=='-': items=[x for x in items if x!=val[1:]]
                s['settings'][k]=','.join(items)
            else: s['settings'][k]=val
        elif op == 'delete': s['settings'].pop(k,None)
        else: code=1
    elif name == 'pm':
        if args[0]=='path':
            if s.get('installed',True): out='package:'+s['apk_path']+'\n'
            else: code=1
        elif args[0]=='grant':
            if s.get('fail_grant'): code=1; out='grant failed\n'
        else: code=1
    elif name == 'appops':
        if args[0]=='set': s['mock_appop']=True
        elif args[0]=='get': out=('MOCK_LOCATION: allow\n' if s.get('mock_appop') else 'MOCK_LOCATION: deny\n')
        elif args[0]=='write-settings': out='Current settings written.\n'
        else: code=1
    elif name == 'am':
        if args[0]=='start-foreground-service':
            if s.get('fail_start'): out='Error: Not found; no service started.\n'
            else:
                out='Starting service: Intent { ... }\n'
                if not s.get('fail_mock'): s['mock_active']=True
        elif args[0]=='stopservice':
            if not s.get('sticky_mock'): s['mock_active']=False
            out='Service stopped\n'
        elif args[0]=='broadcast': out='Broadcast completed\n'
        else: code=1
    elif name == 'dumpsys':
        if s.get('dump_error'): code=1
        else:
            out='Current Location Manager state:\n'
            if s.get('mock_active'):
                coord = s.get('coordinate_format', '26.223536,50.586994')
                out += '  Mock Providers:\n      gps\n        Location[gps '+coord+' hAcc=1 et=1s mock]\n'
                out += '      network\n        Location[network '+coord+' hAcc=1 et=1s mock]\n'
    elif name == 'setprop':
        if s.get('fail_timezone'): code=1
        else: s['properties'][args[0]]=args[1]
    elif name == 'magisk':
        if args[:2]==['resetprop','-n']:
            s['properties'][args[2]]=args[3]
        elif args[:2]==['resetprop','--delete']:
            s['properties'].pop(args[2],None)
        elif args==['--list']: out='su\nresetprop\n'
        else: code=1
    elif name == 'sleep':
        if args == ['3'] and os.environ.get('SIM_STOP_AFTER_TICK'):
            s['short_sleeps'] = s.get('short_sleeps', 0) + 1
            if s['short_sleeps'] >= 2:
                pathlib.Path(os.environ['SIM_MODULE'], 'disable').touch()
    else: code=1
    f.seek(0);json.dump(s,f);f.truncate(); f.flush()
    print(out, end='')
    sys.exit(code)
'''


class DeviceActionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.mod = self.root / 'modules' / 'p585_manama'
        self.token = 'p585-manama-Ab12Cd34'
        self.backup = self.root / 'backups' / self.token
        self.mod.mkdir(parents=True)
        (self.mod / 'touched').mkdir()
        (self.backup / 'props').mkdir(parents=True)
        (self.mod / 'owner').write_text(self.token + '\n')
        (self.mod / 'backup-path').write_text(str(self.backup) + '\n')
        self.apps = self.root / 'apps' / 'io.appium.settings-abc'
        self.apps.mkdir(parents=True)
        self.apk = self.apps / 'base.apk'
        self.apk.write_bytes(b'locally signed helper')
        self.initial = {
            'properties': {
                'gsm.sim.operator.numeric': '26201', 'gsm.sim.operator.iso-country': 'de',
                'gsm.sim.operator.alpha': 'Carrier DE', 'gsm.operator.numeric': '',
                # gsm.operator.iso-country is intentionally absent; empty != absent.
                'gsm.operator.alpha': 'Carrier network', 'persist.sys.timezone': 'Europe/Berlin',
                'sys.boot_completed': '1', 'untouched.property': 'leave this alone',
            },
            'settings': {'secure:location_providers_allowed': 'network', 'global:auto_time_zone': '1'},
            'mock_active': False,
            'mock_appop': False,
            'apk_path': str(self.apk),
            'installed': True,
            'calls': [],
        }
        (self.backup / 'token').write_text(self.token + '\n')
        (self.backup / 'location_providers_allowed').write_text('network\n')
        (self.backup / 'auto_time_zone').write_text('1\n')
        (self.backup / 'timezone').write_text('Europe/Berlin\n')
        (self.backup / 'helper_apk.sha256').write_text(sha(self.apk.read_bytes()) + '\n')
        for key in PROPS:
            present = key in self.initial['properties']
            (self.backup / 'props' / (key + '.present')).write_text(('1' if present else '0') + '\n')
            (self.backup / 'props' / (key + '.value')).write_text(self.initial['properties'].get(key, '') + '\n')
        self.data = self.root / 'device.json'
        self.write_state(copy.deepcopy(self.initial))
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        fake = self.bin / 'fake-android'
        fake.write_text(FAKE_ANDROID)
        fake.chmod(0o755)
        for n in ('getprop', 'settings', 'pm', 'appops', 'am', 'dumpsys', 'setprop', 'magisk', 'sleep'):
            (self.bin / n).symlink_to(fake.name)
        self.env = os.environ.copy()
        self.env.update({'PATH': str(self.bin) + os.pathsep + os.environ['PATH'], 'SIM_STATE': str(self.data),
                         'SIM_MODULE': str(self.mod), 'SIM_STOP_AFTER_TICK': '1'})
        if os.environ.get('TEST_BUSYBOX_ASH') == '1':
            self.env['ASH_STANDALONE'] = '1'
        replacements = {
            '/data/adb/modules/p585_manama': str(self.mod),
            '/data/adb/p585-manama-backups': str(self.root / 'backups'),
            '/data/app/*/base.apk': str(self.root / 'apps') + '/*/base.apk',
        }
        for n in ('actions.sh', 'service.sh'):
            text = (TOOL_DIR / n).read_text()
            for old, new in replacements.items(): text = text.replace(old, new)
            if os.environ.get('TEST_BUSYBOX_ASH') == '1':
                # Magisk's standalone ash prefers its built-in sleep over PATH;
                # explicitly use our no-delay fake only in these test copies.
                for seconds in (1, 2, 3, 5):
                    text = text.replace(f'sleep {seconds}\n', f'"{self.bin / "sleep"}" {seconds}\n')
            p = self.mod / n
            p.write_text(text)
            p.chmod(0o700)

    def tearDown(self):
        self.temp.cleanup()

    def read_state(self):
        return json.loads(self.data.read_text())

    def write_state(self, data):
        self.data.write_text(json.dumps(data))

    def android_shell(self):
        return ['busybox', 'ash'] if os.environ.get('TEST_BUSYBOX_ASH') == '1' else ['sh']

    def action(self, which):
        return subprocess.run(self.android_shell() + [str(self.mod / 'actions.sh'), which, str(self.mod), str(self.backup), self.token],
                              env=self.env, text=True, capture_output=True, timeout=30)

    def service(self):
        return subprocess.run(self.android_shell() + [str(self.mod / 'service.sh')], env=self.env, text=True,
                              capture_output=True, timeout=30)

    def test_apply_persistent_watcher_and_restore_exact_originals(self):
        result = self.action('apply')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('MANAMA_APPLY_OK', result.stdout)
        applied = self.read_state()
        self.assertTrue(applied['mock_active'])
        self.assertEqual(applied['settings']['global:auto_time_zone'], '0')
        self.assertEqual(applied['properties']['persist.sys.timezone'], 'Asia/Bahrain')
        self.assertEqual(set(applied['settings']['secure:location_providers_allowed'].split(',')), {'gps', 'network'})
        watcher = self.service()
        self.assertEqual(watcher.returncode, 0, watcher.stdout + watcher.stderr)
        watched = self.read_state()
        self.assertEqual(watched['properties']['gsm.sim.operator.numeric'], '42601')
        self.assertEqual(watched['properties']['gsm.operator.iso-country'], 'bh')
        self.assertTrue((self.mod / 'touched' / 'carrier').exists())
        restored = self.action('restore')
        self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)
        self.assertIn('MANAMA_RESTORE_OK', restored.stdout)
        end = self.read_state()
        self.assertEqual(end['properties'], self.initial['properties'])
        self.assertEqual(end['settings'], self.initial['settings'])
        self.assertFalse(end['mock_active'])
        self.assertTrue((self.mod / 'disable').exists())
        self.assertFalse((self.mod / 'action.lock').exists())

    def test_localized_coordinate_formats_verify_mock_providers(self):
        for coordinate in ('26,223536,50,586994', '۲۶٫۲۲۳۵۳۶,۵۰٫۵۸۶۹۹۴', '٢٦٫٢٢٣٥٣٦,٥٠٫٥٨٦٩٩٤'):
            with self.subTest(coordinate=coordinate):
                # Reset the isolated fixture for each locale.
                self.tearDown()
                self.setUp()
                state = self.read_state()
                state['coordinate_format'] = coordinate
                self.write_state(state)
                result = self.action('apply')
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                watcher = self.service()
                self.assertEqual(watcher.returncode, 0, watcher.stdout + watcher.stderr)
                self.assertTrue((self.mod / 'touched' / 'carrier').exists())

    def test_disable_stops_watcher_without_touching_settings(self):
        result = self.action('disable')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('MANAMA_DISABLE_OK', result.stdout)
        self.assertTrue((self.mod / 'disable').exists())
        self.assertEqual(self.read_state()['properties'], self.initial['properties'])
        self.assertEqual(self.read_state()['settings'], self.initial['settings'])
        self.assertEqual(list((self.mod / 'touched').iterdir()), [])

    def test_snapshot_change_stops_before_writes_and_restore_preserves_new_value(self):
        state = self.read_state()
        state['properties']['gsm.operator.alpha'] = 'new carrier registered after backup'
        self.write_state(state)
        result = self.action('apply')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('changed since backup', result.stderr)
        self.assertEqual(list((self.mod / 'touched').iterdir()), [])
        restored = self.action('restore')
        self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)
        end = self.read_state()
        self.assertEqual(end['properties']['gsm.operator.alpha'], 'new carrier registered after backup')
        self.assertEqual(end['settings'], self.initial['settings'])
        self.assertFalse(any(call[0] == 'setprop' or (call[0] == 'magisk' and call[1:2] == ['resetprop'])
                             for call in end['calls']))

    def test_unverified_mock_stops_and_restore_does_not_touch_unmodified_settings(self):
        state = self.read_state()
        state['fail_mock'] = True
        self.write_state(state)
        result = self.action('apply')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('could not verify both Manama mock providers', result.stderr)
        self.assertTrue((self.mod / 'touched' / 'service').exists())
        self.assertFalse((self.mod / 'touched' / 'providers').exists())
        restored = self.action('restore')
        self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)
        end = self.read_state()
        self.assertEqual(end['properties'], self.initial['properties'])
        self.assertEqual(end['settings'], self.initial['settings'])

    def test_restore_preserves_new_radio_values_changed_after_spoof(self):
        applied = self.action('apply')
        self.assertEqual(applied.returncode, 0, applied.stderr)
        watcher = self.service()
        self.assertEqual(watcher.returncode, 0, watcher.stderr)
        state = self.read_state()
        state['properties']['gsm.operator.numeric'] = '310260'  # New radio value, not our preset.
        self.write_state(state)
        restored = self.action('restore')
        self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)
        self.assertIn('changed independently', restored.stdout)
        end = self.read_state()
        self.assertEqual(end['properties']['gsm.operator.numeric'], '310260')
        self.assertEqual(end['properties']['gsm.sim.operator.numeric'], '26201')

    def test_watcher_does_not_change_carrier_if_helper_has_been_replaced(self):
        self.apk.write_bytes(b'a different APK')
        result = self.service()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('missing or changed', result.stdout)
        end = self.read_state()
        self.assertEqual(end['properties'], self.initial['properties'])
        self.assertFalse((self.mod / 'touched' / 'carrier').exists())
        self.assertFalse(any(call[:2] == ['am', 'start-foreground-service'] for call in end['calls']))

    def test_watcher_restores_its_spoof_only_in_the_same_boot(self):
        spoof = {'gsm.sim.operator.numeric': '42601', 'gsm.sim.operator.iso-country': 'bh',
                 'gsm.sim.operator.alpha': 'Batelco', 'gsm.operator.numeric': '42601',
                 'gsm.operator.iso-country': 'bh', 'gsm.operator.alpha': 'Batelco'}
        state = self.read_state()
        state['properties'].update(spoof)
        self.write_state(state)
        (self.mod / 'touched' / 'carrier').touch()
        (self.mod / 'spoof-boot').write_text(pathlib.Path('/proc/sys/kernel/random/boot_id').read_text())
        self.apk.write_bytes(b'different APK')
        result = self.service()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.read_state()['properties'], self.initial['properties'])
        self.assertFalse((self.mod / 'spoof-boot').exists())

    def test_watcher_does_not_restore_stale_properties_after_reboot(self):
        state = self.read_state()
        state['properties']['gsm.sim.operator.numeric'] = '42601'  # may be a real new SIM
        self.write_state(state)
        (self.mod / 'touched' / 'carrier').touch()
        (self.mod / 'spoof-boot').write_text('old-boot-id\n')
        self.apk.write_bytes(b'different APK')
        result = self.service()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.read_state()['properties']['gsm.sim.operator.numeric'], '42601')

    def test_restore_failure_keeps_module_disabled_and_backup(self):
        result = self.action('apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.read_state()
        state['sticky_mock'] = True
        self.write_state(state)
        restored = self.action('restore')
        self.assertNotEqual(restored.returncode, 0)
        self.assertIn('mock provider remains', restored.stderr)
        self.assertTrue((self.mod / 'disable').exists())
        self.assertTrue(self.backup.exists())

    def test_uncertain_action_lock_fails_closed(self):
        lock = self.mod / 'action.lock'
        lock.mkdir()
        (lock / 'pid').write_text('unknown\n')
        result = self.action('apply')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('no valid PID', result.stderr)
        self.assertEqual(list((self.mod / 'touched').iterdir()), [])


if __name__ == '__main__':
    unittest.main()
