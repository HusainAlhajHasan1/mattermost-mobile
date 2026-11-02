"""Hermetic tests: no real APKs, networks, or Android devices are accessed."""
import contextlib
import hashlib
import hmac
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from email.message import Message
from unittest import mock
from urllib import parse, request

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / 'scripts/tests/fixtures/p585-apps'

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

apps = load('uptodown_installer', ROOT / 'scripts/p585-uptodown-install.py')
oldtests = load('old_host_tests', ROOT / 'scripts/tests/test_p585_apps_install_host.py')
SERIAL = '5203c66b523ca357'
REAL_DOWNLOAD = apps.Uptodown.download_apk
REAL_IG_ID = apps.instagram_app_id
REAL_IG_FILE = apps.instagram_version_file
REAL_IG_CANDIDATE_CHECK = apps.check_instagram_candidate
REAL_FDROID = apps.get_fdroid


class UrlAndTrustTests(unittest.TestCase):
    def test_only_uptodown_https_hosts_are_accepted(self):
        for url in ('https://www.uptodown.app/eapi/a', 'https://dw.uptodown.com/dwn/file',
                    'https://stc.utdstc.com/x', 'https://dw.uptodown.net/dwn/file',
                    'https://DW.UPTODOWN.NET:443/dwn/file'):
            self.assertTrue(apps.allowed_uptodown(url), url)
        for url in ('http://uptodown.com/x', 'https://uptodown.com.evil.test/x',
                    'https://evil.test/uptodown.com/x', 'https://u@uptodown.com/x',
                    'https://uptodown.com:444/x', 'https://uptodown.com/#frag',
                    'file:///etc/passwd', 'https://uptodown.com:abc/x',
                    'http://dw.uptodown.net/dwn/file', 'https://uptodown.net/file',
                    'https://other.uptodown.net/file', 'https://sub.dw.uptodown.net/file',
                    'https://dw.uptodown.net.evil.test/file', 'https://u@dw.uptodown.net/file',
                    'https://dw.uptodown.net:444/file', 'https://dw.uptodown.net/file#fragment'):
            self.assertFalse(apps.allowed_uptodown(url), url)

    def test_untrusted_api_and_cdn_redirects_are_not_followed(self):
        req = request.Request('https://www.uptodown.app/eapi/a',
                              headers={'Authorization': 'Bearer secret'})
        handler = apps.RestrictedRedirect(True)
        for url in ('https://www.uptodown.app/eapi/b', 'https://evil.test/',
                    'https://www.uptodown.app.evil.test/eapi/a/',
                    'https://dw.uptodown.net/dwn/file',
                    'http://www.uptodown.app/eapi/a/',
                    'https://user@www.uptodown.app/eapi/a/'):
            with self.subTest(url=url), self.assertRaisesRegex(apps.Stop, 'redirect'):
                handler.redirect_request(req, None, 302, 'Found', {}, url)
        with self.assertRaisesRegex(apps.Stop, 'redirect'):
            apps.RestrictedRedirect(False).redirect_request(req, None, 302, 'Found', {},
                                                            'https://evil.test/')
        for target in ('https://dw2.uptodown.com/y', 'https://dw.uptodown.net/y'):
            self.assertIsNotNone(apps.RestrictedRedirect(False).redirect_request(
                request.Request('https://dw.uptodown.com/x'), None, 302, 'Found', {}, target))
            for header in ('Authorization', 'Cookie', 'Proxy-Authorization'):
                req = request.Request('https://dw.uptodown.com/x', headers={header: 'secret'})
                with self.subTest(header=header, target=target), self.assertRaisesRegex(apps.Stop, 'credentials'):
                    apps.RestrictedRedirect(False).redirect_request(req, None, 302, 'Found', {}, target)

    def test_api_canonical_redirect_preserves_only_safe_same_origin_request(self):
        handler = apps.RestrictedRedirect(True)
        req = request.Request('https://www.uptodown.app/eapi/auth/token?identifier=private',
                              data=b'identifier=fixture', method='POST',
                              headers={'Authorization': 'Bearer fixture', 'Cookie': 'secret',
                                       'Proxy-Authorization': 'proxy-secret'})
        for status in (301, 302, 307, 308):
            with self.subTest(status=status):
                redirected = handler.redirect_request(
                    req, None, status, 'Found', {},
                    'https://www.uptodown.app/eapi/auth/token/?identifier=private')
                self.assertEqual(redirected.get_method(), 'POST')
                self.assertEqual(redirected.data, b'identifier=fixture')
                headers = {k.lower(): v for k, v in redirected.header_items()}
                self.assertEqual(headers['authorization'], 'Bearer fixture')
                self.assertNotIn('cookie', headers)
                self.assertNotIn('proxy-authorization', headers)
        with self.assertRaises(apps.Stop):
            handler.redirect_request(req, None, 303, 'See other', {},
                                     'https://www.uptodown.app/eapi/auth/token/')

    def test_redirect_diagnostics_do_not_disclose_url_paths_tokens_or_ip(self):
        req = request.Request('https://www.uptodown.app/eapi/auth/token?identifier=private-id')
        redirect = 'https://user:password@evil.example/path-secret?bearer=secret#fragment'
        with self.assertRaises(apps.Stop) as caught:
            apps.RestrictedRedirect(True).redirect_request(req, None, 302, 'Found', {}, redirect)
        text = str(caught.exception)
        self.assertIn('authentication', text)
        self.assertIn('evil.example', text)
        for secret in ('private-id', 'user', 'password', 'path-secret', 'bearer=secret', 'fragment'):
            self.assertNotIn(secret, text)
        self.assertEqual(apps.safe_origin('https://192.168.1.23/x?token=secret'), 'https://(IP hidden)')
        self.assertEqual(apps.safe_origin('https://[::1]/x'), 'https://(IP hidden)')

    def test_real_urllib_opener_follows_same_api_path_without_changing_post(self):
        from urllib.response import addinfourl
        seen = []
        class FakeHTTPS(request.HTTPSHandler):
            def https_open(self, req):
                seen.append((req.get_method(), req.data, dict(req.header_items()), req.full_url))
                headers = Message()
                if len(seen) == 1:
                    headers['Location'] = '/eapi/auth/token/?identifier=fixture'
                    response = addinfourl(io.BytesIO(b''), headers, req.full_url, 308)
                    response.msg = 'Permanent redirect'
                    return response
                response = addinfourl(io.BytesIO(b'{"token":"a.b.c"}'), headers, req.full_url, 200)
                response.msg = 'OK'
                return response
        opener = request.build_opener(apps.RestrictedRedirect(True), FakeHTTPS())
        req = request.Request('https://www.uptodown.app/eapi/auth/token?identifier=fixture',
                              data=b'example=one', headers={'Authorization': 'Bearer fixture'}, method='POST')
        with opener.open(req, timeout=1) as response:
            self.assertEqual(response.read(), b'{"token":"a.b.c"}')
        self.assertEqual(len(seen), 2)
        self.assertEqual([s[0] for s in seen], ['POST', 'POST'])
        self.assertEqual(seen[1][1], b'example=one')
        self.assertEqual({k.lower(): v for k, v in seen[1][2].items()}['authorization'], 'Bearer fixture')

    def test_real_urllib_download_redirect_to_net_still_requires_pinned_hash(self):
        from urllib.response import addinfourl
        payload = b'synthetic APK bytes'
        digest = hashlib.sha256(payload).hexdigest()
        seen = []

        class FakeHTTPS(request.HTTPSHandler):
            def https_open(self, req):
                seen.append((req.full_url, dict(req.header_items())))
                headers = Message()
                if parse.urlsplit(req.full_url).hostname == 'dw.uptodown.com':
                    headers['Location'] = 'https://dw.uptodown.net/dwn/file?token=fixture'
                    response = addinfourl(io.BytesIO(b''), headers, req.full_url, 302)
                    response.msg = 'Found'
                    return response
                self.assert_target(req.full_url)
                response = addinfourl(io.BytesIO(payload), headers, req.full_url, 200)
                response.msg = 'OK'
                return response

            @staticmethod
            def assert_target(url):
                if parse.urlsplit(url).hostname != 'dw.uptodown.net':
                    raise AssertionError('Unexpected redirected host')

        client = apps.Uptodown()
        real_opener = request.build_opener
        with tempfile.TemporaryDirectory() as temp, \
             mock.patch.object(request, 'build_opener',
                               side_effect=lambda handler: real_opener(handler, FakeHTTPS())), \
             mock.patch.object(client, 'download_url', return_value='https://dw.uptodown.com/dwn/file'):
            apk = Path(temp) / 'store.apk'
            client.download_apk('1', '2', digest, apk, 1000)
            self.assertEqual(apk.read_bytes(), payload)
            with self.assertRaisesRegex(apps.Stop, 'SHA-256'):
                client.download_apk('1', '2', '0' * 64, Path(temp) / 'bad.apk', 1000)
        self.assertEqual([parse.urlsplit(url).hostname for url, _ in seen],
                         ['dw.uptodown.com', 'dw.uptodown.net'] * 2)
        for _url, headers in seen:
            self.assertTrue({'authorization', 'cookie', 'proxy-authorization'}.isdisjoint(
                key.lower() for key in headers))

    def test_authentication_posts_anonymous_hmac_and_uses_returned_bearer(self):
        calls = []
        def fake_json(url, headers=None, body=None, auth=False, require_success=True):
            calls.append((url, headers, body, auth, require_success))
            if '/auth/token?' in url:
                return {'token': 'a.b.c'}  # Real token response need not contain 'success'.
            return {'success': 1, 'data': {'downloadURL': 'https://dw.uptodown.com/dwn/z'}}
        with mock.patch.object(apps, 'json_response', side_effect=fake_json), \
             mock.patch.object(apps.time, 'time', return_value=1770000000), \
             mock.patch.object(apps.secrets, 'token_hex', return_value='1234567890abcdef'):
            url = apps.Uptodown().download_url('18098', '1232507681', apps.STORE_SHA256)
        self.assertEqual(url, 'https://dw.uptodown.com/dwn/z')
        auth_url, headers, body, authorized, require_success = calls[0]
        self.assertTrue(authorized)
        self.assertFalse(require_success)
        self.assertIn('identifier=1234567890abcdef', auth_url)
        fields = parse.parse_qs(body.decode())
        self.assertEqual(fields['identifier'], ['1234567890abcdef'])
        expected = hmac.new(apps.UPTODOWN_CLIENT_KEY.encode(), b'1770000000', hashlib.sha256).hexdigest()
        self.assertEqual(fields['hmac'], [expected])
        self.assertNotIn('Authorization', headers)
        self.assertEqual(calls[1][1]['Authorization'], 'Bearer a.b.c')
        self.assertTrue(calls[1][3])

    def test_token_response_without_success_is_allowed_only_for_auth(self):
        with mock.patch.object(apps, 'small_response', return_value=b'{"token":"a.b.c"}'):
            self.assertEqual(apps.json_response('https://www.uptodown.app', require_success=False),
                             {'token': 'a.b.c'})
            with self.assertRaises(apps.Stop):
                apps.json_response('https://www.uptodown.app')
        with mock.patch.object(apps, 'small_response', return_value=b'{"success":0,"token":"a.b.c"}'):
            with self.assertRaises(apps.Stop):
                apps.json_response('https://www.uptodown.app', require_success=False)

    def test_unsafe_url_and_server_hash_are_rejected(self):
        client = apps.Uptodown()
        client.token = 'a.b.c'
        data = {'success': 1, 'data': {'downloadURL': 'https://attacker.test/a'}}
        with mock.patch.object(client, 'authenticate'), mock.patch.object(apps, 'json_response', return_value=data):
            with self.assertRaisesRegex(apps.Stop, 'outside'):
                client.download_url('1', '2', apps.STORE_SHA256)
        data = {'success': 1, 'data': {'downloadURL': 'https://dw.uptodown.com/a', 'sha256': 'bad'}}
        with mock.patch.object(client, 'authenticate'), mock.patch.object(apps, 'json_response', return_value=data):
            with self.assertRaisesRegex(apps.Stop, 'different APK hash'):
                client.download_url('1', '2', apps.STORE_SHA256)
        for app_id in ('', '../etc', '1?x=evil', '9' * 20):
            with self.assertRaises(apps.Stop):
                client.download_url(app_id, '2', apps.STORE_SHA256)

    def test_pinned_apk_hash_and_size_are_enforced(self):
        payload = b'synthetic-not-an-apk'
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            client = apps.Uptodown()
            with mock.patch.object(client, 'download_url', return_value='https://dw.uptodown.com/z'), \
                 mock.patch.object(apps, 'open_uptodown', side_effect=lambda *a, **k: io.BytesIO(payload)):
                client.download_apk('1', '2', digest, work / 'good.apk', 1000)
                self.assertEqual((work / 'good.apk').read_bytes(), payload)
                with self.assertRaisesRegex(apps.Stop, 'SHA-256'):
                    client.download_apk('1', '2', '0' * 64, work / 'bad.apk', 1000)
                with self.assertRaisesRegex(apps.Stop, 'large'):
                    client.download_apk('1', '2', digest, work / 'big.apk', 5)

    def test_metadata_byte_and_time_limits(self):
        with mock.patch.object(apps, 'open_uptodown', return_value=io.BytesIO(b'123456')):
            with self.assertRaisesRegex(apps.Stop, 'large'):
                apps.small_response('https://www.uptodown.app', limit=5)
        with mock.patch.object(apps.time, 'monotonic', side_effect=[0, 61]):
            with self.assertRaisesRegex(apps.Stop, 'too long'):
                list(apps.response_chunks(io.BytesIO(b'hello'), 100, 60))

    def test_child_process_download_file_size_is_hard_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'large'
            command = [sys.executable, '-c',
                       'import sys; open(sys.argv[1], "wb").write(b"x" * 8192)', str(path)]
            with self.assertRaises(apps.Stop):
                apps.run(command, file_limit=1024)
            self.assertLessEqual(path.stat().st_size, 1024)

    @unittest.skipUnless(shutil.which('gpgv'), 'gpgv required')
    def test_actual_gpgv_checks_signed_fixture_wrong_bytes_and_wrong_signer(self):
        payload = (FIXTURES / 'payload.bin').read_bytes()
        key = (FIXTURES / 'public-key.gpg').read_bytes()
        sig = (FIXTURES / 'payload.asc').read_text()
        def fake_curl(_url, path, github=False):
            path.write_bytes(key if github else payload)
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            with mock.patch.object(apps, 'curl_download', side_effect=fake_curl), \
                 mock.patch.object(apps, 'KEY_HASH', hashlib.sha256(key).hexdigest()), \
                 mock.patch.object(apps, 'FDROID_SIGNATURE', sig), \
                 mock.patch.object(apps, 'SIGNING_FPR', 'CB2A5997CECF1B21FF6EA21C5C993FA25CFA668B'), \
                 mock.patch.object(apps, 'PRIMARY_FPR', '14599C9E06FDF94FCE010518B30784814F027256'):
                self.assertEqual(apps.get_fdroid(work).read_bytes(), payload)
                payload = b'tampered'
                with self.assertRaisesRegex(apps.Stop, 'signature'):
                    apps.get_fdroid(work)
                payload = (FIXTURES / 'payload.bin').read_bytes()
                with mock.patch.object(apps, 'SIGNING_FPR', 'BAD'):
                    with self.assertRaisesRegex(apps.Stop, 'signature'):
                        apps.get_fdroid(work)
                with mock.patch.object(apps, 'KEY_HASH', 'bad'):
                    with self.assertRaisesRegex(apps.Stop, 'checksum'):
                        apps.get_fdroid(work)


