"""No-device tests for the P585 Manama timezone/scan companion."""
import copy
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest
import zipfile
import zlib

from scripts.tests.test_p585_manama import FAKE_ANDROID, PROPS

ROOT = pathlib.Path(__file__).resolve().parents[2]
DIR = ROOT / 'scripts' / 'p585-manama-privacy'
MAIN = ROOT / 'scripts' / 'p585-manama-privacy.sh'

FAKE = FAKE_ANDROID.replace("    elif name == 'am':\n", """    elif name == 'app_process':
        operation=args[2]
        if operation == 'validate':
            if s.get('invalid_tz_helper'): code=1; out='not valid\\n'
            else: out='MANAMA_TZ_VALID='+args[3]+'\\n'
        elif operation == 'set':
            if s.get('fail_tz_service'): code=1; out='service error\\n'
            else: s['properties']['persist.sys.timezone']=args[3]; out='MANAMA_TZ_DEFAULT='+args[3]+'\\n'
        elif operation == 'read': out='MANAMA_TZ_DEFAULT='+s['properties']['persist.sys.timezone']+'\\n'
        else: code=1
    elif name == 'am':
""").replace("elif args[0]=='broadcast': out='Broadcast completed\\n'", """elif args[0]=='broadcast':
            if s.pop('fail_broadcast_once',False): code=1; out='broadcast denied\\n'
            else: out='Broadcast completed: result=0\\n'""")


def sha(b): return hashlib.sha256(b).hexdigest()


class IntegrityTests(unittest.TestCase):
    def test_pinned_device_files_match(self):
        script=MAIN.read_text()
        for name,file in [('SERVICE','service.sh'),('TZ','tzcontrol.jar')]:
            data=(DIR/file).read_bytes()
            blob=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
            for suffix,expected in [('BLOB',blob),('SHA',sha(data))]:
                m=re.search(rf"readonly {name}_{suffix}='([^']+)'",script)
                self.assertIsNotNone(m);self.assertEqual(m.group(1),expected)
        with zipfile.ZipFile(DIR/'tzcontrol.jar') as jar:
            self.assertEqual(jar.namelist(),['classes.dex'])
            dex=jar.read('classes.dex')
            self.assertTrue(dex.startswith(b'dex\n035\0'))
            self.assertEqual(dex[12:32],hashlib.sha1(dex[32:]).digest())
            self.assertEqual(int.from_bytes(dex[8:12],'little'),zlib.adler32(dex[12:]) & 0xffffffff)


