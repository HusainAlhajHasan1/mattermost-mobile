#!/usr/bin/env python3
"""Install the pinned experimental ENCMA APK on the identified SM-P585 and open it.

This never replaces or removes an existing/retained package. Opening ENCMA may
reconnect its saved WireGuard VPN and replace an existing VPN; the user chose to
accept that risk. See p585-encma-install.md for limitations and signing details.
"""

import hashlib
import hmac
import os
from pathlib import Path
import re
import resource
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

SERIAL_HASH = '072734241f69cfc548d138f9a496f532972e476926cb69e054ebda981851728d'
PACKAGE = 'com.encma.hxmain'
ACTIVITY = 'com.encma.hxmain/com.mattermost.rnbeta.MainActivity'
ARTIFACT_ID = 13170879612  # Source Actions artifact; the public release holds the same ZIP.
RELEASE_URL = ('https://github.com/HusainAlhajHasan1/mattermost-mobile/releases/download/'
               'encma-android81-armv7-experimental-41503802436/'
               'encma-experimental-android81-armeabi-v7a.zip')
ARCHIVE_SHA256 = '76e1198f4e64961e1f8437548e576a547ae29c90d1b3e9e18008c659066fd99a'
APK_SHA256 = '2aa1cf50e340ca430a8680317bb96151066b1aa47106db35ae5ff585756700bd'
APK_SIZE = 81931868
APK_FILENAME = 'app-armeabi-v7a-release.apk'
MAX_ARCHIVE = 200 * 1024 * 1024
MAX_APK = 200 * 1024 * 1024


class Stop(Exception):
    pass


def say(message):
    print(message, flush=True)


def run(args, timeout=40, check=True, file_limit=None):
    def set_file_limit():
        _, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
        limit = min(file_limit, hard) if hard != resource.RLIM_INFINITY else file_limit
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))

    try:
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=timeout, check=False,
                                preexec_fn=set_file_limit if file_limit is not None else None)
    except (OSError, subprocess.SubprocessError) as exc:
        if args[0] == 'curl':
            raise Stop('GitHub artifact download failed or timed out.') from exc
        raise Stop('{} failed or timed out: {}'.format(args[0], exc)) from exc
    if check and result.returncode != 0:
        # Artifact redirects contain signed temporary URLs; never print curl stderr.
        if args[0] == 'curl':
            raise Stop('GitHub artifact download failed; check the host connection and artifact availability.')
        detail = result.stderr.decode('utf-8', 'replace').strip()[-700:]
        raise Stop('{} failed ({}): {}'.format(args[0], result.returncode, detail))
    return result


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def check_pins():
    if not isinstance(ARTIFACT_ID, int) or ARTIFACT_ID <= 0:
        raise Stop('No published artifact is pinned; refusing to download.')
    for digest in (ARCHIVE_SHA256, APK_SHA256):
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise Stop('The artifact and APK must both have pinned SHA-256 values.')
    if not isinstance(APK_SIZE, int) or not 0 < APK_SIZE <= MAX_APK:
        raise Stop('The expected APK size pin is missing or invalid.')


def download_archive(path):
    check_pins()
    args = ['curl', '-q', '--fail', '--silent', '--show-error', '--location', '--proto', '=https',
            '--proto-redir', '=https', '--max-redirs', '5', '--retry', '2', '--connect-timeout', '20',
            '--max-time', '600', '--max-filesize', str(MAX_ARCHIVE), '--output', str(path), RELEASE_URL]
    run(args, timeout=630, file_limit=MAX_ARCHIVE)
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_ARCHIVE:
        raise Stop('GitHub artifact is empty or unexpectedly large; nothing installed.')
    if not hmac.compare_digest(file_hash(path), ARCHIVE_SHA256):
        raise Stop('GitHub artifact SHA-256 did not match the pin; nothing installed.')


def extract_verified_apk(archive, apk):
    check_pins()
    try:
        with zipfile.ZipFile(archive) as data:
            entries = data.infolist()
            if len(entries) != 1 or entries[0].filename != APK_FILENAME:
                raise Stop('Artifact contained unexpected files; nothing installed.')
            info = entries[0]
            if info.is_dir() or info.file_size != APK_SIZE:
                raise Stop('APK size did not match the pin; nothing installed.')
            digest = hashlib.sha256()
            count = 0
            with data.open(info) as src, open(apk, 'xb') as dst:
                for chunk in iter(lambda: src.read(1024 * 1024), b''):
                    count += len(chunk)
                    if count > info.file_size:
                        raise Stop('APK expanded beyond its recorded size; nothing installed.')
                    digest.update(chunk)
                    dst.write(chunk)
            if count != info.file_size or not hmac.compare_digest(digest.hexdigest(), APK_SHA256):
                raise Stop('APK SHA-256 did not match the pin; nothing installed.')
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        raise Stop('Could not safely extract the verified APK; nothing installed.') from exc
    say('Artifact and APK SHA-256 and APK size matched the published build.')


