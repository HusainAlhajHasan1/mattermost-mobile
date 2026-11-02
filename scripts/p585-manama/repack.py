#!/usr/bin/env python3
"""Re-sign Appium Settings 5.7.5 with a minimal, ADB-only manifest.

The original dex, resources, and non-signature metadata are unchanged.
A new ephemeral, self-signed APK v1 certificate is created on the user's host.
Appium Settings is Apache-2.0: https://github.com/appium/io.appium.settings
See AndroidManifest.xml and README.md in this directory for modifications.
"""
import base64
import hashlib
import os
import pathlib
import subprocess
import sys
import tempfile
import zipfile

UPSTREAM_SHA256 = 'cfe14349395d7b22aa9cb2d0e4f32618aa4b5d02bb823a35a11da6d66fe4467a'
NOTICE_SHA256 = 'e5fbe88a4b1367dfff7908d4a3f9b3c551c1d882819f43df343d2e7ca5a56fa5'
LICENSE_SHA256 = 'ae9194687e7f3b7d85e8eeb5c973e22d52c4653b1e4332fa0e5c6e82b038ab29'
MANIFEST_SHA256 = '33e3355a6622c92f53087bdcf5fc14ede31bc1c66dfa33ddf2b995f1c4554914'
# Binary AXML produced by aapt2 from the adjacent AndroidManifest.xml using API 27.
# It refers only to framework resources and does not replace resources.arsc.
MANIFEST_B64 = '''
AwAIAFgJAAABABwAUAUAACAAAAAAAAAAAAAAAJwAAAAAAAAAAAAAAA4AAAAcAAAAKAAAAEAAAABYAAAAbAAAAHoAAACYAAAAsgAA
AMwAAADwAAAACgEAACYBAABSAQAAZgEAAHgBAADOAQAAIAIAAHICAACkAgAAvgIAACgDAABoAwAAwAMAAOgDAAAwBAAARAQAAFoE
AABsBAAAfgQAAKAEAAAFAHQAaABlAG0AZQAAAAUAbABhAGIAZQBsAAAABABuAGEAbQBlAAAACgBwAGUAcgBtAGkAcwBzAGkAbwBu
AAAACgBkAGUAYgB1AGcAZwBhAGIAbABlAAAACABlAHgAcABvAHIAdABlAGQAAAAFAHYAYQBsAHUAZQAAAA0AbQBpAG4AUwBkAGsA
VgBlAHIAcwBpAG8AbgAAAAsAdgBlAHIAcwBpAG8AbgBDAG8AZABlAAAACwB2AGUAcgBzAGkAbwBuAE4AYQBtAGUAAAAQAHQAYQBy
AGcAZQB0AFMAZABrAFYAZQByAHMAaQBvAG4AAAALAGEAbABsAG8AdwBCAGEAYwBrAHUAcAAAAAwANQAuADcALgA1AC0AbQBhAG4A
YQBtAGEAAAAUAE0AYQBuAGEAbQBhACAAbQBvAGMAawAgAGwAbwBjAGEAdABpAG8AbgAAAAgAYQBjAHQAaQB2AGkAdAB5AAAABwBh
AG4AZAByAG8AaQBkAAAAKQBhAG4AZAByAG8AaQBkAC4AcABlAHIAbQBpAHMAcwBpAG8AbgAuAEEAQwBDAEUAUwBTAF8AQwBPAEEA
UgBTAEUAXwBMAE8AQwBBAFQASQBPAE4AAAAnAGEAbgBkAHIAbwBpAGQALgBwAGUAcgBtAGkAcwBzAGkAbwBuAC4AQQBDAEMARQBT
AFMAXwBGAEkATgBFAF8ATABPAEMAQQBUAEkATwBOAAAAJwBhAG4AZAByAG8AaQBkAC4AcABlAHIAbQBpAHMAcwBpAG8AbgAuAEEA
QwBDAEUAUwBTAF8ATQBPAEMASwBfAEwATwBDAEEAVABJAE8ATgAAABcAYQBuAGQAcgBvAGkAZAAuAHAAZQByAG0AaQBzAHMAaQBv
AG4ALgBEAFUATQBQAAAACwBhAHAAcABsAGkAYwBhAHQAaQBvAG4AAAAzAGMAbwBtAC4AZwBvAG8AZwBsAGUALgBhAG4AZAByAG8A
aQBkAC4AZwBtAHMALgBjAG8AbQBtAG8AbgAuAGEAcABpAC4ARwBvAG8AZwBsAGUAQQBwAGkAQQBjAHQAaQB2AGkAdAB5AAAAHgBj
AG8AbQAuAGcAbwBvAGcAbABlAC4AYQBuAGQAcgBvAGkAZAAuAGcAbQBzAC4AdgBlAHIAcwBpAG8AbgAAACoAaAB0AHQAcAA6AC8A
LwBzAGMAaABlAG0AYQBzAC4AYQBuAGQAcgBvAGkAZAAuAGMAbwBtAC8AYQBwAGsALwByAGUAcwAvAGEAbgBkAHIAbwBpAGQAAAAS
AGkAbwAuAGEAcABwAGkAdQBtAC4AcwBlAHQAdABpAG4AZwBzAAAAIgBpAG8ALgBhAHAAcABpAHUAbQAuAHMAZQB0AHQAaQBuAGcA
cwAuAEwAbwBjAGEAdABpAG8AbgBTAGUAcgB2AGkAYwBlAAAACABtAGEAbgBpAGYAZQBzAHQAAAAJAG0AZQB0AGEALQBkAGEAdABh
AAAABwBwAGEAYwBrAGEAZwBlAAAABwBzAGUAcgB2AGkAYwBlAAAADwB1AHMAZQBzAC0AcABlAHIAbQBpAHMAcwBpAG8AbgAAAAgA
dQBzAGUAcwAtAHMAZABrAAAAgAEIADgAAAAAAAEBAQABAQMAAQEGAAEBDwABARAAAQEkAAEBDAIBARsCAQEcAgEBcAIBAYACAQEA
ARAAGAAAAAIAAAD/////DwAAABcAAAACARAAYAAAAAIAAAD//////////xoAAAAUABQAAwAAAAAAAAAXAAAACAAAAP////8IAAAQ
SwAAABcAAAAJAAAADAAAAAgAAAMMAAAA/////xwAAAAYAAAACAAAAxgAAAACARAATAAAAAQAAAD//////////x8AAAAUABQAAgAA
AAAAAAAXAAAABwAAAP////8IAAAQFQAAABcAAAAKAAAA/////wgAABAbAAAAAwEQABgAAAAEAAAA//////////8fAAAAAgEQADgA
AAAFAAAA//////////8eAAAAFAAUAAEAAAAAAAAAFwAAAAIAAAARAAAACAAAAxEAAAADARAAGAAAAAUAAAD//////////x4AAAAC
ARAAOAAAAAYAAAD//////////x4AAAAUABQAAQAAAAAAAAAXAAAAAgAAABAAAAAIAAADEAAAAAMBEAAYAAAABgAAAP//////////
HgAAAAIBEAA4AAAABwAAAP//////////HgAAABQAFAABAAAAAAAAABcAAAACAAAAEgAAAAgAAAMSAAAAAwEQABgAAAAHAAAA////
//////8eAAAAAgEQAGAAAAAIAAAA//////////8UAAAAFAAUAAMAAAAAAAAAFwAAAAEAAAANAAAACAAAAw0AAAAXAAAABAAAAP//
//8IAAASAAAAABcAAAALAAAA/////wgAABIAAAAAAgEQAGAAAAAJAAAA//////////8dAAAAFAAUAAMAAAAAAAAAFwAAAAIAAAAZ
AAAACAAAAxkAAAAXAAAAAwAAABMAAAAIAAADEwAAABcAAAAFAAAA/////wgAABL/////AwEQABgAAAAJAAAA//////////8dAAAA
AgEQAGAAAAALAAAA//////////8OAAAAFAAUAAMAAAAAAAAAFwAAAAAAAAD/////CAAAARAAAwEXAAAAAgAAABUAAAAIAAADFQAA
ABcAAAAFAAAA/////wgAABIAAAAAAwEQABgAAAALAAAA//////////8OAAAAAgEQAEwAAAANAAAA//////////8bAAAAFAAUAAIA
AAAAAAAAFwAAAAIAAAAWAAAACAAAAxYAAAAXAAAABgAAAP////8IAAAQuPy9AAMBEAAYAAAADQAAAP//////////GwAAAAMBEAAY
AAAACAAAAP//////////FAAAAAMBEAAYAAAAAgAAAP//////////GgAAAAEBEAAYAAAAAgAAAP////8PAAAAFwAAAA=='''