class InstagramLookupTests(unittest.TestCase):
    def test_app_id_is_parsed_unambiguously(self):
        html = (b'<h1 id="detail-app-name" data-code="12345">Instagram</h1>'
                b'<button id="detail-download-button" data-app-id="12345" data-code="999"></button>')
        with mock.patch.object(apps, 'small_response', return_value=html):
            self.assertEqual(apps.instagram_app_id(), '12345')
        with mock.patch.object(apps, 'small_response', return_value=html.replace(b'data-app-id="12345"', b'data-app-id="999"')):
            with self.assertRaisesRegex(apps.Stop, 'uniquely'):
                apps.instagram_app_id()
        with mock.patch.object(apps, 'small_response', return_value=b'<html>challenge</html>'):
            with self.assertRaises(apps.Stop):
                apps.instagram_app_id()

    @staticmethod
    def pages_with_target(number):
        if number < 5:
            return [{'version': '400.0.0', 'fileID': number, 'kindFile': 'apk'}]
        if number == 5:
            return [{'version': apps.IG_VERSION, 'fileID': 11, 'kindFile': 'xapk'},
                    {'version': apps.IG_VERSION, 'fileID': 12, 'kindFile': 'apk'},
                    {'version': apps.IG_VERSION, 'fileID': 13, 'kindFile': 'apk'}]
        if number == 6:
            return [{'version': '377.0.0', 'fileID': 99, 'kindFile': 'apk'}]
        return []

    def test_finds_only_exact_pinned_apk_with_bounded_lookup(self):
        pages, files = [], []
        def fake_json(url):
            number = int(url.rsplit('/', 1)[1])
            pages.append(number)
            return {'success': 1, 'data': self.pages_with_target(number)}
        def fake_small(url):
            files.append(url)
            return (b'<td>SHA256</td>' + apps.IG_SHA256.encode()) if '/13-x' in url else b'wrong hash'
        with mock.patch.object(apps, 'json_response', side_effect=fake_json), \
             mock.patch.object(apps, 'small_response', side_effect=fake_small):
            self.assertEqual(apps.instagram_version_file('123'), '13')
        self.assertLessEqual(len(pages), 16)
        self.assertEqual(len(files), 2)  # no XAPK, no other versions
        self.assertFalse(any('/11-x' in x for x in files))

    def test_different_hash_and_changed_layout_fail_closed(self):
        def fake_json(url):
            return {'success': 1, 'data': self.pages_with_target(int(url.rsplit('/', 1)[1]))}
        with mock.patch.object(apps, 'json_response', side_effect=fake_json), \
             mock.patch.object(apps, 'small_response', return_value=b'wrong hash'):
            with self.assertRaisesRegex(apps.Stop, 'not found'):
                apps.instagram_version_file('123')
        with mock.patch.object(apps, 'json_response', return_value={'data': 'not a list'}):
            with self.assertRaisesRegex(apps.Stop, 'layout'):
                apps.instagram_version_file('123')

    def test_missing_data_on_final_page_is_treated_as_end(self):
        def fake_json(url):
            number = int(url.rsplit('/', 1)[1])
            rows = self.pages_with_target(number)
            return {'success': 1, 'data': rows} if rows else {'success': 1}
        with mock.patch.object(apps, 'json_response', side_effect=fake_json), \
             mock.patch.object(apps, 'small_response', return_value=apps.IG_SHA256.encode()):
            self.assertEqual(apps.instagram_version_file('123'), '12')

    def test_unparseable_or_very_deep_archive_fails_closed(self):
        with mock.patch.object(apps, 'json_response', return_value={'data': [{'version': '???'}]}):
            with self.assertRaisesRegex(apps.Stop, 'parse'):
                apps.instagram_version_file('123')
        with mock.patch.object(apps, 'json_response', return_value={'data': [{'version': '500.0'}]}) as calls:
            with self.assertRaisesRegex(apps.Stop, 'bounded'):
                apps.instagram_version_file('123')
            self.assertLessEqual(calls.call_count, 10)


