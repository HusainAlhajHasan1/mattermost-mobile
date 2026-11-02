#!/usr/bin/env python3
"""Safely attempt F-Droid, Uptodown and a pinned Instagram on one SM-P585.

Installs only missing packages, uses no Google login or touchscreen automation,
never uninstalls, and never changes VPN, network, location or system settings.
See p585-uptodown-install.md for the provenance and important limitations.
"""
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import resource
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from html.parser import HTMLParser
from urllib import error, parse, request

SERIAL_HASH = '072734241f69cfc548d138f9a496f532972e476926cb69e054ebda981851728d'
FDROID, STORE, INSTAGRAM = 'org.fdroid.fdroid', 'com.uptodown', 'com.instagram.android'
STORE_APP_ID = '18098'
STORE_FILE_ID = '1232507681'  # Uptodown 7.42
STORE_SHA256 = 'd903bec98d76854baa279c81b97048273a7ffd506e2d421308efbb6e7bf66b76'
IG_VERSION = '378.0.0.52.68'  # code 378213503; single APK, Android 8+, arm-v7a, nodpi
IG_SHA256 = '47e70d9ed13da9228277f8f8b7cb564b845074a71fd0afb118650a52cedacdbe'
IG_BASE = 'https://instagram.en.uptodown.com/android'
KEY_URL = ('https://api.github.com/repos/f-droid/fdroidclient/contents/'
           'f-droid.org-signing-key.gpg?ref=ed2b119f33aeaa04b722f85fc267cc7dffca28a9')
KEY_HASH = 'b2ee80ba470992f8f44943a4a763235ba9494721fb0f5a6f540798603634cfac'
PRIMARY_FPR = '37D2C98789D8311948394E3E41E7044E1DBA2E89'
SIGNING_FPR = '802A9799016112346E1FEFF47A029E54DD5DCE7A'
FDROID_APK = 'https://f-droid.org/repo/org.fdroid.fdroid_1023052.apk'

# The official F-Droid detached signature pins the exact client APK.
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

# Public anonymous-client protocol, as documented in Obtainium's Uptodown
# source. This is not a user account/password. The API may change or block it.
UPTODOWN_CLIENT_KEY = 'MDGMXUMdvHJBG/vjdFgmqX6LUdy7ecfwvYNd0gyfOCs='
API_HOST = 'www.uptodown.app'
API_BASE = 'https://' + API_HOST
UA = 'Dalvik/2.1.0 (Linux; U; Android 16; Pixel 8 Pro Build/BP4A.260205.001)'


class Stop(Exception):
    pass


def say(text):
    print(text, flush=True)


def run(args, timeout=40, check=True, file_limit=None):
    def set_file_limit():
        # Also bound older curl versions when a response has no Content-Length.
        _, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
        limit = min(file_limit, hard) if hard != resource.RLIM_INFINITY else file_limit
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))

    try:
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=timeout, check=False,
                                preexec_fn=set_file_limit if file_limit is not None else None)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Stop(f'{args[0]} failed or timed out: {exc}') from exc
    if check and result.returncode:
        detail = result.stderr.decode('utf-8', 'replace').strip()[-1000:]
        raise Stop(f'{args[0]} failed ({result.returncode}): {detail}')
    return result


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as file:
        for block in iter(lambda: file.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def allowed_uptodown(url):
    try:
        p = parse.urlsplit(url)
        host = (p.hostname or '').lower()
        return (p.scheme == 'https' and p.username is None and p.password is None
                and p.port in (None, 443) and not p.fragment
                and any(host == d or host.endswith('.' + d) for d in
                        ('uptodown.com', 'uptodown.app', 'utdstc.com')))
    except ValueError:
        return False


def safe_origin(url):
    """Report only an origin, never URL paths, query strings, userinfo or IPs."""
    try:
        parts = parse.urlsplit(url)
        scheme = parts.scheme.lower() if parts.scheme.lower() in ('http', 'https') else '(other)'
        host = parts.hostname or ''
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r'[a-zA-Z0-9.-]{1,253}', host):
                host = '(hidden)'
        else:
            host = '(IP hidden)'
        # Do not let control characters or a user-supplied port appear in logs.
        return scheme + '://' + host
    except ValueError:
        return '(invalid origin)'


def same_api_endpoint(old, new):
    """Allow only HTTPS canonicalization of the same path on the API host."""
    try:
        source, target = parse.urlsplit(old), parse.urlsplit(new)
        return (source.scheme == target.scheme == 'https'
                and source.hostname == target.hostname == API_HOST
                and source.port in (None, 443) and target.port in (None, 443)
                and source.username is None and source.password is None
                and target.username is None and target.password is None
                and not target.fragment and bool(source.path)
                and source.path.rstrip('/') == target.path.rstrip('/'))
    except ValueError:
        return False