class Device:
    def __init__(self, serial):
        if not re.fullmatch(r'[A-Za-z0-9]+', serial or ''):
            raise Stop('Set P585_SERIAL to the tablet serial.')
        if hashlib.sha256(serial.encode()).hexdigest() != SERIAL_HASH:
            raise Stop('Wrong serial: this script only supports the previously identified tablet.')
        self.adb = ['adb', '-s', serial]

    def run(self, *args, timeout=40, check=True):
        return run(self.adb + list(args), timeout, check)

    def text(self, *args, timeout=40):
        return self.run(*args, timeout=timeout).stdout.decode('utf-8', 'replace').replace('\r', '').strip()

    def marked(self, command, marker='P585_PM_OK', timeout=40):
        output = self.text('shell', 'if {}; then echo {}; else echo P585_COMMAND_FAILED; fi'.format(command, marker),
                           timeout=timeout)
        if output.splitlines()[-1:] != [marker]:
            raise Stop('A tablet command did not confirm success.')
        return '\n'.join(output.splitlines()[:-1])

    def preflight(self):
        if self.text('get-state') != 'device':
            raise Stop('Boot Android and authorize USB debugging; do not use TWRP.')
        names = ('ro.product.model', 'ro.product.device', 'ro.build.version.incremental',
                 'ro.build.version.sdk', 'ro.product.cpu.abi', 'sys.boot_completed')
        actual = tuple(self.text('shell', 'getprop', name) for name in names)
        if actual != ('SM-P585', 'gtanotexllte', 'P585JXS3CTL1', '27', 'armeabi-v7a', '1'):
            raise Stop('The tablet model, build, Android version, ABI or boot state does not match.')
        if self.text('shell', 'am', 'get-current-user') != '0':
            raise Stop('Switch Android to owner user 0 and try again.')

    def packages(self, include_uninstalled=False):
        option = '-u ' if include_uninstalled else ''
        output = self.marked('pm list packages {}--user 0'.format(option), timeout=60)
        result = set()
        for line in output.splitlines():
            if not line.startswith('package:'):
                raise Stop('Could not safely parse the tablet package list.')
            name = line[len('package:'):].strip()
            if not name:
                raise Stop('The tablet package list contained an empty name.')
            result.add(name)
        if 'android' not in result:
            raise Stop('The tablet package list was incomplete; refusing to install.')
        return result

    def install(self, apk):
        # Recheck just before installation. Never use -r, -d, uninstall or clear.
        if PACKAGE in self.packages(True):
            raise Stop('ENCMA appeared during setup; it was left untouched.')
        result = self.run('install', '--user', '0', str(apk), timeout=240, check=False)
        text = (result.stdout + result.stderr).decode('utf-8', 'replace').strip()
        if result.returncode != 0 or not re.search(r'(?m)^Success\s*$', text):
            raise Stop('Android did not confirm installation: ' + text[-700:])
        if PACKAGE not in self.packages():
            raise Stop('ADB reported success but ENCMA is not installed for owner user 0.')

    def open_app(self):
        # Opening is explicitly requested; no VPN configuration or connection is sent.
        self.preflight()
        if PACKAGE not in self.packages():
            raise Stop('ENCMA is not present for owner user 0.')
        result = self.run('shell', 'am', 'start', '-W', '-n', ACTIVITY, timeout=90, check=False)
        output = (result.stdout + result.stderr).decode('utf-8', 'replace').replace('\r', '').strip()
        if result.returncode != 0 or not re.search(r'(?m)^Status: ok\s*$', output):
            raise Stop('Android did not confirm the launch request: ' + output[-700:])
        time.sleep(5)
        process = self.run('shell', 'pidof', PACKAGE, timeout=30, check=False)
        if process.returncode != 0 or not process.stdout.strip():
            raise Stop('Launch was requested, but the ENCMA process is not running.')


def main():
    present = False
    try:
        for executable in ('adb', 'curl'):
            if not shutil.which(executable):
                raise Stop('Host command {} is required.'.format(executable))
        check_pins()
        device = Device(os.environ.get('P585_SERIAL', ''))
        device.preflight()
        if PACKAGE in device.packages():
            present = True
            say('ENCMA already installed; existing app and data preserved (build not verified).')
        else:
            if PACKAGE in device.packages(True):
                raise Stop('Retained ENCMA package/data exists; it was left untouched.')
            say('Experimental APK: Android 8.1/ARM compatibility is not proven on this tablet.')
            say('The build uses a public debug signing key and is not a trusted production release.')
            say('Downloading via the host network, which may not use the tablet VPN...')
            with tempfile.TemporaryDirectory(prefix='p585-encma-') as temp:
                work = Path(temp)
                archive, apk = work / 'artifact.zip', work / (PACKAGE + '.apk')
                download_archive(archive)
                extract_verified_apk(archive, apk)
                device.preflight()
                device.install(apk)
            present = True
            say('ENCMA installed and confirmed for owner user 0.')
        say('Opening ENCMA as requested. A saved WireGuard VPN may replace the current VPN.')
        device.open_app()
        say('Launch request accepted and ENCMA process is running. Check the tablet screen.')
        say('VPN negotiation, login, server access, and other app features remain untested.')
        return 0
    except (Stop, OSError) as exc:
        if present:
            say('Result: ENCMA is installed/present, but launch is not confirmed: {}'.format(exc))
        else:
            say('Result: ENCMA installation not confirmed: {}'.format(exc))
        return 1


if __name__ == '__main__':
    sys.exit(main())
