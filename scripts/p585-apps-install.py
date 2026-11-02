#!/usr/bin/env python3
"""Install the F-Droid and Aurora clients and attempt Instagram via Aurora.

For the previously identified SM-P585 only. No APK mirrors, package removals,
partition writes, Google-account login, or changes to the location modules.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET

SERIAL_HASH = '072734241f69cfc548d138f9a496f532972e476926cb69e054ebda981851728d'
KEY_URL = ('https://api.github.com/repos/f-droid/fdroidclient/contents/'
           'f-droid.org-signing-key.gpg?ref=ed2b119f33aeaa04b722f85fc267cc7dffca28a9')
KEY_HASH = 'b2ee80ba470992f8f44943a4a763235ba9494721fb0f5a6f540798603634cfac'
PRIMARY_FPR = '37D2C98789D8311948394E3E41E7044E1DBA2E89'
SIGNING_FPR = '802A9799016112346E1FEFF47A029E54DD5DCE7A'

# Detached signatures obtained from the corresponding official F-Droid URLs.
# Keeping the signatures here pins the exact signed APK bytes, not just a URL.
FDROID_SIGNATURE = '''-----BEGIN PGP SIGNATURE-----

iQGzBAABCgAdFiEEgCqXmQFhEjRuH+/0egKeVN1dznoFAmmXm0EACgkQegKeVN1d
znq9vAv/boUhRPfkJpUVWrFjkXXYxWirAcSle4pgz+Np+V3+s+doreHpOpZH7pVz
RbH5+cV9wwMikJBQ1EicnbTXVx70AOvHf/yPzsZ5itM14P5SxsXhncB/COLJ5p0Y
NEqOic7Gt1+8XYbnZjn7C6A6xmwo3fVmXUEKHi+INwpaSJ4kPbt2XuPx25vg4v/u
TCsS4bZlnj5sX1bXbz0b7mGBo9chxh3KhEn/gpJ83VBQz+btWdDjwEQrhnEl8XlE
I0HYPVlxsZD4UKRf9iN2q1kM6KW2VjoDvmGlj2+gRY6x1pHzUloRENLaQ6bgpu3y
dYbj8wu4K6tHPtWDy3oXyVFqJn1e9GW1DFvmkwH7z3Q7WtFjKnuaj5LnWrzC84jE
O54Zij2TCMK8QrM9szaXjwt6D6ebTzRfZ5sFaErWs+8/UWBH6bn5tmaRCdaUn6UB
BONJ2P+T9GWJda6RcWTHWmCl1wRdZvpGZQBM4HGmPPAeH9MkGGeLqOmuhirpVgqP
B6L04C7j
=nxXb
-----END PGP SIGNATURE-----
'''
AURORA_SIGNATURE = '''-----BEGIN PGP SIGNATURE-----

iQGzBAABCgAdFiEEgCqXmQFhEjRuH+/0egKeVN1dznoFAmpsaOkACgkQegKeVN1d
znov7wv+NppnueOmuFDtYYhshQ3Y8RJgobhyK0hG/LTVrbu2x8R0+r+ViIyRpxoq
d4KkXqKWlU0m1+CK2R8ps2F3Z42p7fN4Zhjk7WAmy5pbCrTnMO3KPozP6G2DW6qu
azaN/ZVI6qqrsc+jXDwDpvWIscpIH+tkG3S2UsLQmFfH8c9QXBZAOQwDW9+6JePJ
y7aojUf5qwShZNvlrS2w0Vz/FvaGsYsjD/FnqOqu41oRUNUcNc7SnzEAP5JWPxl4
dlmoVi02iP9+oB1aD2zMtbFAvC6Ui0fVlDD5HJ46RYvzrjHsFma0OtYmdgcEr5lf
zbaW+ZZmFqKYWPu5UZFbqhUUaeWFmijZQ6ryCDfAV1TRSJm0mnJ7w7uYt9A+q/PB
CSsetUBjANQX+KiBChGACkrR+L+ZW3Ez6Kn+MnlUfU0bMl3aBUDzd0O9ZLuDuLrX
YQGcFoHLQb34yN2RXaMBfXbj/B/pWvbC2vrZ0Mw+qqXdzQS/2+x0oUsplQZ4QGHG
WbX0mx+v
=wi0t
-----END PGP SIGNATURE-----
'''

FDROID = 'org.fdroid.fdroid'
AURORA = 'com.aurora.store'
INSTAGRAM = 'com.instagram.android'
SPECS = {
    FDROID: ('F-Droid 1.23.2', 'https://f-droid.org/repo/org.fdroid.fdroid_1023052.apk', FDROID_SIGNATURE),
    AURORA: ('Aurora Store 4.8.4', 'https://f-droid.org/repo/com.aurora.store_76.apk', AURORA_SIGNATURE),
}

# Labels from Aurora 4.8.4's English, Persian and Arabic resources. If a UI is
# not recognized, the script stops rather than tapping guessed coordinates.
SKIP = {'Skip', 'تخطي'}
NEXT = {'Next', 'پَسین', 'التالي'}
WELCOME = {'Welcome', 'خوش‌آمَدید', 'أهلًا وسهلًا'}
ANONYMOUS = {'Anonymous', 'ناشناس', 'حساب مجهول'}
GOOGLE = {'Google', 'گوگل', 'حساب Google'}
MAIN_LABELS = {'Apps', 'Games', 'Updates', 'بَرنامہ‌ھا', 'بازی‌ها', 'بروزرسانی‌ها',
               'التطبيقات', 'الألعاب', 'التحديثات'}
MORE = {'More', 'المزيد'}
MANAGE_ACCOUNT = {'Manage your account', 'أدر حسابك'}
INSTALL = {'Install', 'INSTALL', 'نصب', 'ثبِّت', 'تثبيت'}
MANUAL = {'Manual download', 'بارگیری دستی', 'تنزيل يدوي'}
INSTALLERS = {'com.android.packageinstaller', 'com.google.android.packageinstaller',
              'com.samsung.android.packageinstaller'}


class Stop(Exception):
    pass


def say(message):
    print(message, flush=True)


def execute(args, timeout=40, check=True):
    try:
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Stop(f'{args[0]} failed or timed out: {exc}') from exc
    if check and result.returncode != 0:
        detail = result.stderr.decode('utf-8', 'replace').strip()[-1000:]
        raise Stop(f'{args[0]} failed ({result.returncode}): {detail}')
    return result


def digest_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def download(url, path, api=False):
    args = ['curl', '--fail', '--silent', '--show-error', '--location',
            '--proto', '=https', '--proto-redir', '=https', '--retry', '2',
            '--connect-timeout', '20', '--max-time', '240']
    if api:
        args += ['--header', 'Accept: application/vnd.github.raw+json']
    execute(args + ['--output', str(path), url], timeout=270)
    if not path.is_file() or path.stat().st_size == 0:
        raise Stop('Download was empty.')


def get_key(work):
    key = work / 'fdroid-key.gpg'
    download(KEY_URL, key, api=True)
    if digest_file(key) != KEY_HASH:
        raise Stop('F-Droid public-key checksum mismatch; refusing to install.')
    return key


def verify_apk(apk, signature, key, work):
    sig = work / (apk.name + '.asc')
    sig.write_text(signature, encoding='ascii')
    result = execute(['gpgv', '--status-fd', '1', '--keyring', str(key), str(sig), str(apk)],
                     timeout=90, check=False)
    lines = result.stdout.decode('utf-8', 'replace').splitlines()
    valid = [line.split() for line in lines if line.startswith('[GNUPG:] VALIDSIG ')]
    if (result.returncode != 0 or len(valid) != 1 or valid[0][2] != SIGNING_FPR
            or valid[0][-1] != PRIMARY_FPR):
        raise Stop('F-Droid signature verification failed; refusing to install this APK.')


class Device:
    def __init__(self, serial):
        if not re.fullmatch(r'[A-Za-z0-9]+', serial or ''):
            raise Stop('Set P585_SERIAL to the tablet serial.')
        if hashlib.sha256(serial.encode()).hexdigest() != SERIAL_HASH:
            raise Stop('Wrong serial: this script only supports the previously identified tablet.')
        self.serial = serial
        self.adb = ['adb', '-s', serial]

    def run(self, *args, timeout=40, check=True):
        return execute(self.adb + list(args), timeout=timeout, check=check)

    def text(self, *args, timeout=40):
        return self.run(*args, timeout=timeout).stdout.decode('utf-8', 'replace').replace('\r', '').strip()

    def marked(self, command, marker='P585_COMMAND_OK', timeout=40):
        # Explicit markers detect older adbd versions that mask shell failures.
        output = self.text('shell', f'if {command}; then echo {marker}; else echo P585_COMMAND_FAILED; fi',
                           timeout=timeout)
        if output.splitlines()[-1:] != [marker]:
            raise Stop('A tablet command did not confirm success.')
        return '\n'.join(output.splitlines()[:-1])

    def preflight(self):
        if self.text('get-state') != 'device':
            raise Stop('Boot Android normally and authorize USB debugging, not TWRP.')
        props = {name: self.text('shell', 'getprop', name) for name in (
            'ro.product.model', 'ro.product.device', 'ro.build.version.incremental',
            'ro.build.version.sdk', 'ro.product.cpu.abi', 'sys.boot_completed')}
        expected = ('SM-P585', 'gtanotexllte', 'P585JXS3CTL1', '27', 'armeabi-v7a', '1')
        if tuple(props.values()) != expected:
            raise Stop('The connected model, build, ABI or boot state does not match the expected tablet.')
        if self.text('shell', 'am', 'get-current-user') != '0':
            raise Stop('Switch Android to owner user 0 and try again.')

    def packages(self, include_uninstalled=False):
        options = '-u ' if include_uninstalled else ''
        result = self.marked(f'pm list packages {options}--user 0', 'P585_PM_OK', timeout=60)
        names = set()
        for line in result.splitlines():
            if not line.startswith('package:'):
                raise Stop('Could not parse the tablet package list safely.')
            names.add(line[len('package:'):].strip())
        return names

    def install(self, apk, package):
        # Recheck immediately before installing; never use -r, -d, uninstall or pm clear.
        if package in self.packages(True):
            raise Stop('Package appeared during setup; left untouched.')
        result = self.run('install', '--user', '0', str(apk), timeout=180, check=False)
        text = (result.stdout + result.stderr).decode('utf-8', 'replace').strip()
        if result.returncode != 0 or not re.search(r'(?m)^Success\s*$', text):
            raise Stop('Android did not confirm installation: ' + text[-700:])
        if package not in self.packages():
            raise Stop('ADB reported success but the expected package is not installed for user 0.')

    def installed_apk_hash(self, package):
        result = self.marked(f'pm path {package}', 'P585_PATH_OK')
        paths = [line[len('package:'):] for line in result.splitlines() if line.startswith('package:')]
        if len(paths) != 1 or not re.fullmatch(r'/data/app/[A-Za-z0-9_.$+=/@-]+\.apk', paths[0]):
            raise Stop('Existing Aurora has an unexpected APK path or splits; leaving it untouched.')
        # The path is constrained above and cannot inject shell syntax.
        result = self.run('exec-out', 'cat', paths[0], timeout=90)
        if not result.stdout:
            raise Stop('Cannot read the installed Aurora APK for comparison.')
        return hashlib.sha256(result.stdout).hexdigest()

    def start_aurora(self):
        self.marked('am start -W -a android.intent.action.MAIN '
                    '-c android.intent.category.LAUNCHER -n com.aurora.store/.ComposeActivity',
                    'P585_START_OK', timeout=60)

    def open_instagram(self):
        # Aurora 4.8.4 explicitly supports ACTION_SHOW_APP_INFO + EXTRA_PACKAGE_NAME.
        self.marked('am start -W -a android.intent.action.SHOW_APP_INFO '
                    '-n com.aurora.store/.ComposeActivity '
                    '--es android.intent.extra.PACKAGE_NAME com.instagram.android',
                    'P585_DETAILS_OK', timeout=60)

    def dump_ui(self):
        # A random, private shell temp path; never use a fixed shared filename.
        path = '/data/local/tmp/p585-apps-' + uuid.uuid4().hex + '.xml'
        command = (f"p='{path}'; if [ -e \"$p\" ]; then exit 1; fi; "
                   'if uiautomator dump "$p" >/dev/null 2>&1 && [ -s "$p" ]; then '
                   'cat "$p"; r=$?; rm -f "$p"; [ "$r" -eq 0 ] || exit 1; '
                   'echo P585_UI_OK; else rm -f "$p"; exit 1; fi')
        result = self.text('shell', command, timeout=45)
        if not result.endswith('P585_UI_OK'):
            raise Stop('Could not read the UI; unlock Android and keep its display awake.')
        xml = result[:-len('P585_UI_OK')].strip()
        try:
            return ET.fromstring(xml)
        except ET.ParseError as exc:
            raise Stop('The tablet returned an unreadable UI hierarchy.') from exc

    def tap(self, xy):
        x, y = xy
        self.marked(f'input tap {x} {y}', 'P585_TAP_OK')

    def appop(self):
        out = self.marked('appops get --user 0 com.aurora.store REQUEST_INSTALL_PACKAGES',
                          'P585_APPOP_OK')
        if out.strip() in ('No operations.', 'No operations'):
            return 'default'
        matches = re.findall(r'(?im)^\s*REQUEST_INSTALL_PACKAGES:\s*(allow|ignore|deny|default)\b', out)
        if len(matches) != 1:
            raise Stop('Cannot safely determine Aurora’s current install-apps permission.')
        return matches[0].lower()

    def set_appop(self, mode):
        if mode not in ('allow', 'ignore', 'deny', 'default'):
            raise Stop('Invalid saved app-op mode.')
        self.marked(f'appops set --user 0 com.aurora.store REQUEST_INSTALL_PACKAGES {mode}',
                    'P585_APPOP_SET')
        if self.appop() != mode:
            raise Stop('Could not verify Aurora’s install-apps permission.')


def foreground_package(root):
    # UiAutomator dumps the active window. Refuse to tap another window or an
    # Aurora node underneath a system dialog, even if labels happen to match.
    for node in root.iter('node'):
        if node.get('package'):
            return node.get('package')
    return None


def labels(root, package):
    if foreground_package(root) != package:
        return set()
    out = set()
    for node in root.iter('node'):
        if node.get('package') == package:
            for attr in ('text', 'content-desc'):
                value = node.get(attr, '').strip()
                if value:
                    out.add(value)
    return out


def tap_target(root, package, choices):
    if foreground_package(root) != package:
        return None
    parents = {child: parent for parent in root.iter() for child in parent}
    coords = set()
    for node in root.iter('node'):
        if node.get('package') != package or node.get('enabled', 'true') != 'true':
            continue
        if not ({node.get('text', '').strip(), node.get('content-desc', '').strip()} & choices):
            continue
        current = node
        while (current.get('clickable') != 'true' and current in parents
               and parents[current].get('package') == package):
            current = parents[current]
        if current.get('clickable') != 'true' or current.get('enabled', 'true') != 'true':
            continue
        match = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', current.get('bounds', ''))
        if not match:
            continue
        x1, y1, x2, y2 = map(int, match.groups())
        if x2 > x1 and y2 > y1:
            coords.add(((x1 + x2) // 2, (y1 + y2) // 2))
    return next(iter(coords)) if len(coords) == 1 else None


def instagram_visible(root, package):
    return bool(labels(root, package) & {'Instagram', 'com.instagram.android', 'اینستاگرام'})


def login_aurora(device, seconds=100):
    say('Opening Aurora; attempting anonymous setup. Keep Android unlocked and do not touch it.')
    device.start_aurora()
    deadline = time.monotonic() + seconds
    skipped = False
    anonymous_clicked = False
    while time.monotonic() < deadline:
        root = device.dump_ui()
        text = labels(root, AURORA)
        if not skipped and text & WELCOME and text & NEXT and text & SKIP:
            xy = tap_target(root, AURORA, SKIP)
            if xy:
                device.tap(xy)
                skipped = True
                say('Aurora onboarding skipped with its defaults.')
                time.sleep(3)
                continue
        if not anonymous_clicked and text & ANONYMOUS and text & GOOGLE:
            xy = tap_target(root, AURORA, ANONYMOUS)
            if xy:
                device.tap(xy)
                anonymous_clicked = True
                say('Requested Aurora anonymous login; waiting for its service.')
                time.sleep(3)
                continue
        if len(text & MAIN_LABELS) >= 2:
            if anonymous_clicked:
                return True
            # A previously configured Aurora might have a personal account.
            # Verify the active account label rather than silently using it.
            xy = tap_target(root, AURORA, MORE)
            if not xy:
                raise Stop('An existing Aurora session cannot be verified as anonymous; '
                           'no download was requested.')
            device.tap(xy)
            time.sleep(2)
            account_text = labels(device.dump_ui(), AURORA)
            if account_text & ANONYMOUS and account_text & MANAGE_ACCOUNT:
                say('Confirmed Aurora reports an anonymous active account.')
                return True
            raise Stop('Aurora’s existing session was not confirmed anonymous; '
                       'no download was requested.')
        if instagram_visible(root, AURORA) and text & MANUAL and anonymous_clicked:
            return True
        time.sleep(3)
    raise Stop('Aurora did not reach its app list. Anonymous login, internet, a locked screen, '
               'an unsupported language or onboarding may need attention.')


def save_appop_backup(serial, mode):
    base = Path.home() / 'root-p585'
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='p585-apps-backup-', dir=str(base)))
    record = {'serial': serial, 'package': AURORA, 'operation': 'REQUEST_INSTALL_PACKAGES',
              'previous_mode': mode, 'note': 'Captured before the temporary permission change.'}
    path = directory / 'aurora-appop-before.json'
    path.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    os.chmod(path, 0o600)
    return path


def instagram_via_aurora(device, seconds=300):
    login_aurora(device)
    old_mode = device.appop()
    changed = False
    backup = None
    # Change only Aurora's per-app install source permission, and restore it
    # even on a normal error or interrupt. Do not grant root to Aurora.
    try:
        if old_mode != 'allow':
            backup = save_appop_backup(device.serial, old_mode)
            say(f'Backed up Aurora install permission: {backup}')
            # Mark first: if a command changes it but confirmation fails, finally restores it.
            changed = True
            device.set_appop('allow')
            say('Temporarily allowing Aurora to request app installation.')
        device.open_instagram()
        say('Looking for Instagram in Aurora. Download may take several minutes.')
        deadline = time.monotonic() + seconds
        install_tapped = False
        confirm_tapped = False
        while time.monotonic() < deadline:
            if INSTAGRAM in device.packages():
                return True
            root = device.dump_ui()
            aurora_text = labels(root, AURORA)
            if not install_tapped and instagram_visible(root, AURORA) and aurora_text & MANUAL:
                xy = tap_target(root, AURORA, INSTALL)
                if xy:
                    device.tap(xy)
                    install_tapped = True
                    say('Requested Instagram download through Aurora.')
                    time.sleep(3)
                    continue
            if install_tapped and not confirm_tapped:
                for package in INSTALLERS:
                    if instagram_visible(root, package):
                        xy = tap_target(root, package, INSTALL)
                        if xy:
                            device.tap(xy)
                            confirm_tapped = True
                            say('Approved the Android installer for Instagram.')
                            break
            time.sleep(4)
        raise Stop('Instagram was not confirmed installed. Aurora may be rate-limited, require '
                   'interaction, or offer no compatible version for Android 8.1.')
    finally:
        if changed:
            try:
                device.set_appop(old_mode)
                say(f'Aurora install permission restored to: {old_mode}.')
            except (Stop, KeyboardInterrupt) as exc:
                say('WARNING: automatic permission restoration could not be verified: ' + str(exc))
                say(f'Reconnect the tablet and run: adb -s {device.serial} shell appops set '
                    f'--user 0 {AURORA} REQUEST_INSTALL_PACKAGES {old_mode}')
                say(f'Original permission backup: {backup}')
                raise Stop('Aurora permission needs manual restoration.') from exc


def main():
    os.umask(0o077)
    # Permit finally blocks to restore Aurora's app-op on a normal termination.
    def terminate(_signum, _frame):
        raise Stop('Terminated by signal.')
    signal.signal(signal.SIGTERM, terminate)
    for name in ('adb', 'curl', 'gpgv'):
        if not shutil.which(name):
            raise Stop(f'Missing Linux host command: {name}. Install it and rerun.')
    device = Device(os.environ.get('P585_SERIAL', ''))
    device.preflight()
    say('Tablet matched. No packages will be uninstalled or cleared; no partitions will be changed.')
    say('Aurora anonymous access still contacts Google Play and can reveal the tablet IP/app list.')
    installed, all_packages = device.packages(), device.packages(True)
    status = {}
    for package in (FDROID, AURORA, INSTAGRAM):
        status[package] = ('already installed (left untouched)' if package in installed else
                           'retained package data (left untouched)' if package in all_packages else
                           'not installed')
    need_client = any(package not in all_packages for package in (FDROID, AURORA))
    need_aurora_verification = (INSTAGRAM not in all_packages and AURORA in installed)
    if not need_client and not need_aurora_verification:
        return summarize(status)

    with tempfile.TemporaryDirectory(prefix='p585-apps-') as temp:
        work = Path(temp)
        try:
            key = get_key(work)
        except Stop as exc:
            say('Could not verify F-Droid signing key: ' + str(exc))
            return summarize(status)
        aurora_apk = None
        for package in (FDROID, AURORA):
            if package in all_packages and not (package == AURORA and need_aurora_verification):
                continue
            name, url, signature = SPECS[package]
            try:
                apk = work / (package + '.apk')
                say('Downloading and verifying ' + name + ' from F-Droid...')
                download(url, apk)
                verify_apk(apk, signature, key, work)
                say(name + ': official F-Droid signature verified.')
                if package not in all_packages:
                    device.install(apk, package)
                    status[package] = 'installed and verified'
                    installed.add(package)
                    all_packages.add(package)
                if package == AURORA:
                    aurora_apk = apk
            except Stop as exc:
                say(name + ': ' + str(exc))
        if INSTAGRAM not in all_packages and AURORA in installed and aurora_apk:
            try:
                if device.installed_apk_hash(AURORA) != digest_file(aurora_apk):
                    raise Stop('Existing Aurora differs from the verified 4.8.4 APK; '
                               'it will not be replaced or automated.')
                if instagram_via_aurora(device):
                    status[INSTAGRAM] = 'installed and verified via Aurora'
            except Stop as exc:
                say('Instagram attempt: ' + str(exc))
    return summarize(status)


def summarize(status):
    say('\nResult:')
    for name, package in (('F-Droid', FDROID), ('Aurora Store', AURORA), ('Instagram', INSTAGRAM)):
        say(f'  {name}: {status[package]}')
    if not all(status[p].startswith(('installed and verified', 'already installed'))
               for p in (FDROID, AURORA, INSTAGRAM)):
        say('Incomplete: no APK mirror was used. Keep Wi-Fi on, unlock Android, and retry if needed.')
        return 1
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say('\nInterrupted. Installed apps were not removed.')
        sys.exit(130)
    except Stop as error:
        say('STOP: ' + str(error))
        sys.exit(1)