class RestrictedRedirect(request.HTTPRedirectHandler):
    def __init__(self, with_auth):
        self.with_auth = with_auth

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if self.with_auth and code in (301, 302, 307, 308) and same_api_endpoint(req.full_url, newurl):
            # Preserve POST and its anonymous HMAC, or GET and its bearer, only
            # for the exact same HTTPS API origin and same endpoint. Standard
            # urllib redirects can silently turn a POST into a GET.
            if req.get_method() not in ('GET', 'POST'):
                raise Stop('Unexpected API redirect method; no redirect followed.')
            forwarded = {k: v for k, v in req.header_items()
                         if k.lower() not in ('proxy-authorization', 'cookie')}
            return request.Request(newurl, data=req.data, headers=forwarded,
                                   origin_req_host=req.origin_req_host, unverifiable=True,
                                   method=req.get_method())
        if not self.with_auth and allowed_uptodown(newurl):
            # No bearer, proxy credentials, or cookies may be forwarded from a
            # webpage or CDN request, even to a permitted Uptodown hostname.
            if any(k.lower() in ('authorization', 'proxy-authorization', 'cookie')
                   for k, _value in req.header_items()):
                raise Stop('Unexpected credentials in an Uptodown file redirect; not followed.')
            return super().redirect_request(req, fp, code, msg, headers, newurl)
        path = parse.urlsplit(req.full_url).path
        stage = ('authentication' if self.with_auth and path.rstrip('/') == '/eapi/auth/token' else
                 'file URL lookup' if self.with_auth else 'web/file download')
        same_path = 'yes' if same_api_endpoint(req.full_url, newurl) else 'no'
        raise Stop(f'Installer blocked an Uptodown redirect at {stage} (HTTP {code}, '
                   f'{safe_origin(req.full_url)} -> {safe_origin(newurl)}, '
                   f'same API endpoint: {same_path}). No redirect was followed.')


def open_uptodown(url, headers=None, body=None, auth=False):
    if not allowed_uptodown(url):
        raise Stop('Uptodown returned a URL outside its HTTPS hosts.')
    if auth and parse.urlsplit(url).hostname != API_HOST:
        raise Stop('Refusing to send API authorization to a different host.')
    opener = request.build_opener(RestrictedRedirect(auth))
    req = request.Request(url, data=body, headers=headers or {'User-Agent': UA})
    try:
        return opener.open(req, timeout=30)
    except Stop:
        raise
    except error.HTTPError as exc:
        # Do not print signed URLs, bearer tokens or response bodies.
        raise Stop(f'Uptodown network request failed (HTTP {exc.code}).') from exc
    except (error.URLError, OSError, ValueError) as exc:
        raise Stop('Uptodown network request failed or timed out; check the host connection.') from exc


def response_chunks(response, limit, max_seconds):
    """Bound both bytes and elapsed time, including slow/chunked responses."""
    deadline, count = time.monotonic() + max_seconds, 0
    while True:
        if time.monotonic() > deadline:
            raise Stop('Uptodown download took too long.')
        block = response.read1(64 * 1024)
        if not block:
            return
        count += len(block)
        if count > limit:
            raise Stop('Uptodown response was unexpectedly large.')
        yield block


def small_response(url, headers=None, body=None, auth=False, limit=3 * 1024 * 1024):
    try:
        with open_uptodown(url, headers, body, auth) as response:
            return b''.join(response_chunks(response, limit, 60))
    except (OSError, TimeoutError, http.client.HTTPException) as exc:
        raise Stop('Uptodown response was interrupted or timed out.') from exc


def json_response(url, headers=None, body=None, auth=False, require_success=True):
    try:
        data = json.loads(small_response(url, headers, body, auth))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise Stop('Uptodown did not return valid JSON; it may be blocking automated requests.') from exc
    if (not isinstance(data, dict) or
            (require_success and data.get('success') != 1) or
            (not require_success and 'success' in data and data['success'] != 1)):
        raise Stop('Uptodown did not accept the request; its API may have changed or be blocked.')
    return data