class InstagramCandidateTests(unittest.TestCase):
    def test_exact_candidate_skips_fallback_and_uses_pinned_id(self):
        payload = b'fake standalone APK'
        client = mock.Mock()
        client.download_apk.side_effect = lambda _app, _file, _hash, dest, _limit: dest.write_bytes(payload)
        with tempfile.TemporaryDirectory() as temp, \
             mock.patch.object(apps, 'IG_CANDIDATE_SIZE', len(payload)), \
             mock.patch.object(apps, 'IG_CANDIDATE_SHA256', hashlib.sha256(payload).hexdigest()), \
             mock.patch.object(apps, 'instagram_app_id') as id_lookup, \
             mock.patch.object(apps, 'instagram_version_file') as file_lookup:
            result = apps.get_instagram(client, Path(temp))
            self.assertEqual(result.read_bytes(), payload)
            args = client.download_apk.call_args.args
            self.assertEqual(args[:3], (apps.IG_APP_ID, apps.IG_CANDIDATE_FILE_ID,
                                        hashlib.sha256(payload).hexdigest()))
            self.assertEqual(args[4], 160 * 1024 * 1024)
            self.assertEqual(client.download_apk.call_count, 1)
            id_lookup.assert_not_called()
            file_lookup.assert_not_called()

    def test_exact_independent_sha256_and_size_are_both_required(self):
        payload = b'fake Instagram APK bytes'
        with tempfile.TemporaryDirectory() as temp:
            apk = Path(temp) / 'candidate.apk'
            apk.write_bytes(payload)
            with mock.patch.object(apps, 'IG_CANDIDATE_SIZE', len(payload)), \
                 mock.patch.object(apps, 'IG_CANDIDATE_SHA256', hashlib.sha256(payload).hexdigest()):
                apps.check_instagram_candidate(apk)
                with mock.patch.object(apps, 'IG_CANDIDATE_SIZE', len(payload) + 1):
                    with self.assertRaisesRegex(apps.Stop, 'not installing'):
                        apps.check_instagram_candidate(apk)
                apk.write_bytes(b'X' + payload[1:])  # same size, different bytes
                with self.assertRaisesRegex(apps.Stop, 'not installing'):
                    apps.check_instagram_candidate(apk)
            apk.unlink()
            with self.assertRaisesRegex(apps.Stop, 'not installing'):
                apps.check_instagram_candidate(apk)

    def test_candidate_failure_falls_back_only_to_separately_pinned_apk(self):
        calls = []
        def download(app_id, file_id, expected, path, limit):
            calls.append((app_id, file_id, expected, limit))
            path.write_bytes(b'wrong candidate' if file_id == apps.IG_CANDIDATE_FILE_ID else b'old pin')
        client = mock.Mock()
        client.download_apk.side_effect = download
        with tempfile.TemporaryDirectory() as temp, \
             mock.patch.object(apps, 'instagram_app_id', return_value=apps.IG_APP_ID), \
             mock.patch.object(apps, 'instagram_version_file', return_value='123'):
            result = apps.get_instagram(client, Path(temp))
            self.assertEqual(result.read_bytes(), b'old pin')
        self.assertEqual(calls[0][:3], (apps.IG_APP_ID, apps.IG_CANDIDATE_FILE_ID,
                                      apps.IG_CANDIDATE_SHA256))
        self.assertEqual(calls[1][:3], (apps.IG_APP_ID, '123', apps.IG_SHA256))
        self.assertLessEqual(calls[0][3], 160 * 1024 * 1024)

    def test_fallback_refuses_changed_catalogue_id(self):
        client = mock.Mock()
        client.download_apk.side_effect = apps.Stop('unavailable')
        with tempfile.TemporaryDirectory() as temp, \
             mock.patch.object(apps, 'instagram_app_id', return_value='999'), \
             mock.patch.object(apps, 'instagram_version_file') as lookup:
            with self.assertRaisesRegex(apps.Stop, 'catalogue ID changed'):
                apps.get_instagram(client, Path(temp))
            lookup.assert_not_called()
            self.assertEqual(client.download_apk.call_count, 1)


