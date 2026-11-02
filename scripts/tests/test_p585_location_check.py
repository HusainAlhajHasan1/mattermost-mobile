"""Hermetic tests for the read-only P585 network/location diagnostic."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
SCRIPT=ROOT/'scripts/p585-location-check.sh'

FAKE_ADB=r'''#!/usr/bin/env python3
import os,sys
args=sys.argv[1:]
if args[:2]!=['-s','5203c66b523ca357']:sys.exit(2)
args=args[2:];scenario=os.environ.get('CASE','ok')
if args==['get-state']:
    print('offline' if scenario=='offline' else 'device');sys.exit()
if args==['shell','getprop','ro.product.model']:
    print('other' if scenario=='wrong_model' else 'SM-P585');sys.exit()
if args==['shell','su -c id']:
    print('uid=2000(shell)' if scenario=='not_root' else 'uid=0(root) gid=0(root)');sys.exit()
if args==['shell','su -c sh']:
    body=sys.stdin.read()
    open(os.environ['BODY_PATH'],'w').write(body)
    print('Timezone property: Asia/Bahrain\nPrivacy watcher: RUNNING\nip=198.51.100.1\nloc=DE')
    if scenario!='no_marker':print('P585_DIAGNOSTIC_FINISHED')
    sys.exit(0)
sys.exit(3)
'''

FAKE_ANDROID=r'''#!/usr/bin/env python3
import os,pathlib,sys
name=pathlib.Path(sys.argv[0]).name;a=sys.argv[1:];s=os.environ.get('CASE','ok')
if name=='getprop':
    values={'persist.sys.timezone':'Asia/Bahrain','persist.sys.locale':'fa-IR','gsm.sim.state':'ABSENT',
            'gsm.sim.operator.numeric':'42601','gsm.operator.numeric':'42601','net.dns1':'192.0.2.53'}
    print(values.get(a[0],''))
elif name=='settings':
    values={'auto_time_zone':'0','location_mode':'3','wifi_scan_always_enabled':'0','ble_scan_always_enabled':'0','http_proxy':'null','private_dns_mode':'null'}
    print(values.get(a[-1],'null'))
elif name=='dumpsys':
    if a==['location']:
        print('Mock Providers:\n Location[gps 26.223536,50.586994 hAcc=1 mock]\n Location[network 26.223536,50.586994 hAcc=1 mock]\n Location[gps 35.000000,51.000000 real]')
    else:print('Network SSID_SECRET DnsAddresses: [192.0.2.53,2001:db8::53] tail')
elif name=='ip':
    print('    inet 192.0.2.20/24 scope global wlan0\n    inet6 2001:db8::1234/64 scope global')
elif name=='curl':
    if s=='network_off' or '-6' in a:sys.exit(1)
    print('ip=198.51.100.1\nloc=DE\nwarp=off\nextra=do-not-show')
elif name=='date': print('2026-11-02 12:00:00 +0300 +03')
'''

class HostTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.bin=self.root/'bin';self.bin.mkdir()
        p=self.bin/'adb';p.write_text(FAKE_ADB);p.chmod(0o755)
        self.env=os.environ.copy();self.env.update(PATH=str(self.bin)+os.pathsep+self.env['PATH'],BODY_PATH=str(self.root/'body'))
    def tearDown(self):self.tmp.cleanup()
    def run_it(self,scenario='ok'):
        env=self.env.copy();env['CASE']=scenario
        return subprocess.run(['bash',str(SCRIPT)],text=True,capture_output=True,env=env,timeout=25)
    def test_success_has_targeted_read_only_commands_and_detects_completion(self):
        r=self.run_it();self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertIn('not the Linux host',r.stdout);self.assertIn('loc=DE',r.stdout)
        body=(self.root/'body').read_text()
        self.assertIn('dumpsys location',body);self.assertIn('dumpsys connectivity',body)
        self.assertIn('www.cloudflare.com/cdn-cgi/trace',body);self.assertIn('o-o.myaddr.l.google.com',body)
        self.assertNotRegex(body,r'\bsettings\s+[^\n]*\b(?:put|delete)\b')
        self.assertNotRegex(body,r'\b(?:setprop|resetprop|reboot|pm grant|rm -rf)\b')
    def test_preflight_refusals_do_not_execute_device_body(self):
        for case in ('offline','wrong_model','not_root'):
            with self.subTest(case=case):
                r=self.run_it(case);self.assertNotEqual(r.returncode,0);self.assertIn('STOP:',r.stderr)
                self.assertFalse((self.root/'body').exists())
    def test_remote_exit_zero_without_marker_is_an_error(self):
        r=self.run_it('no_marker');self.assertNotEqual(r.returncode,0);self.assertIn('did not confirm',r.stderr)

class AndroidCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.bin=self.root/'bin';self.bin.mkdir()
        for name in ('getprop','settings','dumpsys','ip','curl','date'):
            p=self.bin/name;p.write_text(FAKE_ANDROID);p.chmod(0o755)
        text=SCRIPT.read_text();self.body=text.split("<<'PHONE'\n",1)[1].split('\nPHONE\n',1)[0]
        self.env=os.environ.copy();self.env['PATH']=str(self.bin)+os.pathsep+self.env['PATH']
    def tearDown(self):self.tmp.cleanup()
    def run_body(self,case='ok'):
        env=self.env.copy();env['CASE']=case
        return subprocess.run(['sh','-c',self.body],text=True,capture_output=True,env=env,timeout=25)
    def test_reports_requested_signals_without_wifi_name_or_real_location(self):
        r=self.run_body();self.assertEqual(r.returncode,0,r.stderr)
        for expected in ('Asia/Bahrain','gsm.sim.state=ABSENT','wifi_scan_always_enabled=0',
                         '26.223536,50.586994','192.0.2.53','198.51.100.1','loc=DE'):
            self.assertIn(expected,r.stdout)
        self.assertNotIn('SSID_SECRET',r.stdout);self.assertNotIn('35.000000,51.000000',r.stdout);self.assertNotIn('extra=do-not-show',r.stdout)
    def test_does_not_substitute_host_ip_when_external_test_fails(self):
        r=self.run_body('network_off');self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn('Public IP could not be retrieved; no host IP was substituted.',r.stdout)

if __name__=='__main__':unittest.main()