class Uptodown:
    def __init__(self):
        self.token = None

    @staticmethod
    def headers():
        return {'User-Agent': UA, 'Identificador': 'Uptodown_Android',
                'Identificador-Version': '739', 'Accept': 'application/json'}

    def authenticate(self):
        identifier = secrets.token_hex(8)
        stamp = str(int(time.time()))
        signature = hmac.new(UPTODOWN_CLIENT_KEY.encode('utf-8'), stamp.encode('ascii'),
                             hashlib.sha256).hexdigest()
        body = parse.urlencode({'identifier': identifier, 'id_plataforma': '13',
                                'lang': 'en', 'unixtime': stamp, 'hmac': signature}).encode('ascii')
        headers = self.headers()
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
        data = json_response(API_BASE + '/eapi/auth/token?' + parse.urlencode({'identifier': identifier}),
                             headers, body, auth=True, require_success=False)
        token = data.get('token')
        segment = r'[A-Za-z0-9_-]+={0,2}'
        if (not isinstance(token, str) or len(token) > 8192 or
                not re.fullmatch(segment + r'\.' + segment + r'\.' + segment, token)):
            raise Stop('Uptodown did not provide a usable anonymous session.')
        self.token = token

    def download_url(self, app_id, file_id, expected_hash):
        if not re.fullmatch(r'[0-9]{1,15}', app_id) or not re.fullmatch(r'[0-9]{1,15}', file_id):
            raise Stop('Uptodown returned an invalid app or file ID.')
        # Use a fresh anonymous token for each download; the version search
        # between downloads can otherwise allow an earlier token to expire.
        self.authenticate()
        headers = self.headers()
        headers['Authorization'] = 'Bearer ' + self.token
        data = json_response(f'{API_BASE}/eapi/apps/{app_id}/file/{file_id}/downloadUrl',
                             headers, auth=True)
        item = data.get('data')
        if not isinstance(item, dict) or not isinstance(item.get('downloadURL'), str):
            raise Stop('Uptodown did not provide a download URL.')
        if item.get('sha256') and str(item['sha256']).lower() != expected_hash:
            raise Stop('Uptodown returned a different APK hash; refusing to install.')
        url = item['downloadURL']
        if not allowed_uptodown(url):
            raise Stop('Uptodown returned a download URL outside its HTTPS hosts.')
        return url

    def download_apk(self, app_id, file_id, expected_hash, dest, max_bytes):
        url = self.download_url(app_id, file_id, expected_hash)
        digest, count = hashlib.sha256(), 0
        try:
            with open_uptodown(url, {'User-Agent': UA}) as response, open(dest, 'xb') as out:
                for block in response_chunks(response, max_bytes, 300):
                    count += len(block)
                    digest.update(block)
                    out.write(block)
                    if count // (20 * 1024 * 1024) != (count - len(block)) // (20 * 1024 * 1024):
                        say(f'  Downloaded {count // (1024 * 1024)} MiB...')
        except (OSError, TimeoutError, http.client.HTTPException) as exc:
            raise Stop('APK download was interrupted or timed out.') from exc
        if count == 0 or digest.hexdigest() != expected_hash:
            raise Stop('APK SHA-256 did not match the pinned file; it was NOT installed.')
        say('  Pinned SHA-256 matched.')


class AppIdParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get('id') == 'detail-app-name':
            value = attrs.get('data-code', '')
        elif attrs.get('id') == 'detail-download-button':
            value = attrs.get('data-app-id', '')
        else:
            return
        if isinstance(value, str) and re.fullmatch(r'[0-9]{1,15}', value):
            self.ids.add(value)


def instagram_app_id():
    html = small_response(IG_BASE + '/versions').decode('utf-8', 'replace')
    parser = AppIdParser()
    parser.feed(html)
    if len(parser.ids) != 1:
        raise Stop('Could not uniquely identify Instagram in Uptodown’s version list.')
    return next(iter(parser.ids))