def digest(data):
    return base64.b64encode(hashlib.sha256(data).digest()).decode('ascii')


def header(name, value):
    """JAR manifest: CRLF and continuation lines at no more than 72 bytes."""
    payload = (name + ': ' + value).encode('utf-8')
    result, payload = payload[:72] + b'\r\n', payload[72:]
    while payload:
        result += b' ' + payload[:71] + b'\r\n'
        payload = payload[71:]
    return result


def is_signature(name):
    upper = name.upper()
    if upper == 'META-INF/MANIFEST.MF':
        return True
    prefix = 'META-INF/'
    return (upper.startswith(prefix) and '/' not in upper[len(prefix):]
            and upper.endswith(('.SF', '.RSA', '.DSA', '.EC')))


def repack(source, output, notice, license_file):
    if hashlib.sha256(source.read_bytes()).hexdigest() != UPSTREAM_SHA256:
        raise ValueError('Upstream Appium APK checksum mismatch')
    notice_data, license_data = notice.read_bytes(), license_file.read_bytes()
    if hashlib.sha256(notice_data).hexdigest() != NOTICE_SHA256:
        raise ValueError('Appium NOTICE checksum mismatch')
    if hashlib.sha256(license_data).hexdigest() != LICENSE_SHA256:
        raise ValueError('Appium LICENSE checksum mismatch')
    manifest = base64.b64decode(MANIFEST_B64, validate=False)
    if hashlib.sha256(manifest).hexdigest() != MANIFEST_SHA256:
        raise ValueError('Compiled manifest checksum mismatch')
    if output.exists():
        raise ValueError('Refusing to overwrite the output APK')
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='.manama-sign-', dir=output.parent) as tmp:
            work = pathlib.Path(tmp)
            sf_path, rsa_path = work / 'cert.sf', work / 'cert.rsa'
            key_path, crt_path = work / 'key.pem', work / 'cert.pem'
            with zipfile.ZipFile(source) as inp, zipfile.ZipFile(output, 'w') as out:
                entries = []
                names = set()
                for item in inp.infolist():
                    name = item.filename
                    if name in names:
                        raise ValueError('Duplicate APK entry')
                    names.add(name)
                    if item.is_dir() or is_signature(name):
                        continue
                    data = manifest if name == 'AndroidManifest.xml' else inp.read(item)
                    entries.append((name, data))
                    out.writestr(item, data)
                if 'AndroidManifest.xml' not in names or 'classes.dex' not in names:
                    raise ValueError('The upstream APK is incomplete')
                # Retain upstream attribution and license with this derivative.
                for name, data in (('assets/manama-notices/NOTICE-Appium.txt', notice_data),
                                   ('assets/manama-notices/LICENSE-Appium.txt', license_data)):
                    if name in names:
                        raise ValueError('Notice path already exists in upstream APK')
                    entries.append((name, data))
                    out.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
                mf = header('Manifest-Version', '1.0') + header('Created-By', 'Manama helper') + b'\r\n'
                sections = []
                for name, data in entries:
                    section = header('Name', name) + header('SHA-256-Digest', digest(data)) + b'\r\n'
                    sections.append((name, section))
                    mf += section
                sf = (header('Signature-Version', '1.0') + header('Created-By', 'Manama helper')
                      + header('SHA-256-Digest-Manifest', digest(mf)) + b'\r\n')
                for name, section in sections:
                    sf += header('Name', name) + header('SHA-256-Digest', digest(section)) + b'\r\n'
                out.writestr('META-INF/MANIFEST.MF', mf, compress_type=zipfile.ZIP_DEFLATED)
                out.writestr('META-INF/CERT.SF', sf, compress_type=zipfile.ZIP_DEFLATED)
                sf_path.write_bytes(sf)
            # Generated locally, never uploaded. The key is deleted after signing.
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-sha256',
                            '-keyout', str(key_path), '-out', str(crt_path), '-nodes',
                            '-days', '3650', '-subj', '/CN=Manama Mock Location/'],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            subprocess.run(['openssl', 'smime', '-sign', '-binary', '-noattr', '-md', 'sha256',
                            '-in', str(sf_path), '-signer', str(crt_path), '-inkey', str(key_path),
                            '-outform', 'DER', '-out', str(rsa_path)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            with zipfile.ZipFile(output, 'a') as out:
                out.writestr('META-INF/CERT.RSA', rsa_path.read_bytes(),
                             compress_type=zipfile.ZIP_DEFLATED)
    except BaseException:
        output.unlink(missing_ok=True)
        raise


def main():
    if len(sys.argv) != 5:
        raise SystemExit('Usage: repack.py upstream.apk output.apk NOTICE LICENSE')
    os.umask(0o077)
    try:
        repack(*(pathlib.Path(arg) for arg in sys.argv[1:]))
    except (OSError, ValueError, zipfile.BadZipFile, subprocess.CalledProcessError) as exc:
        print(f'Cannot prepare the helper APK: {exc}', file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == '__main__':
    main()