@unittest.skipUnless(shutil.which('javac') and shutil.which('java'),'JDK not installed')
class JavaReflectionTests(unittest.TestCase):
    def test_dynamic_alarm_interface_and_invalid_zone(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);(root/'android/os').mkdir(parents=True);(root/'android/app').mkdir()
            (root/'android/os/IBinder.java').write_text('package android.os; public interface IBinder {}')
            (root/'android/os/ServiceManager.java').write_text('package android.os; public class ServiceManager { public static IBinder getService(String name) { return new IBinder() {}; } }')
            (root/'android/app/IAlarmManager.java').write_text('''package android.app;
                import android.os.IBinder;
                public interface IAlarmManager { void setTimeZone(String id);
                  public static class Stub { public static IAlarmManager asInterface(IBinder b) {
                    return new IAlarmManager() { public void setTimeZone(String id) {
                      if (id.equals("UTC")) throw new SecurityException("denied");
                      System.setProperty("user.timezone", id);
                    }};
                  }}
                }''')
            classes=root/'classes';classes.mkdir()
            files=[str(p) for p in root.glob('android/**/*.java')]+[str(DIR/'TzControl.java')]
            subprocess.run(['javac','-d',str(classes),*files],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            def call(*args):
                return subprocess.run(['java','-Duser.timezone=Asia/Tehran','-cp',str(classes),'local.manama.TzControl',*args],text=True,capture_output=True,timeout=20)
            self.assertEqual(call('read').stdout.strip(),'MANAMA_TZ_DEFAULT=Asia/Tehran')
            result=call('set','Asia/Bahrain');self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.strip(),'MANAMA_TZ_DEFAULT=Asia/Bahrain')
            bad=call('set','Not/A_Real_Zone');self.assertNotEqual(bad.returncode,0)
            denied=call('set','UTC');self.assertNotEqual(denied.returncode,0);self.assertIn('denied',denied.stderr)


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name)
        self.mod=self.root/'modules/p585_manama_privacy'; self.mod.mkdir(parents=True)
        self.base=self.root/'modules/p585_manama';self.base_owner='p585-manama-Ab12Cd34'
        self.token='p585-privacy-Ab12Cd34';self.backups=self.root/'backups';self.backup=self.backups/self.token
        (self.backup/'props').mkdir(parents=True);(self.mod/'touched').mkdir()
        (self.mod/'owner').write_text(self.token+'\n');(self.backup/'token').write_text(self.token+'\n')
        shutil.copyfile(DIR/'tzcontrol.jar',self.mod/'tzcontrol.jar')
        (self.backup/'tz_sha256').write_text(sha((self.mod/'tzcontrol.jar').read_bytes())+'\n')
        self.original={'properties':{'sys.boot_completed':'1','persist.sys.timezone':'Asia/Tehran',
                         'gsm.sim.operator.numeric':'','gsm.sim.operator.alpha':'',
                         'gsm.operator.numeric':'','gsm.operator.alpha':'Outside', 'irrelevant':'keep'},
                       'settings':{'global:auto_time_zone':'1','global:wifi_scan_always_enabled':'1'},
                       'mock_active':True,'calls':[]}
        self.data=self.root/'state.json';self.write(self.original)
        for k,v in [('timezone','Asia/Tehran'),('auto_time_zone','1'),('wifi_scan_always_enabled','1'),
                    ('ble_scan_always_enabled','null'),('carrier_mode','independent'),('base_owner','none')]:
            (self.backup/k).write_text(v+'\n')
        for key in PROPS:
            (self.backup/'props'/f'{key}.present').write_text(('1' if key in self.original['properties'] else '0')+'\n')
            (self.backup/'props'/f'{key}.value').write_text(self.original['properties'].get(key,'')+'\n')
        self.bin=self.root/'bin';self.bin.mkdir()
        for name in ('getprop','settings','dumpsys','magisk','am','app_process','sleep'):
            p=self.bin/name;p.write_text(FAKE);p.chmod(0o755)
        text=(DIR/'service.sh').read_text()
        text=text.replace('/data/adb/modules/p585_manama_privacy',str(self.mod))
        text=text.replace('/data/adb/modules/p585_manama',str(self.base))
        text=text.replace('/data/adb/p585-privacy-backups/',str(self.backups)+'/')
        self.busybox=bool(os.environ.get('TEST_BUSYBOX_ASH')=='1' and shutil.which('busybox'))
        if self.busybox:
            text=re.sub(r'\bsleep ([135])(?=[;\s])',lambda m: '"'+str(self.bin/'sleep')+'" '+m.group(1),text)
        (self.mod/'service.sh').write_text(text)
        self.env=os.environ.copy();self.env.update(PATH=str(self.bin)+os.pathsep+self.env['PATH'],SIM_STATE=str(self.data),SIM_MODULE=str(self.mod),SIM_STOP_AFTER_TICK='1')
        if self.busybox:self.env['ASH_STANDALONE']='1'

    def tearDown(self):self.tmp.cleanup()
    def write(self,s):self.data.write_text(json.dumps(copy.deepcopy(s)))
    def state(self):return json.loads(self.data.read_text())
    def run_action(self,mode):
        shell=['busybox','ash'] if self.busybox else ['sh']
        return subprocess.run(shell+[str(self.mod/'service.sh'),mode],env=self.env,text=True,capture_output=True,timeout=30)
    def shared(self):
        self.base.mkdir();(self.base/'owner').write_text(self.base_owner+'\n')
        (self.backup/'base_owner').write_text(self.base_owner+'\n');(self.backup/'carrier_mode').write_text('shared\n')

    def test_apply_and_restore_preserve_original_settings(self):
        apply=self.run_action('apply');self.assertEqual(apply.returncode,0,apply.stdout+apply.stderr)
        s=self.state();self.assertEqual(s['properties']['persist.sys.timezone'],'Asia/Bahrain')
        self.assertEqual(s['properties']['gsm.sim.operator.numeric'],'42601')
        self.assertEqual(s['settings']['global:auto_time_zone'],'0');self.assertEqual(s['settings']['global:wifi_scan_always_enabled'],'0')
        self.assertEqual(s['settings']['global:ble_scan_always_enabled'],'0')
        self.assertTrue(s['mock_active'])
        r=self.run_action('restore');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        s=self.state();self.assertEqual(s['properties'],self.original['properties']);self.assertEqual(s['settings'],self.original['settings'])
        self.assertTrue(s['mock_active']);self.assertTrue((self.mod/'disable').exists())

    def test_shared_module_is_not_overwritten_or_removed(self):
        self.shared();apply=self.run_action('apply');self.assertEqual(apply.returncode,0,apply.stdout+apply.stderr)
        self.assertEqual(self.state()['properties']['gsm.sim.operator.numeric'],'')
        r=self.run_action('restore');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual(self.state()['settings'],self.original['settings']);self.assertTrue(self.base.exists())

    def test_no_mock_or_changed_setting_fails_before_changes(self):
        s=self.state();s['mock_active']=False;self.write(s)
        r=self.run_action('apply');self.assertNotEqual(r.returncode,0);self.assertIn('mock GPS',r.stderr)
        self.assertEqual(self.state()['properties'],self.original['properties']);self.assertEqual(list((self.mod/'touched').iterdir()),[])
        s=self.state();s['mock_active']=True;s['settings']['global:wifi_scan_always_enabled']='0';self.write(s)
        r=self.run_action('apply');self.assertNotEqual(r.returncode,0);self.assertIn('changed since backup',r.stderr)
        self.assertEqual(list((self.mod/'touched').iterdir()),[])

    def test_timezone_broadcast_failure_can_be_restored(self):
        s=self.state();s['fail_broadcast_once']=True;self.write(s)
        r=self.run_action('apply');self.assertNotEqual(r.returncode,0);self.assertEqual(self.state()['properties']['persist.sys.timezone'],'Asia/Bahrain')
        restore=self.run_action('restore');self.assertEqual(restore.returncode,0,restore.stdout+restore.stderr)
        self.assertEqual(self.state()['properties'],self.original['properties']);self.assertEqual(self.state()['settings'],self.original['settings'])

    def test_timezone_helper_tampered_fails_without_changing_settings(self):
        (self.mod/'tzcontrol.jar').write_bytes(b'tampered')
        r=self.run_action('apply');self.assertNotEqual(r.returncode,0);self.assertIn('could not be validated',r.stderr)
        self.assertEqual(self.state()['properties'],self.original['properties']);self.assertEqual(list((self.mod/'touched').iterdir()),[])

    def test_new_values_are_preserved_and_no_sim_is_created(self):
        self.assertEqual(self.run_action('apply').returncode,0)
        s=self.state();s['properties']['gsm.operator.numeric']='310260';s['settings']['global:wifi_scan_always_enabled']='1';self.write(s)
        r=self.run_action('restore');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual(self.state()['properties']['gsm.operator.numeric'],'310260')
        self.assertEqual(self.state()['settings']['global:wifi_scan_always_enabled'],'1')
        self.assertIn('changed independently',r.stdout)
        self.assertNotIn('sim_state',self.state())

    def test_stale_boot_marker_does_not_restore_radio_values(self):
        self.assertEqual(self.run_action('apply').returncode,0)
        (self.mod/'spoof-boot').write_text('old-boot-id\n')
        r=self.run_action('restore');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual(self.state()['properties']['gsm.sim.operator.numeric'],'42601')

    def test_worker_reapplies_timezone_and_scan_flags(self):
        self.assertEqual(self.run_action('apply').returncode,0)
        s=self.state();s['properties']['persist.sys.timezone']='Asia/Tehran'
        s['settings']['global:auto_time_zone']='1';s['settings']['global:wifi_scan_always_enabled']='1';self.write(s)
        worker=self.run_action('worker');self.assertEqual(worker.returncode,0,worker.stdout+worker.stderr)
        s=self.state();self.assertEqual(s['properties']['persist.sys.timezone'],'Asia/Bahrain')
        self.assertEqual(s['settings']['global:auto_time_zone'],'0');self.assertEqual(s['settings']['global:wifi_scan_always_enabled'],'0')

    def test_uncertain_lock_fails_before_changes(self):
        (self.mod/'control.lock').mkdir()
        r=self.run_action('apply');self.assertNotEqual(r.returncode,0);self.assertIn('uncertain action lock',r.stderr)
        self.assertEqual(self.state()['properties'],self.original['properties']);self.assertEqual(self.state()['settings'],self.original['settings'])

    def test_worker_stops_if_original_module_disappears(self):
        self.shared();self.assertEqual(self.run_action('apply').returncode,0)
        shutil.rmtree(self.base)
        r=self.run_action('worker');self.assertEqual(r.returncode,1,r.stdout+r.stderr)
        self.assertIn('Original Manama module changed',r.stdout)
        restore=self.run_action('restore');self.assertEqual(restore.returncode,0,restore.stdout+restore.stderr)
        self.assertIn('leaving timezone untouched',restore.stdout)


if __name__=='__main__':unittest.main()
