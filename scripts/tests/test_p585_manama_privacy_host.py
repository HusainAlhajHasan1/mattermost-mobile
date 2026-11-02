"""Fake-ADB installer tests; never contacts a tablet."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from scripts.tests.test_p585_manama_privacy import FAKE, PROPS, DIR, MAIN

SERIAL='simulator123'
FAKE_ADB=r'''#!/usr/bin/env python3
import json,os,pathlib,shutil,subprocess,sys
args=sys.argv[1:]
if args[:2] != ['-s',os.environ['P585_SERIAL']]:sys.exit(4)
args=args[2:];fs=pathlib.Path(os.environ['FAKE_FS']); state=pathlib.Path(os.environ['SIM_STATE'])
def mapped(s): return s.replace('/data/adb/',str(fs/'data/adb')+'/').replace('/data/local/tmp/',str(fs/'data/local/tmp')+'/')
def run(text,root=False):
    text=mapped(text)
    if root and 'PRIVACY_WATCHER_ACTIVE' in text:
        s=json.loads(state.read_text());p=s['properties'];g=s['settings'];out='MANAMA_TZ_DEFAULT='+p['persist.sys.timezone']+'\n'
        out+='persist.sys.timezone='+p['persist.sys.timezone']+'\n'
        for k in ('auto_time_zone','wifi_scan_always_enabled','ble_scan_always_enabled'):out+=k+'='+g.get('global:'+k,'null')+'\n'
        for k in ('gsm.sim.operator.numeric','gsm.sim.operator.iso-country','gsm.sim.operator.alpha','gsm.operator.numeric','gsm.operator.iso-country','gsm.operator.alpha'):out+=k+'='+p.get(k,'')+'\n'
        out+=('PRIVACY_WATCHER_ACTIVE' if s.get('watcher') else 'PRIVACY_WATCHER_MISSING')+'\nPRIVACY_REMOTE_OK\n'
        print(out,end='');return 0
    if root and "service.sh' </dev/null" in text:
        s=json.loads(state.read_text());s['watcher']=True;state.write_text(json.dumps(s))
        mod=fs/'data/adb/modules/p585_manama_privacy';(mod/'disable').unlink(missing_ok=True)
        print('PRIVACY_REMOTE_OK');return 0
    p=subprocess.run(['sh','-c',text],text=True,capture_output=True,env=os.environ.copy())
    print(p.stdout,end='');print(p.stderr,end='',file=sys.stderr)
    # Some older adb versions hide remote failure; sentinel checks must catch it.
    return 0 if root else p.returncode
if args[0]=='get-state':print('device');sys.exit()
if args[0]=='push':
    dest=pathlib.Path(mapped(args[2]));dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(args[1],dest)
    if os.environ.get('SCENARIO')=='bad_stage' and args[2].endswith('/timezone'):dest.write_text('corrupt')
    print('pushed');sys.exit()
if args[0]!='shell':sys.exit(5)
cmd=' '.join(args[1:])
if cmd=='su -c sh':sys.exit(run(sys.stdin.read(),True))
if cmd=='su -c id':print('uid=0(root) gid=0(root) context=u:r:magisk:s0');sys.exit()
if cmd=='am get-current-user':print('0');sys.exit()
sys.exit(run(cmd))
'''
CURL=r'''#!/usr/bin/env python3
import json,os,pathlib,shutil,sys
args=sys.argv[1:];out=pathlib.Path(args[args.index('-o')+1]);url=next(x for x in args if x.startswith('https://'))
shutil.copyfile(json.loads(os.environ['BLOB_MAP'])[url.rsplit('/',1)[-1]],out)
'''
def sha(data):return hashlib.sha256(data).hexdigest()

class HostTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.fs=self.root/'fs'
        (self.fs/'data/adb/modules').mkdir(parents=True);(self.fs/'data/local/tmp').mkdir(parents=True)
        self.work=self.root/'work';self.work.mkdir();self.bin=self.root/'bin';self.bin.mkdir()
        self.state=self.root/'state.json'
        self.initial={'properties':{'ro.product.model':'SM-P585','ro.product.device':'gtanotexllte','ro.build.version.incremental':'P585JXS3CTL1',
                        'ro.build.version.sdk':'27','sys.boot_completed':'1','persist.sys.timezone':'Asia/Tehran',
                        'gsm.sim.operator.numeric':'','gsm.sim.operator.alpha':'','gsm.operator.numeric':'','gsm.operator.alpha':'Outside'},
                      'settings':{'global:auto_time_zone':'1','global:wifi_scan_always_enabled':'1'},'mock_active':True,'calls':[]}
        self.state.write_text(json.dumps(self.initial))
        for name,content in [('adb',FAKE_ADB),('curl',CURL)]+[(x,FAKE) for x in ('getprop','settings','dumpsys','magisk','am','app_process','sleep')]:
            p=self.bin/name;p.write_text(content);p.chmod(0o755)
        service=DIR/'service.sh';text=service.read_text().replace('/data/adb/modules/p585_manama_privacy','@TEST_MOD@')
        text=text.replace('/data/adb/modules/p585_manama','@TEST_BASE@').replace('/data/adb/p585-privacy-backups/','@TEST_BACKUPS@')
        text=text.replace('@TEST_MOD@',str(self.fs/'data/adb/modules/p585_manama_privacy'))
        text=text.replace('@TEST_BASE@',str(self.fs/'data/adb/modules/p585_manama'))
        text=text.replace('@TEST_BACKUPS@',str(self.fs/'data/adb/p585-privacy-backups')+'/')
        self.service=self.root/'service.sh';self.service.write_text(text)
        main=MAIN.read_text().replace("readonly SERIAL_SHA='"+re.search(r"readonly SERIAL_SHA='([^']+)'",MAIN.read_text()).group(1)+"'",f"readonly SERIAL_SHA='{sha(SERIAL.encode())}'")
        blobs={}
        for key,path in [('SERVICE',self.service),('TZ',DIR/'tzcontrol.jar')]:
            data=path.read_bytes();blob=sha(data)[:40];blobs[blob]=str(path)
            main=re.sub(rf"readonly {key}_BLOB='[^']+'",f"readonly {key}_BLOB='{blob}'",main)
            main=re.sub(rf"readonly {key}_SHA='[^']+'",f"readonly {key}_SHA='{sha(data)}'",main)
        self.base_file=self.fs/'data/adb/modules/p585_manama/service.sh'
        self.base_sha=sha(b'base mock service')
        main=re.sub(r"readonly KNOWN_BASE_SHA='[^']+'",f"readonly KNOWN_BASE_SHA='{self.base_sha}'",main)
        self.script=self.root/'tool.sh';self.script.write_text(main)
        self.env=os.environ.copy();self.env.update(PATH=str(self.bin)+os.pathsep+self.env['PATH'],P585_SERIAL=SERIAL,P585_WORKDIR=str(self.work),
            FAKE_FS=str(self.fs),SIM_STATE=str(self.state),SIM_MODULE=str(self.fs/'data/adb/modules/p585_manama_privacy'),BLOB_MAP=json.dumps(blobs))
    def tearDown(self):self.tmp.cleanup()
    def run_tool(self,script=None,args=(),scenario='success',answer='APPLY'):
        env=self.env.copy();env['SCENARIO']=scenario
        return subprocess.run(['bash',str(script or self.script),*args],input=answer+'\n',text=True,capture_output=True,env=env,timeout=90)
    def read(self):return json.loads(self.state.read_text())
    def backup(self):return next(self.work.glob('p585-privacy-*'))
    def assert_original(self):
        s=self.read();self.assertEqual(s['properties'],self.initial['properties']);self.assertEqual(s['settings'],self.initial['settings']);self.assertTrue(s['mock_active'])
    def test_install_and_restore_without_destroying_mock(self):
        r=self.run_tool();self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertIn('Add-on active',r.stdout);self.assertIn('MANAMA_TZ_DEFAULT=Asia/Bahrain',r.stdout)
        s=self.read();self.assertEqual(s['properties']['gsm.sim.operator.numeric'],'42601')
        back=self.backup();self.assertTrue((back/'state.sha256').exists());self.assertEqual((back/'state/carrier_mode').read_text(),'independent\n')
        restore=self.run_tool(back/'tool.sh',('restore',));self.assertEqual(restore.returncode,0,restore.stdout+restore.stderr)
        self.assert_original();self.assertFalse((self.fs/'data/adb/modules/p585_manama_privacy').exists());self.assertTrue(back.exists())
    def test_existing_known_module_keeps_ownership_of_carrier(self):
        base=self.base_file.parent;base.mkdir();(base/'owner').write_text('p585-manama-Ab12Cd34\n');self.base_file.write_bytes(b'base mock service')
        r=self.run_tool();self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual(self.read()['properties']['gsm.sim.operator.numeric'],'');self.assertIn('Restore this add-on BEFORE',r.stdout)
        back=self.backup();self.assertEqual((back/'state/carrier_mode').read_text(),'shared\n')
        restore=self.run_tool(back/'tool.sh',('restore',));self.assertEqual(restore.returncode,0,restore.stdout+restore.stderr)
        self.assert_original();self.assertTrue(base.exists())
    def test_cancel_and_missing_mock_do_not_touch_tablet_settings(self):
        r=self.run_tool(answer='');self.assertNotEqual(r.returncode,0);self.assert_original()
        s=self.read();s['mock_active']=False;self.state.write_text(json.dumps(s))
        r=self.run_tool();self.assertNotEqual(r.returncode,0);self.assertIn('mock GPS was not verified',r.stderr)
        self.assertFalse((self.fs/'data/adb/modules/p585_manama_privacy').exists())
    def test_bad_stage_aborts_before_apply(self):
        r=self.run_tool(scenario='bad_stage');self.assertNotEqual(r.returncode,0);self.assertIn('Readback failed: timezone',r.stderr)
        self.assert_original();self.assertFalse((self.fs/'data/adb/modules/p585_manama_privacy').exists())
    def test_failed_broadcast_rolls_back_and_preserves_mock(self):
        s=self.read();s['fail_broadcast_once']=True;self.state.write_text(json.dumps(s))
        r=self.run_tool();self.assertNotEqual(r.returncode,0);self.assertIn('Add-on restore completed',r.stderr)
        self.assert_original();self.assertFalse((self.fs/'data/adb/modules/p585_manama_privacy').exists())
    def test_changed_backup_is_refused_before_restore(self):
        r=self.run_tool();self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        back=self.backup();(back/'state/timezone').write_text('Etc/UTC\n')
        restore=self.run_tool(back/'tool.sh',('restore',));self.assertNotEqual(restore.returncode,0);self.assertIn('Host backup checksum mismatch',restore.stderr)
        self.assertTrue((self.fs/'data/adb/modules/p585_manama_privacy').exists())

if __name__=='__main__':unittest.main()
