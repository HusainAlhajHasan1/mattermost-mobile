"""End-to-end fake-ADB tests for the P585 app installer entry point."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('p585_apps_host', ROOT / 'scripts/p585-apps-install.py')
apps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(apps)

FAKE_ADB = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
p = pathlib.Path(os.environ['FAKE_STATE'])
s = json.loads(p.read_text())
args = sys.argv[1:]
if args[:2] != ['-s', '5203c66b523ca357']:
    sys.exit(9)
a = args[2:]
def save(): p.write_text(json.dumps(s))
def xml(pkg, labels):
    import xml.etree.ElementTree as E
    r=E.Element('hierarchy'); top=E.SubElement(r,'node',package=pkg,clickable='false',enabled='true',bounds='[0,0][1000,1000]')
    for i,v in enumerate(labels):
        E.SubElement(top,'node',package=pkg,text=v,clickable='true',enabled='true',bounds=f'[{i*100},10][{i*100+80},90]')
    return E.tostring(r,encoding='unicode')
if a == ['get-state']: print('device'); sys.exit()
if a[:2] == ['shell','getprop']:
    print({'ro.product.model':'SM-P585','ro.product.device':'gtanotexllte',
           'ro.build.version.incremental':'P585JXS3CTL1','ro.build.version.sdk':'27',
           'ro.product.cpu.abi':'armeabi-v7a','sys.boot_completed':'1'}[a[2]]);sys.exit()
if a == ['shell','am','get-current-user']:print('0');sys.exit()
if a[:3] == ['install','--user','0']:
    package=pathlib.Path(a[3]).stem
    if package in s['packages']:print('Failure [INSTALL_FAILED_ALREADY_EXISTS]');sys.exit(1)
    if not (s.get('false_success') and package=='org.fdroid.fdroid'):
        s['packages'].append(package);save()
    print('Success');sys.exit()
if a[:2] == ['exec-out','cat']:
    sys.stdout.buffer.write(b'aurora-verified-fixture');sys.exit()
if len(a) != 2 or a[0] != 'shell':sys.exit(8)
c=a[1]
if 'pm list packages' in c:
    for package in s['packages']:print('package:'+package)
    if 'pm list packages -u --user' in c:
        for package in s.get('retained',[]):print('package:'+package)
    print('P585_PM_OK');sys.exit()
if 'pm path com.aurora.store' in c:
    print('package:/data/app/com.aurora.store-1/base.apk\nP585_PATH_OK');sys.exit()
if 'appops get ' in c:
    print('REQUEST_INSTALL_PACKAGES: '+s['mode']);print('P585_APPOP_OK');sys.exit()
if 'appops set ' in c:
    for mode in ['allow','default','ignore','deny']:
        if 'REQUEST_INSTALL_PACKAGES '+mode in c:
            s['mode']=mode;s['modes'].append(mode);save();print('P585_APPOP_SET');sys.exit()
if 'am start -W' in c:
    if 'SHOW_APP_INFO' in c:s['ui']='details';print('P585_DETAILS_OK')
    else:s['ui']='welcome';print('P585_START_OK')
    save();sys.exit()
if 'uiautomator dump' in c:
    phase=s['ui']; screens={
      'welcome':('com.aurora.store',['Welcome','Next','Skip']),
      'login':('com.aurora.store',['Google','Anonymous']),
      'main':('com.aurora.store',['Apps','Games','Updates']),
      'details':('com.aurora.store',['Instagram','Manual download','Install']),
      'installer':('com.android.packageinstaller',['Instagram','INSTALL']),
    }
    pkg,labels=screens[phase];print(xml(pkg,labels)+'P585_UI_OK');sys.exit()
if 'input tap' in c:
    phase=s['ui'];s['taps'].append(phase)
    s['ui']={'welcome':'login','login':'main','details':'installer','installer':'installer'}[phase]
    if phase=='installer':s['packages'].append('com.instagram.android')
    save();print('P585_TAP_OK');sys.exit()
sys.exit(7)
'''


class FullHostFlow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        adb = bin_dir / 'adb'
        adb.write_text(FAKE_ADB)
        adb.chmod(0o755)
        state = self.root / 'state.json'
        state.write_text(json.dumps({'packages': [], 'retained': [], 'ui': 'welcome',
                                     'taps': [], 'mode': 'default', 'modes': []}))
        self.state = state
        env = {'PATH': str(bin_dir) + os.pathsep + os.environ.get('PATH', ''),
               'FAKE_STATE': str(state), 'HOME': str(self.root), 'P585_SERIAL': '5203c66b523ca357'}
        self.patches = [mock.patch.dict(os.environ, env), mock.patch.object(apps.time, 'sleep'),
                        mock.patch.object(apps, 'get_key', return_value=self.root / 'key'),
                        mock.patch.object(apps, 'verify_apk')]
        for patch in self.patches:
            patch.start()
        self.download = mock.patch.object(apps, 'download', side_effect=self.fake_download)
        self.download.start()

    def tearDown(self):
        self.download.stop()
        for patch in reversed(self.patches):
            patch.stop()
        self.temp.cleanup()

    @staticmethod
    def fake_download(_url, path, api=False):
        path.write_bytes(b'aurora-verified-fixture' if 'com.aurora.store' in path.name else b'fdroid-fixture')

    def run_main(self):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            result = apps.main()
        return result, stream.getvalue(), json.loads(self.state.read_text())

    def test_fresh_install_uses_adb_and_verifies_three_packages(self):
        result, output, state = self.run_main()
        self.assertEqual(result, 0, output)
        self.assertEqual(set(state['packages']), {apps.FDROID, apps.AURORA, apps.INSTAGRAM})
        self.assertEqual(state['taps'], ['welcome', 'login', 'details', 'installer'])
        self.assertEqual(state['modes'], ['allow', 'default'])
        self.assertIn('Instagram: installed and verified via Aurora', output)

    def test_false_adb_success_is_not_reported_as_installed(self):
        state = json.loads(self.state.read_text())
        state['false_success'] = True
        self.state.write_text(json.dumps(state))
        result, output, state = self.run_main()
        self.assertEqual(result, 1, output)
        self.assertNotIn(apps.FDROID, state['packages'])
        self.assertIn('F-Droid: not installed', output)
        self.assertIn('Instagram: installed and verified via Aurora', output)

    def test_retained_data_is_not_reinstalled(self):
        state = json.loads(self.state.read_text())
        state['retained'] = [apps.FDROID, apps.AURORA, apps.INSTAGRAM]
        self.state.write_text(json.dumps(state))
        result, output, state = self.run_main()
        self.assertEqual(result, 1)
        self.assertEqual(state['packages'], [])
        self.assertEqual(state['taps'], [])
        self.assertIn('retained package data (left untouched)', output)

    def test_existing_aurora_mismatch_blocks_automation(self):
        state = json.loads(self.state.read_text())
        state['packages'] = [apps.FDROID, apps.AURORA]
        self.state.write_text(json.dumps(state))
        with mock.patch.object(apps.Device, 'installed_apk_hash', return_value='bad-hash'):
            result, output, state = self.run_main()
        self.assertEqual(result, 1)
        self.assertEqual(state['taps'], [])
        self.assertEqual(state['modes'], [])
        self.assertIn('it will not be replaced or automated', output)


if __name__ == '__main__':
    unittest.main()
