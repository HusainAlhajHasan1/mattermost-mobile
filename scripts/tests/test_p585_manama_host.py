"""End-to-end host-script simulations with a synthetic APK and fake Android ADB."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / 'scripts' / 'p585-manama.sh'
HELPERS = ROOT / 'scripts' / 'p585-manama'
SERIAL = 'aaaaaaaaaaaaaaaa'  # Synthetic only.
PROPS = ('gsm.sim.operator.numeric', 'gsm.sim.operator.iso-country', 'gsm.sim.operator.alpha',
         'gsm.operator.numeric', 'gsm.operator.iso-country', 'gsm.operator.alpha')


def digest(data):
    return hashlib.sha256(data).hexdigest()


ADB = r'''#!/usr/bin/env python3
import hashlib, json, os, pathlib, re, shutil, sys
base=pathlib.Path(os.environ['FAKE_ADB_DIR'])
fs=base/'fs'
record=base/'state.json'
scenario=os.environ['SCENARIO']
a=sys.argv[1:]
with (base/'calls.jsonl').open('a') as f: f.write(json.dumps(a[2:])+'\n')
if a[:2] != ['-s', os.environ['P585_SERIAL']]: sys.exit(2)
a=a[2:]
state=json.loads(record.read_text())
def save(): record.write_text(json.dumps(state))
def d(path): return fs/path.lstrip('/')
def emit(s=''): print(s)
def root_ok(s=''): print((s+'\n' if s else '')+'MANAMA_REMOTE_OK')
def root_fail(s):
    # Simulates an adbd that may return host status 0 for failed remote shell.
    emit(s); sys.exit(0)
if a[0]=='get-state': emit('device');sys.exit()
if a[0]=='push':
    src, dest=pathlib.Path(a[1]),d(a[2]); dest.parent.mkdir(parents=True, exist_ok=True);shutil.copyfile(src,dest)
    if scenario=='bad_stage' and a[2].endswith('gsm.sim.operator.numeric.value'): dest.write_text('corrupt\n')
    emit('pushed');sys.exit()
if a[0]=='install':
    target=d('/data/app/io.appium.settings-abc/base.apk');target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(a[-1],target)
    if scenario=='different_installed_apk': target.write_bytes(b'other app')
    state['installed']=True;save()
    if scenario=='install_fail': emit('Failure [INSTALL_FAILED]')
    else: emit('Performing Streamed Install\nSuccess')
    sys.exit()
if a[0]!='shell': sys.exit(3)
cmd=' '.join(a[1:])
if cmd=='su -c sh':
    body=sys.stdin.read()
    module='/data/adb/modules/p585_manama'
    mod=d(module)
    if 'command -v magisk; magisk --list' in body:
        root_ok('/sbin/magisk\nsu\nresetprop');sys.exit()
    if 'command -v sha256sum' in body:
        root_ok();sys.exit()
    if 'MANAMA_MODULE_EXISTS' in body:
        root_ok('MANAMA_MODULE_EXISTS' if scenario=='module_exists' or mod.exists() else 'MANAMA_MODULE_CLEAR');sys.exit()
    if 'sha256sum ' in body:
        matches=re.findall(r"sha256sum '([^']+)'",body)
        if not matches: root_fail('missing sha path')
        path=matches[-1];p=d(path)
        if not p.is_file(): root_fail('missing file')
        root_ok(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+path);sys.exit()
    if "pm path 'io.appium.settings'" in body:
        root_ok('package:/data/app/io.appium.settings-abc/base.apk' if state.get('installed') else 'MANAMA_APP_ABSENT');sys.exit()
    if "pm uninstall 'io.appium.settings'" in body:
        p=d('/data/app/io.appium.settings-abc/base.apk');p.unlink(missing_ok=True)
        state['installed']=False;save();root_ok('Success');sys.exit()
    if 'cp -R ' in body and 'mkdir ' in body:
        stage=re.search(r"'(/data/local/tmp/p585-manama-[A-Za-z0-9]{8})/state/\.'",body).group(1)
        backup=re.search(r"'(/data/adb/p585-manama-backups/p585-manama-[A-Za-z0-9]{8})'",body).group(1)
        token=backup.rsplit('/',1)[-1]
        b=d(backup);b.parent.mkdir(parents=True,exist_ok=True);shutil.copytree(d(stage+'/state'),b)
        mod.mkdir(parents=True);(mod/'owner').write_text(token+'\n');(mod/'backup-path').write_text(backup+'\n');(mod/'touched').mkdir()
        shutil.copyfile(d(stage+'/actions.sh'),mod/'actions.sh');shutil.copyfile(d(stage+'/service.sh'),mod/'service.sh')
        (mod/'module.prop').write_text('id=p585_manama\n')
        if scenario=='bad_device_backup': (b/'props/gsm.operator.numeric.value').write_text('corrupted')
        state['module']=True;save();root_ok();sys.exit()
    if "actions.sh' apply" in body:
        state['apply_called']=True;state['mock']=True;save()
        (mod/'touched/service').touch()
        if scenario=='apply_fail': root_fail('MANAMA_ERROR: mock provider failed')
        (mod/'touched/providers').touch();(mod/'touched/timezone').touch();state['timezone']='Asia/Bahrain';save()
        root_ok('MANAMA_APPLY_OK');sys.exit()
    if "actions.sh' disable" in body:
        state['disabled']=True;save();(mod/'disable').touch();root_ok('MANAMA_DISABLE_OK');sys.exit()
    if "actions.sh' restore" in body:
        state['restore_called']=True;state['mock']=False;state['timezone']='Europe/Berlin';state['carrier']=False;save()
        (mod/'disable').touch()
        if scenario=='restore_fail':root_fail('MANAMA_ERROR: restore incomplete')
        root_ok('MANAMA_RESTORE_OK');sys.exit()
    if "service.sh' </dev/null" in body:
        state['watcher']=True;state['carrier']=scenario!='no_carrier';save();root_ok();sys.exit()
    if 'MANAMA_LOCATION_OBSERVED' in body:
        result=('MANAMA_LOCATION_OBSERVED' if state.get('mock') else 'MANAMA_LOCATION_NOT_VERIFIED')+'\n'
        result+=('MANAMA_CARRIER_OBSERVED' if state.get('carrier') else 'MANAMA_CARRIER_NOT_VERIFIED')+'\n'
        result+=('MANAMA_TIMEZONE_OBSERVED' if state.get('timezone')=='Asia/Bahrain' else 'MANAMA_TIMEZONE_NOT_VERIFIED')+'\n'
        result+=('MANAMA_WATCHER_OBSERVED' if state.get('watcher') else 'MANAMA_WATCHER_NOT_VERIFIED')
        root_ok(result);sys.exit()
    if "cat '/data/adb/modules/p585_manama/owner'" in body and 'rm -rf' not in body and "disable'" not in body:
        if not mod.exists(): root_fail('module missing')
        root_ok((mod/'owner').read_text().strip());sys.exit()
    if 'rm -rf ' in body and '/data/local/tmp/' in body:
        m=re.search(r"'(/data/local/tmp/p585-manama-[A-Za-z0-9]{8})'",body)
        if m: shutil.rmtree(d(m.group(1)),ignore_errors=True)
        root_ok();sys.exit()
    if 'rm -rf ' in body and module in body:
        if not mod.exists() or not (mod/'owner').exists(): root_fail('Module owner mismatch')
        shutil.rmtree(mod);state['module']=False;save();root_ok();sys.exit()
    if "disable'" in body and module in body:
        if mod.exists() and (mod/'owner').exists(): (mod/'disable').touch()
        root_ok();sys.exit()
    if 'MANAMA_MOCK_EXISTS' in body:
        root_ok('MANAMA_MOCK_EXISTS' if state.get('mock') else 'MANAMA_MOCK_CLEAR');sys.exit()
    root_fail('unknown root command: '+body)
if cmd.startswith('getprop'):
    props={'ro.product.model':'SM-P585','ro.product.device':'gtanotexllte','ro.build.version.incremental':'P585JXS3CTL1',
           'ro.build.version.sdk':'26' if scenario=='wrong_sdk' else '27','sys.boot_completed':'1','persist.sys.timezone':'Europe/Berlin',
           'gsm.sim.operator.numeric':'26201','gsm.sim.operator.iso-country':'de','gsm.sim.operator.alpha':'Carrier DE',
           'gsm.operator.numeric':'','gsm.operator.alpha':'Other'}
    if cmd=='getprop':emit(''.join('['+k+']: ['+v+']\n' for k,v in props.items()).rstrip('\n'))
    else:emit(props.get(cmd.split()[1],''))
    sys.exit()
if cmd=='am get-current-user':emit('0');sys.exit()
if cmd=='su -c id':emit('uid=0(root) gid=0(root) context=u:r:magisk:s0');sys.exit()
if cmd.startswith('settings --user 0 get secure'):emit('network');sys.exit()
if cmd.startswith('settings --user 0 get global'):emit('1');sys.exit()
if 'MANAMA_APP_EXISTS' in cmd:
    emit('MANAMA_APP_EXISTS' if scenario=='existing_app' else 'MANAMA_APP_CLEAR');sys.exit()
if 'MANAMA_QUERY_OK' in cmd:
    emit('some.other.mock.app\nMANAMA_QUERY_OK' if scenario=='other_mock' else 'No operations.\nMANAMA_QUERY_OK');sys.exit()
if 'MANAMA_MOCK_CLEAR' in cmd:
    emit('MANAMA_MOCK_EXISTS' if scenario=='mock_running' else 'MANAMA_MOCK_CLEAR');sys.exit()
if 'MANAMA_STAGE_OK' in cmd:
    m=re.search(r"'(/data/local/tmp/p585-manama-[A-Za-z0-9]{8})'",cmd);p=d(m.group(1));p.mkdir(parents=True);(p/'owner').write_text(p.name+'\n')
    emit('MANAMA_STAGE_OK');sys.exit()
if 'MANAMA_DIRS_OK' in cmd:
    m=re.search(r"'(/data/local/tmp/p585-manama-[A-Za-z0-9]{8})/state'",cmd);p=d(m.group(1));(p/'state/props').mkdir(parents=True)
    emit('MANAMA_DIRS_OK');sys.exit()
root_fail('unknown shell command: '+cmd)
'''

CURL = r'''#!/usr/bin/env python3
import json, os, pathlib, shutil, sys
args=sys.argv[1:];out=pathlib.Path(args[args.index('-o')+1]);url=next(a for a in args if a.startswith('https://'))
if 'registry.npmjs.org' in url: source=os.environ['FIXTURE_TGZ']
else: source=json.loads(os.environ['BLOB_MAP'])[url.rsplit('/',1)[-1]]
shutil.copyfile(source,out)
'''


class HostFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.fake=self.root/'fake';self.fake.mkdir()
        (self.fake/'fs').mkdir()
        (self.fake/'state.json').write_text(json.dumps({'installed':False,'module':False,'mock':False,'timezone':'Europe/Berlin'}))
        self.work=self.root/'work';self.work.mkdir()
        self.bin=self.root/'bin';self.bin.mkdir()
        for name,content in [('adb',ADB),('curl',CURL),('sleep','#!/bin/sh\nexit 0\n')]:
            (self.bin/name).write_text(content);(self.bin/name).chmod(0o755)
        self.apk=self.root/'upstream.apk'
        with zipfile.ZipFile(self.apk,'w') as z:
            z.writestr('AndroidManifest.xml',b'original');z.writestr('classes.dex',b'dex fixture');z.writestr('resources.arsc',b'res fixture')
        self.tgz=self.root/'appium.tgz'
        self.license_data=b'Appium license fixture'
        with tarfile.open(self.tgz,'w:gz') as t:
            for name,data in [('package/apks/settings_apk-debug.apk',self.apk.read_bytes()),('package/LICENSE',self.license_data)]:
                info=tarfile.TarInfo(name);info.size=len(data);t.addfile(info,io.BytesIO(data))
        self.repack=self.root/'repack.py'
        text=(HELPERS/'repack.py').read_text()
        text=re.sub(r"UPSTREAM_SHA256 = '[0-9a-f]{64}'",f"UPSTREAM_SHA256 = '{digest(self.apk.read_bytes())}'",text)
        text=re.sub(r"LICENSE_SHA256 = '[0-9a-f]{64}'",f"LICENSE_SHA256 = '{digest(self.license_data)}'",text)
        self.repack.write_text(text)
        helpers={'REPACK':self.repack,'ACTIONS':HELPERS/'actions.sh','SERVICE':HELPERS/'service.sh','NOTICE':HELPERS/'NOTICE-Appium.txt'}
        main=MAIN.read_text()
        main=re.sub(r"readonly SERIAL_SHA256='[0-9a-f]+'",f"readonly SERIAL_SHA256='{digest(SERIAL.encode())}'",main)
        main=re.sub(r"readonly APPIUM_TGZ_SHA='[0-9a-f]+'",f"readonly APPIUM_TGZ_SHA='{digest(self.tgz.read_bytes())}'",main)
        main=re.sub(r"readonly APPIUM_APK_SHA='[0-9a-f]+'",f"readonly APPIUM_APK_SHA='{digest(self.apk.read_bytes())}'",main)
        main=re.sub(r"readonly APPIUM_LICENSE_SHA='[0-9a-f]+'",f"readonly APPIUM_LICENSE_SHA='{digest(self.license_data)}'",main)
        blobs={}
        for name,path in helpers.items():
            blob=digest(path.read_bytes())[:40];sha=digest(path.read_bytes());blobs[blob]=str(path)
            main=re.sub(rf"readonly {name}_BLOB='[^']+'",f"readonly {name}_BLOB='{blob}'",main)
            main=re.sub(rf"readonly {name}_SHA='[^']+'",f"readonly {name}_SHA='{sha}'",main)
        self.script=self.root/'tool.sh';self.script.write_text(main)
        self.env=os.environ.copy()
        self.env.update(PATH=str(self.bin)+os.pathsep+self.env['PATH'],P585_SERIAL=SERIAL,P585_WORKDIR=str(self.work),
                        FAKE_ADB_DIR=str(self.fake),FIXTURE_TGZ=str(self.tgz),BLOB_MAP=json.dumps(blobs))

    def tearDown(self):self.temp.cleanup()
    def run_tool(self, scenario='success', response='APPLY', script=None, args=()):
        env=self.env.copy();env['SCENARIO']=scenario
        return subprocess.run(['bash',str(script or self.script),*args],input=response+'\n',text=True,
                              capture_output=True,env=env,timeout=90)
    def calls(self):
        p=self.fake/'calls.jsonl'
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
    def backup(self):
        return next(self.work.glob('p585-manama-*'))

    def test_success_and_restore_from_private_backup(self):
        r=self.run_tool();self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertIn('Manama mock GPS/network providers',r.stdout)
        backup=self.backup();self.assertTrue((backup/'state.sha256').exists())
        self.assertEqual((backup/'state/props/gsm.operator.numeric.present').read_text(),'1\n')
        self.assertEqual((backup/'state/props/gsm.operator.numeric.value').read_text(),'\n')
        self.assertEqual((backup/'state/props/gsm.operator.iso-country.present').read_text(),'0\n')
        state=json.loads((self.fake/'state.json').read_text());self.assertTrue(state['installed']);self.assertTrue(state['module'])
        env=self.env.copy();env.pop('P585_SERIAL');env['SCENARIO']='success'
        # Fake ADB needs to know the serial independently; the real tool loads it from the backup.
        env['P585_SERIAL']=SERIAL
        restore=subprocess.run(['bash',str(backup/'tool.sh'),'restore'],text=True,capture_output=True,env=env,timeout=90)
        self.assertEqual(restore.returncode,0,restore.stdout+restore.stderr)
        self.assertIn('Restore completed',restore.stdout)
        state=json.loads((self.fake/'state.json').read_text());self.assertFalse(state['installed']);self.assertFalse(state['module'])
        self.assertTrue((backup/'state.sha256').exists())
        self.assertTrue(any((self.fake/'fs/data/adb/p585-manama-backups').iterdir()))

    def test_cancel_keeps_device_untouched(self):
        r=self.run_tool(response='')
        self.assertNotEqual(r.returncode,0);self.assertIn('Cancelled',r.stderr)
        self.assertFalse(any(c[0] in ('push','install') for c in self.calls()))
        state=json.loads((self.fake/'state.json').read_text());self.assertFalse(state['installed']);self.assertFalse(state['module'])
        self.assertTrue((self.backup()/'state.sha256').exists())

    def test_identity_and_existing_apps_fail_before_backup_or_changes(self):
        for scenario in ('wrong_sdk','existing_app','other_mock','mock_running','module_exists'):
            with self.subTest(scenario=scenario):
                # Separate run for each scenario, without device writes.
                r=self.run_tool(scenario);self.assertNotEqual(r.returncode,0,r.stdout+r.stderr)
                self.assertFalse(any(c[0] in ('push','install') for c in self.calls()))

    def test_corrupt_stage_or_device_backup_stops_before_install(self):
        for scenario in ('bad_stage','bad_device_backup'):
            with self.subTest(scenario=scenario):
                if scenario=='bad_device_backup':
                    self.tearDown();self.setUp()
                r=self.run_tool(scenario);self.assertNotEqual(r.returncode,0,r.stdout+r.stderr)
                self.assertIn('verification failed' if scenario=='bad_device_backup' else 'readback failed',r.stderr)
                self.assertFalse(any(c[0]=='install' for c in self.calls()))

    def test_failed_install_is_uninstalled_only_after_hash_match(self):
        r=self.run_tool('install_fail');self.assertNotEqual(r.returncode,0)
        self.assertIn('installation was not confirmed',r.stderr)
        state=json.loads((self.fake/'state.json').read_text());self.assertFalse(state['installed']);self.assertFalse(state['module'])
        self.assertFalse(state.get('apply_called',False))

    def test_apply_failure_automatically_restores_and_removes_helper(self):
        r=self.run_tool('apply_fail');self.assertNotEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertIn('Automatic restore completed',r.stderr)
        state=json.loads((self.fake/'state.json').read_text())
        self.assertTrue(state['restore_called']);self.assertFalse(state['installed']);self.assertFalse(state['module'])

    def test_different_installed_app_is_never_uninstalled(self):
        r=self.run_tool('different_installed_apk');self.assertNotEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertIn('does not match the locally signed APK',r.stderr)
        self.assertIn('differs from this backup; it was not removed',r.stderr)
        state=json.loads((self.fake/'state.json').read_text())
        self.assertTrue(state['installed']);self.assertFalse(state['module'])
        self.assertFalse(any(c[0]=='shell' and 'pm uninstall' in ' '.join(c) for c in self.calls()))

    def test_replacement_app_is_not_stopped_or_uninstalled_on_restore(self):
        r=self.run_tool();self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        backup=self.backup()
        (self.fake/'fs/data/app/io.appium.settings-abc/base.apk').write_bytes(b'replacement app')
        restore=self.run_tool(script=backup/'tool.sh',args=('restore',))
        self.assertNotEqual(restore.returncode,0);self.assertIn('helper changed. The watcher was stopped',restore.stderr)
        state=json.loads((self.fake/'state.json').read_text())
        self.assertTrue(state['installed']);self.assertTrue(state['module']);self.assertTrue(state['disabled'])
        self.assertFalse(state.get('restore_called',False))

    def test_missing_helper_with_unknown_mock_does_not_change_settings(self):
        r=self.run_tool();self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        backup=self.backup()
        state=json.loads((self.fake/'state.json').read_text());state['installed']=False
        (self.fake/'state.json').write_text(json.dumps(state))
        (self.fake/'fs/data/app/io.appium.settings-abc/base.apk').unlink()
        restore=self.run_tool(script=backup/'tool.sh',args=('restore',))
        self.assertNotEqual(restore.returncode,0);self.assertIn('helper is absent and a mock provider remains',restore.stderr)
        state=json.loads((self.fake/'state.json').read_text())
        self.assertTrue(state['mock']);self.assertTrue(state['disabled']);self.assertFalse(state.get('restore_called',False))

    def test_unverified_carrier_never_reports_success_and_rolls_back(self):
        r=self.run_tool('no_carrier');self.assertNotEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertIn('could not all be verified',r.stderr)
        self.assertIn('Automatic restore completed',r.stderr)
        state=json.loads((self.fake/'state.json').read_text())
        self.assertTrue(state['restore_called']);self.assertFalse(state['installed']);self.assertFalse(state['module'])

    def test_device_backup_change_stops_restore_before_mutations(self):
        r=self.run_tool();self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        backup=self.backup()
        device_backup=next((self.fake/'fs/data/adb/p585-manama-backups').iterdir())
        (device_backup/'timezone').write_text('Etc/UTC\n')
        restore=self.run_tool(script=backup/'tool.sh',args=('restore',))
        self.assertNotEqual(restore.returncode,0);self.assertIn('Device backup differs: timezone',restore.stderr)
        state=json.loads((self.fake/'state.json').read_text());self.assertTrue(state['module']);self.assertTrue(state['installed'])
        self.assertFalse(state.get('restore_called',False))

    def test_modified_host_backup_rejected_before_restore(self):
        r=self.run_tool();self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        backup=self.backup();(backup/'state/timezone').write_text('Etc/UTC\n')
        restore=self.run_tool(script=backup/'tool.sh',args=('restore',))
        self.assertNotEqual(restore.returncode,0);self.assertIn('Backup checksum mismatch',restore.stderr)
        state=json.loads((self.fake/'state.json').read_text());self.assertTrue(state['module']);self.assertTrue(state['installed'])
        self.assertFalse(state.get('restore_called',False))


if __name__=='__main__':unittest.main()