def instagram_version_file(app_id):
    """Bounded lookup of one separately pinned APK, not 'latest' or a split.

    Uptodown's web version pages are normally in descending version order.
    Their layout can change, in which case the safe result is no installation.
    """
    if not re.fullmatch(r'[0-9]{1,15}', app_id):
        raise Stop('Invalid Instagram app ID.')
    cache = {}
    deadline = time.monotonic() + 120

    def check_deadline():
        if time.monotonic() > deadline:
            raise Stop('The bounded Uptodown version search took too long.')

    def page(n):
        check_deadline()
        if n not in cache:
            data = json_response(f'{IG_BASE}/apps/{app_id}/versions/{n}')
            # Uptodown returns {"success":1} with no data field at the end.
            rows = data.get('data', [])
            if not isinstance(rows, list) or any(not isinstance(x, dict) for x in rows):
                raise Stop('Uptodown version-list layout changed.')
            cache[n] = rows
        return cache[n]

    def min_major(rows):
        if not rows:
            return -1
        majors = [int(m.group(1)) for row in rows
                  for m in [re.match(r'^(\d+)(?:\.|$)', str(row.get('version', '')))] if m]
        if not majors:
            raise Stop('Could not parse Uptodown Instagram versions safely.')
        return min(majors)

    target = int(IG_VERSION.split('.')[0])
    hi = 1
    while min_major(page(hi)) > target and hi < 512:
        hi *= 2
    if min_major(page(hi)) > target:
        raise Stop('The bounded Uptodown version search did not reach the pinned Instagram build.')
    lo = 1
    while lo < hi:
        mid = (lo + hi) // 2
        if min_major(page(mid)) > target:
            lo = mid + 1
        else:
            hi = mid
    # Include one page before the boundary and inspect at most 16 pages. Even
    # if version ordering changes, an exact binary hash is required to install.
    candidates = []
    for number in range(max(1, lo - 1), min(512, lo + 15) + 1):
        rows = page(number)
        for row in rows:
            if str(row.get('version', '')) != IG_VERSION:
                continue
            if str(row.get('kindFile', row.get('titleKindFile', ''))).lower() != 'apk':
                continue
            file_id = str(row.get('fileID', ''))
            if re.fullmatch(r'[0-9]{1,15}', file_id) and file_id not in candidates:
                candidates.append(file_id)
        if not rows or (number > lo and min_major(rows) < target):
            break
    # Each metadata page is small; never download unknown variants to guess.
    for file_id in candidates[:40]:
        check_deadline()
        html = small_response(f'{IG_BASE}/download/{file_id}-x').decode('utf-8', 'replace').lower()
        if re.search(r'(?<![a-f0-9])' + IG_SHA256 + r'(?![a-f0-9])', html):
            return file_id
    raise Stop('The independently pinned Instagram APK was not found in Uptodown’s checked versions.')


class Device:
    def __init__(self, serial):
        if not re.fullmatch(r'[A-Za-z0-9]+', serial or ''):
            raise Stop('Set P585_SERIAL to the tablet serial.')
        if hashlib.sha256(serial.encode()).hexdigest() != SERIAL_HASH:
            raise Stop('Wrong serial: this script is limited to the previously identified tablet.')
        self.adb = ['adb', '-s', serial]

    def run(self, *args, timeout=40, check=True):
        return run(self.adb + list(args), timeout, check)

    def text(self, *args, timeout=40):
        return self.run(*args, timeout=timeout).stdout.decode('utf-8', 'replace').replace('\r', '').strip()

    def marked(self, command, marker='P585_PM_OK', timeout=40):
        result = self.text('shell', f'if {command}; then echo {marker}; else echo P585_COMMAND_FAILED; fi',
                           timeout=timeout)
        if result.splitlines()[-1:] != [marker]:
            raise Stop('A tablet command did not confirm success.')
        return '\n'.join(result.splitlines()[:-1])

    def preflight(self):
        if self.text('get-state') != 'device':
            raise Stop('Boot Android and authorize USB debugging; do not use TWRP.')
        names = ('ro.product.model', 'ro.product.device', 'ro.build.version.incremental',
                 'ro.build.version.sdk', 'ro.product.cpu.abi', 'sys.boot_completed')
        actual = tuple(self.text('shell', 'getprop', name) for name in names)
        if actual != ('SM-P585', 'gtanotexllte', 'P585JXS3CTL1', '27', 'armeabi-v7a', '1'):
            raise Stop('The tablet model, build, ABI or boot state does not match.')
        if self.text('shell', 'am', 'get-current-user') != '0':
            raise Stop('Switch Android to owner user 0 and try again.')

    def packages(self, include_uninstalled=False):
        option = '-u ' if include_uninstalled else ''
        output = self.marked(f'pm list packages {option}--user 0', timeout=60)
        result = set()
        for line in output.splitlines():
            if not line.startswith('package:'):
                raise Stop('Could not parse the tablet package list safely.')
            result.add(line[len('package:'):].strip())
        return result

    def install(self, apk, package):
        # Do not update, downgrade, clear, uninstall or replace retained data.
        if package in self.packages(True):
            raise Stop('The package appeared during setup; it was left untouched.')
        result = self.run('install', '--user', '0', str(apk), timeout=240, check=False)
        text = (result.stdout + result.stderr).decode('utf-8', 'replace').strip()
        if result.returncode != 0 or not re.search(r'(?m)^Success\s*$', text):
            raise Stop('Android did not confirm installation: ' + text[-700:])
        if package not in self.packages():
            raise Stop('ADB reported success but the expected package is not installed for user 0.')


