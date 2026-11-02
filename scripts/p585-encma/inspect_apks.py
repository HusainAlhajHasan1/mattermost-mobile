#!/usr/bin/env python3
"""Sanity-check ABI split contents; Android SDK tools check package metadata separately."""

import pathlib
import struct
import sys
import zipfile

# React Native 0.83 ships Hermes under its 0.82+ name, libhermesvm.so.
REQUIRED_LIBS = frozenset({"libreactnative.so", "libhermesvm.so", "libwg-go.so"})
ABIS = {"armeabi-v7a": (1, 40), "x86_64": (2, 62)}


def inspect(path, abi):
    elf_class, machine = ABIS[abi]
    with zipfile.ZipFile(path) as apk:
        entries = apk.namelist()
        if len(entries) != len(set(entries)):
            raise ValueError("{} has duplicate ZIP entries".format(path))
        for required in ("AndroidManifest.xml", "assets/index.android.bundle"):
            if required not in entries:
                raise ValueError("{} is missing {}".format(path, required))
        all_native = [name for name in entries if name.startswith("lib/") and name.endswith(".so")]
        if any(len(name.split("/")) != 3 for name in all_native):
            raise ValueError("Unexpected native library path in {}".format(path))
        actual_abis = {name.split("/")[1] for name in all_native}
        print("{} native entries: {}".format(path, ", ".join(sorted(all_native))), flush=True)
        if actual_abis != {abi}:
            raise ValueError("{} contains unexpected native ABIs: {}".format(path, sorted(actual_abis)))
        libraries = set()
        for name in all_native:
            with apk.open(name) as native:
                header = native.read(20)
            if (len(header) < 20 or header[:4] != b"\x7fELF" or
                    header[4] != elf_class or header[5] != 1 or
                    struct.unpack("<H", header[18:20])[0] != machine):
                raise ValueError("Wrong or invalid ELF architecture: {}".format(name))
            libraries.add(name.rsplit("/", 1)[1])
        missing = REQUIRED_LIBS - libraries
        if missing:
            raise ValueError("{} is missing required libraries: {}".format(path, sorted(missing)))
        return libraries


def main(argv):
    if len(argv) != 3:
        raise SystemExit("Usage: inspect_apks.py ARM_APK X86_64_APK")
    try:
        arm = inspect(pathlib.Path(argv[1]), "armeabi-v7a")
        x86 = inspect(pathlib.Path(argv[2]), "x86_64")
        missing_from_arm = x86 - arm
        if missing_from_arm:
            raise ValueError("Native libraries present on x86_64 but missing on ARM: {}".format(
                sorted(missing_from_arm)))
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise SystemExit("APK content verification failed: {}".format(exc))
    print("ARM and x86_64 ELF headers and required React Native, Hermes, and WireGuard libraries verified.")
    print("ARM libraries: {}".format(", ".join(sorted(arm))))
    print("x86_64 libraries: {}".format(", ".join(sorted(x86))))
    print("Library presence is not proof of runtime compatibility.")


if __name__ == "__main__":
    main(sys.argv)