class DeviceUnitTests(unittest.TestCase):
    def test_wrong_device_and_remote_marker_are_rejected(self):
        for serial in ('', 'wrong', 'bad serial'):
            with self.assertRaises(apps.Stop):
                apps.Device(serial)
        dev = apps.Device(SERIAL)
        with mock.patch.object(dev, 'text', side_effect=['device', 'OTHER', 'gtanotexllte',
                                                        'P585JXS3CTL1', '27', 'armeabi-v7a', '1']):
            with self.assertRaisesRegex(apps.Stop, 'does not match'):
                dev.preflight()
        with mock.patch.object(dev, 'text', return_value='not a marker'):
            with self.assertRaisesRegex(apps.Stop, 'did not confirm'):
                dev.marked('true')

    def test_retained_data_and_false_adb_success_are_preserved(self):
        dev = apps.Device(SERIAL)
        with mock.patch.object(dev, 'packages', return_value={apps.STORE}), \
             mock.patch.object(dev, 'run') as run:
            with self.assertRaisesRegex(apps.Stop, 'left untouched'):
                dev.install(Path('/tmp/unused.apk'), apps.STORE)
            run.assert_not_called()
        result = subprocess.CompletedProcess([], 0, b'Success\n', b'')
        with mock.patch.object(dev, 'packages', side_effect=[set(), set()]), \
             mock.patch.object(dev, 'run', return_value=result) as run:
            with self.assertRaisesRegex(apps.Stop, 'not installed'):
                dev.install(Path('/tmp/unused.apk'), apps.STORE)
            self.assertEqual(run.call_args.args[:3], ('install', '--user', '0'))
            self.assertNotIn('-r', run.call_args.args)


class HostFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        adb = bin_dir / 'adb'
        adb.write_text(oldtests.FAKE_ADB)
        adb.chmod(0o755)
        self.state = self.root / 'state.json'
        self.save(packages=[apps.FDROID, 'com.aurora.store'])
        env = {'PATH': str(bin_dir) + os.pathsep + os.environ.get('PATH', ''),
               'FAKE_STATE': str(self.state), 'HOME': str(self.root), 'P585_SERIAL': SERIAL}
        self.patches = [mock.patch.dict(os.environ, env),
                        mock.patch.object(apps, 'instagram_app_id', return_value=apps.IG_APP_ID),
                        mock.patch.object(apps, 'instagram_version_file', return_value='456'),
                        mock.patch.object(apps, 'check_instagram_candidate'),
                        mock.patch.object(apps.Uptodown, 'download_apk', side_effect=self.fake_download),
                        mock.patch.object(apps, 'get_fdroid', side_effect=self.fake_fdroid)]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.temp.cleanup()

    def save(self, packages, retained=None, **extra):
        state = {'packages': packages, 'retained': retained or [], 'ui': 'welcome',
                 'taps': [], 'mode': 'default', 'modes': []}
        state.update(extra)
        self.state.write_text(json.dumps(state))

    @staticmethod
    def fake_download(_app_id, _file_id, _hash, dest, _limit):
        dest.write_bytes(b'verified-only-in-fixture')

    @staticmethod
    def fake_fdroid(work):
        path = work / (apps.FDROID + '.apk')
        path.write_bytes(b'fdroid-fixture')
        return path

    def run_main(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = apps.main()
        return result, out.getvalue(), json.loads(self.state.read_text())

    def test_fresh_store_and_instagram_preserve_fdroid_and_aurora(self):
        result, output, state = self.run_main()
        self.assertEqual(result, 0, output)
        self.assertEqual(set(state['packages']), {apps.FDROID, 'com.aurora.store', apps.STORE, apps.INSTAGRAM})
        self.assertEqual(state['taps'], [])
        self.assertEqual(state['modes'], [])
        self.assertIn('Uptodown: installed and confirmed', output)
        self.assertIn('Instagram: installed and confirmed', output)
        self.assertIn('F-Droid: already installed', output)

    @unittest.skipUnless(shutil.which('gpgv'), 'gpgv required')
    def test_complete_mock_network_and_adb_path_with_real_checks(self):
        """Exercise protocol, bounded reads, SHA, GPG, package checks together."""
        self.save(packages=['com.aurora.store'])
        store, instagram = b'synthetic store fixture', b'synthetic instagram fixture'
        key = (FIXTURES / 'public-key.gpg').read_bytes()
        payload = (FIXTURES / 'payload.bin').read_bytes()
        sig = (FIXTURES / 'payload.asc').read_text()
        ig_hash = hashlib.sha256(instagram).hexdigest()
        requests = []
        def network(url, headers=None, body=None, auth=False):
            requests.append((url, auth, headers or {}))
            path = parse.urlsplit(url).path
            if path == '/eapi/auth/token':
                data = b'{"token":"a.b.c"}'
            elif path.startswith('/eapi/apps/') and path.endswith('/downloadUrl'):
                file_id = path.split('/')[-2]
                data = json.dumps({'success': 1, 'data': {'downloadURL':
                                  'https://dw.uptodown.com/dwn/' + file_id}}).encode()
            elif path == '/dwn/' + apps.STORE_FILE_ID:
                data = store
            elif path in ('/dwn/13', '/dwn/' + apps.IG_CANDIDATE_FILE_ID):
                data = instagram
            elif path == '/android/versions':
                data = b'<h1 id="detail-app-name" data-code="123">Instagram</h1>'
            elif '/apps/123/versions/' in path:
                number = int(path.rsplit('/', 1)[1])
                rows = InstagramLookupTests.pages_with_target(number)
                data = json.dumps({'success': 1, 'data': rows} if rows else {'success': 1}).encode()
            elif path == '/android/download/12-x':
                data = b'wrong hash'
            elif path == '/android/download/13-x':
                data = ('<td>SHA256</td><td>' + ig_hash + '</td>').encode()
            else:
                raise AssertionError('Unexpected URL: ' + url)
            return io.BytesIO(data)
        def curl_fixture(_url, path, github=False):
            path.write_bytes(key if github else payload)
        with mock.patch.object(apps.Uptodown, 'download_apk', REAL_DOWNLOAD), \
             mock.patch.object(apps, 'instagram_app_id', REAL_IG_ID), \
             mock.patch.object(apps, 'instagram_version_file', REAL_IG_FILE), \
             mock.patch.object(apps, 'check_instagram_candidate', REAL_IG_CANDIDATE_CHECK), \
             mock.patch.object(apps, 'get_fdroid', REAL_FDROID), \
             mock.patch.object(apps, 'open_uptodown', side_effect=network), \
             mock.patch.object(apps, 'curl_download', side_effect=curl_fixture), \
             mock.patch.object(apps, 'STORE_SHA256', hashlib.sha256(store).hexdigest()), \
             mock.patch.object(apps, 'IG_CANDIDATE_SHA256', ig_hash), \
             mock.patch.object(apps, 'IG_CANDIDATE_SIZE', len(instagram)), \
             mock.patch.object(apps, 'IG_SHA256', ig_hash), \
             mock.patch.object(apps, 'KEY_HASH', hashlib.sha256(key).hexdigest()), \
             mock.patch.object(apps, 'FDROID_SIGNATURE', sig), \
             mock.patch.object(apps, 'SIGNING_FPR', 'CB2A5997CECF1B21FF6EA21C5C993FA25CFA668B'), \
             mock.patch.object(apps, 'PRIMARY_FPR', '14599C9E06FDF94FCE010518B30784814F027256'):
            result, output, state = self.run_main()
        self.assertEqual(result, 0, output)
        self.assertEqual(set(state['packages']), {apps.FDROID, apps.STORE, apps.INSTAGRAM, 'com.aurora.store'})
        self.assertIn('Official F-Droid signature verified', output)
        self.assertIn('Independently published APKMirror SHA-256 and size matched.', output)
        self.assertEqual(output.count('Pinned SHA-256 matched.'), 2)
        self.assertEqual(sum('/eapi/auth/token' in x[0] for x in requests), 2)
        self.assertTrue(all('Authorization' not in headers for url, _auth, headers in requests
                            if '/dwn/' in url))

    def test_partial_result_when_instagram_not_available_still_installs_fdroid_and_store(self):
        self.save(packages=['com.aurora.store'])
        with mock.patch.object(apps, 'check_instagram_candidate', side_effect=apps.Stop('bad candidate')), \
             mock.patch.object(apps, 'instagram_version_file', side_effect=apps.Stop('no pinned version')):
            result, output, state = self.run_main()
        self.assertEqual(result, 1)
        self.assertEqual(set(state['packages']), {apps.FDROID, 'com.aurora.store', apps.STORE})
        self.assertIn('no pinned version', output)
        self.assertIn('Instagram: not confirmed installed', output)
        self.assertIn('F-Droid: installed and confirmed', output)

    def test_mismatched_candidate_and_absent_fallback_never_install_instagram(self):
        with mock.patch.object(apps, 'check_instagram_candidate', REAL_IG_CANDIDATE_CHECK), \
             mock.patch.object(apps, 'IG_CANDIDATE_SIZE', len(b'verified-only-in-fixture')), \
             mock.patch.object(apps, 'IG_CANDIDATE_SHA256', '0' * 64), \
             mock.patch.object(apps, 'instagram_version_file', side_effect=apps.Stop('fallback unavailable')):
            result, output, state = self.run_main()
        self.assertEqual(result, 1)
        self.assertNotIn(apps.INSTAGRAM, state['packages'])
        self.assertIn('candidate was not used', output)
        self.assertIn('fallback unavailable', output)
        self.assertIn('Instagram: not confirmed installed', output)
        self.assertIn(apps.FDROID, state['packages'])
        self.assertIn('com.aurora.store', state['packages'])

    def test_existing_and_retained_packages_are_not_touched(self):
        self.save(packages=[apps.FDROID, 'com.aurora.store'], retained=[apps.STORE, apps.INSTAGRAM])
        with mock.patch.object(apps.Uptodown, 'download_apk') as download:
            result, output, state = self.run_main()
        self.assertEqual(result, 1)
        self.assertEqual(set(state['packages']), {apps.FDROID, 'com.aurora.store'})
        self.assertIn('retained package/data', output)
        download.assert_not_called()

    def test_all_already_installed_needs_no_network(self):
        self.save(packages=[apps.FDROID, apps.STORE, apps.INSTAGRAM, 'com.aurora.store'])
        with mock.patch.object(apps.Uptodown, 'download_apk') as download:
            result, output, state = self.run_main()
        self.assertEqual(result, 0)
        self.assertIn('existing APK not checked', output)
        download.assert_not_called()
        self.assertEqual(len(state['packages']), 4)

    def test_false_adb_success_is_not_reported_as_fdroid_installed(self):
        self.save(packages=[], false_success=True)
        result, output, state = self.run_main()
        self.assertEqual(result, 1)
        self.assertNotIn(apps.FDROID, state['packages'])
        self.assertIn('ADB reported success', output)
        self.assertIn('F-Droid: not confirmed installed', output)

    def test_bad_store_hash_does_not_prevent_other_safe_attempts(self):
        self.save(packages=[])
        def fail_store(app_id, _file_id, _hash, dest, _limit):
            if app_id == apps.STORE_APP_ID:
                raise apps.Stop('APK SHA-256 did not match')
            dest.write_bytes(b'fixture')
        with mock.patch.object(apps.Uptodown, 'download_apk', side_effect=fail_store):
            result, output, state = self.run_main()
        self.assertEqual(result, 1)
        self.assertNotIn(apps.STORE, state['packages'])
        self.assertIn(apps.INSTAGRAM, state['packages'])
        self.assertIn(apps.FDROID, state['packages'])
        self.assertIn('SHA-256 did not match', output)


if __name__ == '__main__':
    unittest.main()
