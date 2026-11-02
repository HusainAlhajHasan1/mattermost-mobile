#!/usr/bin/env python3
"""Prepare one explicitly pinned ENCMA source tree for an experimental API-27 build.

This does not touch a device, alter the checked-out project, override any library
manifest checks, or establish runtime compatibility. It fails closed if either
pinned source file differs from the source selected by the workflow.
"""

import hashlib
import pathlib
import sys

SOURCE_COMMIT = "4a9471207b14d41f5ffec2bf6b547061e8df7949"
BUILD_GRADLE_SHA256 = "e26810705347e4453465d48d9b618d1bdaa23637565565284e0fa6de161b73e5"
RNUTILS_PATH = pathlib.Path(
    "libraries/@mattermost/rnutils/android/src/main/java/com/mattermost/rnutils/RNUtilsModuleImpl.kt"
)
RNUTILS_SHA256 = "c56fa4ba49577773862a14770981eb8b1f09e111050e96fc6bd21f81ea2f80fa"
OLD_SETTING = b"        minSdkVersion = 30\n"
NEW_SETTING = b"        minSdkVersion = 27\n"
OLD_NAVIGATION = (
    b"                    w.isNavigationBarContrastEnforced = false\n"
    b"                    w.navigationBarDividerColor = Color.TRANSPARENT\n"
)
NEW_NAVIGATION = (
    b"                    if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.Q) {\n"
    b"                        w.isNavigationBarContrastEnforced = false\n"
    b"                    }\n"
    b"                    if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.P) {\n"
    b"                        w.navigationBarDividerColor = Color.TRANSPARENT\n"
    b"                    }\n"
)


def prepare(source_root):
    root = pathlib.Path(source_root)
    build_file = root / "android" / "build.gradle"
    utils_file = root / RNUTILS_PATH
    original = build_file.read_bytes()
    original_utils = utils_file.read_bytes()
    if hashlib.sha256(original).hexdigest() != BUILD_GRADLE_SHA256:
        raise ValueError("Unexpected android/build.gradle SHA-256; refusing to modify it")
    if hashlib.sha256(original_utils).hexdigest() != RNUTILS_SHA256:
        raise ValueError("Unexpected RNUtilsModuleImpl.kt SHA-256; refusing to modify it")
    if original.count(OLD_SETTING) != 1 or NEW_SETTING in original:
        raise ValueError("Expected a single, unmodified Android minimum SDK setting")
    if original_utils.count(OLD_NAVIGATION) != 1 or NEW_NAVIGATION in original_utils:
        raise ValueError("Expected one unmodified navigation-bar code block")

    # Validate both exact files before writing either one. API 27 lacks the
    # Android 10 contrast method and Android 9 divider-color method.
    updated = original.replace(OLD_SETTING, NEW_SETTING, 1)
    updated_utils = original_utils.replace(OLD_NAVIGATION, NEW_NAVIGATION, 1)
    build_file.write_bytes(updated)
    utils_file.write_bytes(updated_utils)
    return hashlib.sha256(updated).hexdigest()


def main(argv):
    if len(argv) != 2:
        raise SystemExit("Usage: prepare_source.py SOURCE_ROOT")
    try:
        digest = prepare(argv[1])
    except (OSError, ValueError) as exc:
        raise SystemExit("Compatibility preparation failed: {}".format(exc))
    utils_digest = hashlib.sha256((pathlib.Path(argv[1]) / RNUTILS_PATH).read_bytes()).hexdigest()
    print("Experimental minimum SDK changed from 30 to 27 in the pinned source tree.")
    print("Guarded Android 9/10 navigation-bar APIs for older devices.")
    print("Modified android/build.gradle SHA-256: {}".format(digest))
    print("Modified RNUtilsModuleImpl.kt SHA-256: {}".format(utils_digest))
    print("This is not evidence of runtime or VPN compatibility.")


if __name__ == "__main__":
    main(sys.argv)
