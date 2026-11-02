#!/usr/bin/env python3
"""Prepare one explicitly pinned ENCMA source tree for an experimental API-27 build.

This does not touch a device, alter the checked-out project, override any library
manifest checks, or establish runtime compatibility. It fails closed if the exact
expected build.gradle differs from the source selected by the workflow.
"""

import hashlib
import pathlib
import sys

SOURCE_COMMIT = "4a9471207b14d41f5ffec2bf6b547061e8df7949"
BUILD_GRADLE_SHA256 = "e26810705347e4453465d48d9b618d1bdaa23637565565284e0fa6de161b73e5"
OLD_SETTING = b"        minSdkVersion = 30\n"
NEW_SETTING = b"        minSdkVersion = 27\n"


def prepare(source_root):
    build_file = pathlib.Path(source_root) / "android" / "build.gradle"
    original = build_file.read_bytes()
    actual = hashlib.sha256(original).hexdigest()
    if actual != BUILD_GRADLE_SHA256:
        raise ValueError("Unexpected android/build.gradle SHA-256; refusing to modify it")
    if original.count(OLD_SETTING) != 1 or NEW_SETTING in original:
        raise ValueError("Expected a single, unmodified Android minimum SDK setting")
    updated = original.replace(OLD_SETTING, NEW_SETTING, 1)
    build_file.write_bytes(updated)
    return hashlib.sha256(updated).hexdigest()


def main(argv):
    if len(argv) != 2:
        raise SystemExit("Usage: prepare_source.py SOURCE_ROOT")
    try:
        digest = prepare(argv[1])
    except (OSError, ValueError) as exc:
        raise SystemExit("Compatibility preparation failed: {}".format(exc))
    print("Experimental minimum SDK changed from 30 to 27 in the pinned source tree.")
    print("Modified android/build.gradle SHA-256: {}".format(digest))
    print("This is not evidence of runtime or VPN compatibility.")


if __name__ == "__main__":
    main(sys.argv)
