# Experimental ENCMA build for SM-P585

This is an **experiment**, not an assertion that the app supports Android 8.1.
The requested Actions run `41048323747` built ENCMA from source commit
`4a9471207b14d41f5ffec2bf6b547061e8df7949`, for arm64 and with minimum API 30.
The user's existing SM-P585 installation reported Android 8.1/API 27 and
`armeabi-v7a`. That original APK is not suitable for that OS.

The workflow `.github/workflows/p585-encma-compat.yml` checks out that exact
source separately, checks the known `android/build.gradle` digest, changes only
its `minSdkVersion` from 30 to 27, and builds ARMv7 and x86_64 ABI splits. It
does **not** suppress Android library manifest minimum-SDK errors. It verifies
package ID, app label, min SDK, signatures, native ELF architecture, key React
Native/Hermes/WireGuard libraries, library-set parity, and APK SHA-256. It then
runs native tests and starts the x86_64 sibling on a fresh Android 8.1 emulator,
requiring the WireGuard configuration screen to render before uploading the ARM
APK. Synthetic tests cover the patch and content checks.

Important boundaries:

- Lowering a declared minimum is not a compatibility fix. Building and running
  an **x86_64** emulator does not prove the **ARM** APK works on a Samsung
  tablet, nor that calls, notifications, file access, authentication, or any
  server integration work. Native code may behave differently by ABI.
- No real VPN, server, or credentials are used in the emulator smoke test. The
  app requires a working WireGuard configuration and server to continue past its
  VPN gate. Android normally permits only one active VPN per user: starting this
  app's tunnel could replace the user's existing VPN. Do not automatically
  connect it or change existing VPN settings.
- In the absence of configured release signing credentials, this source uses
  its repository's **public debug signing key**, so the build is not suitable as
  a trusted production release. Anyone with that key can sign updates. A
  separately authorized release-signing process is needed for production.
- The workflow uploads a temporary Actions artifact only after its checks pass.
  It never installs anything on a physical device. An installation command,
  if created, must use a pinned artifact digest, preserve any existing package
  and app data, and not launch the app or change VPN settings automatically.