def curl_download(url, path, github=False):
    limit = 128 * 1024 if github else 64 * 1024 * 1024
    args = ['curl', '--fail', '--silent', '--show-error', '--location', '--proto', '=https',
            '--proto-redir', '=https', '--retry', '2', '--connect-timeout', '20', '--max-time', '240',
            '--max-filesize', str(limit)]
    if github:
        args += ['--header', 'Accept: application/vnd.github.raw+json']
    run(args + ['--output', str(path), url], timeout=270, file_limit=limit)
    if not path.is_file() or not 0 < path.stat().st_size <= limit:
        raise Stop('Download was empty or unexpectedly large.')


def get_fdroid(work):
    for name in ('curl', 'gpgv'):
        if not shutil.which(name):
            raise Stop(f'Host command {name} is required to verify the F-Droid APK.')
    key, apk, sig = work / 'fdroid-key.gpg', work / (FDROID + '.apk'), work / 'fdroid.asc'
    curl_download(KEY_URL, key, github=True)
    if file_hash(key) != KEY_HASH:
        raise Stop('F-Droid signing-key checksum mismatch; not installing.')
    curl_download(FDROID_APK, apk)
    sig.write_text(FDROID_SIGNATURE, encoding='ascii')
    result = run(['gpgv', '--status-fd', '1', '--keyring', str(key), str(sig), str(apk)],
                 timeout=90, check=False)
    valid = [line.split() for line in result.stdout.decode('utf-8', 'replace').splitlines()
             if line.startswith('[GNUPG:] VALIDSIG ')]
    if (result.returncode or len(valid) != 1 or valid[0][2] != SIGNING_FPR
            or valid[0][-1] != PRIMARY_FPR):
        raise Stop('F-Droid signature verification failed; not installing.')
    say('  Official F-Droid signature verified.')
    return apk


def summary(status):
    say('\nResult:')
    for name, package in (('Uptodown', STORE), ('Instagram', INSTAGRAM), ('F-Droid', FDROID)):
        say(f'  {name}: {status[package]}')
    okay = all(status[p].startswith(('installed and confirmed', 'already installed'))
               for p in (STORE, INSTAGRAM, FDROID))
    if not okay:
        say('Incomplete. No existing app/data or VPN setting was removed or changed.')
    return 0 if okay else 1


def main():
    os.umask(0o077)
    if not shutil.which('adb'):
        raise Stop('Missing host command: adb.')
    device = Device(os.environ.get('P585_SERIAL', ''))
    device.preflight()
    say('Tablet matched. Existing apps and retained data will not be replaced or removed.')
    say('Downloads use THIS COMPUTER’s network, which may not use the tablet VPN.')
    installed, known = device.packages(), device.packages(True)
    status = {p: ('already installed (preserved; existing APK not checked)' if p in installed else
                  'retained package/data (preserved, not installed)' if p in known else
                  'not confirmed installed') for p in (STORE, INSTAGRAM, FDROID)}
    if all(p in known for p in (STORE, INSTAGRAM, FDROID)):
        return summary(status)
    client = Uptodown()
    with tempfile.TemporaryDirectory(prefix='p585-uptodown-') as temp:
        work = Path(temp)
        for package in (STORE, INSTAGRAM, FDROID):
            if package in known:
                continue
            try:
                if package == STORE:
                    say('Downloading Uptodown App Store 7.42 from Uptodown...')
                    apk = work / (STORE + '.apk')
                    client.download_apk(STORE_APP_ID, STORE_FILE_ID, STORE_SHA256, apk, 25 * 1024 * 1024)
                elif package == INSTAGRAM:
                    say('Looking for the pinned Android 8 / 32-bit Instagram APK on Uptodown...')
                    app_id = instagram_app_id()
                    say('  Public Instagram catalogue ID: ' + app_id)
                    file_id = instagram_version_file(app_id)
                    apk = work / (INSTAGRAM + '.apk')
                    client.download_apk(app_id, file_id, IG_SHA256, apk, 125 * 1024 * 1024)
                else:
                    say('Downloading F-Droid 1.23.2 from F-Droid...')
                    apk = get_fdroid(work)
                device.install(apk, package)
                status[package] = 'installed and confirmed for owner user 0'
                known.add(package)
            except Stop as exc:
                say(f'{package}: {exc}')
    return summary(status)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say('\nInterrupted. Installed apps were not removed.')
        sys.exit(130)
    except Stop as exc:
        say('STOP: ' + str(exc))
        sys.exit(1)
