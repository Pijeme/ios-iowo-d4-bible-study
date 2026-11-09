# Work progress / handoff ? 10 November 2026

## Delivered
- Huawei/universal production share APK: ../District4-Release-20261109/APK/IOWO-D4-Bible-Study-1.0.3-share.apk.
- Version 1.0.3 (4), existing production signing certificate, armeabi-v7a + arm64-v8a + x86_64; separate opt-in Python 3.11 compatibility profile. Default modern Python 3.13 profile remains available.
- Final APK includes restored rounded buttons, full-frame artwork, loading bar and 30 rotating study verses, followed by three seconds of artwork alone, revised tutorial screenshots, all prior Notes tools and 8px inset. All 32 resource hashes, production signature, ZIP 16KB alignment and 140 ELF64 load alignments verified.
- Original 1.0.1 production APK archived for rollback; original Play AAB and test-phone artifact remain historical versions and are clearly labeled. No app was uninstalled and no user's notes were cleared.
- Docker Hub image pijeme/district4-tool-main:20261110-notes-tools and latest published. Digest sha256:cb16d6a008274bba6f558d7a3b480a27bbc73b37f9281e4b02ad8538ee3ef028.
- Local Server update Docker deployment recreated with its existing named data volume; health healthy and CSS matches the current website source. Public URL health 200 but CSS still old. Production access was requested; not supplied yet. A remote pull/recreate is still needed there.
- Standalone iOS source in this folder: Xcode project, Swift/Objective-C native host, Apple Python runtime, Keychain/resource install/PDF/share adapters, exact shared study engine and interface, full 32-file offline library, tutorial and static splash.
- Earlier delivery kept source local. The owner subsequently authorized publishing the latest website/Android source and a new public iOS source repository.

## Verified here
- Python 3.13.12 with vendored pure-Python packages: 25 tests, 22 passed / 3 skipped (browser suites separate; Android-only ELF test inapplicable).
- Three iOS bridge contract/error/preflight tests passed; SQLite connection cleanup corrected.
- Browser workflows: Chromium desktop/phone; WebKit phone, iPad portrait and iPad landscape passed.
- Tutorial browser suite: Chromium desktop/phone and WebKit phone passed.
- Final static splash: 390x844, 820x1180, 1180x820 and 844x390 passed with full-frame artwork and rounded help controls overlaid.
- Swift syntax (6 files), 55-object Xcode OpenStep project, Info/privacy plists, launch storyboard and shared scheme parse checks passed.
- Python Apple support 3.13-b18 archive SHA-256 verified. Framework executables unchanged; project-specific privacy manifests added to the two slices.

## Mac work still required
- No Apple SDK compilation/type checking, Apple Simulator execution, physical iPhone/iPad testing, signing or IPA export was possible on Windows.
- Friend should follow MAC_HANDOFF.md: full Xcode build; native PDF tests; actual SQLite FTS5/JSON preflight; Keychain/lock; storage and interrupted setup; touch/hardware keyboard; iPad popovers; Unicode/formatting/PDF; background sync suspension/recovery; signed archive validation.
- iOS background execution is limited; persistent request IDs preserve sync recovery, but indefinite background sync is not promised.

## Preview
- Windows shared-UI preview runs on http://127.0.0.1:8766/open-preview?gate=test-gate using a fictional account. This is not an Apple Simulator.
- A Python 3.13 .venv and VS Code launch configuration are ready. See README.md.
- User has Windows/VS Code only and plans to send this folder to a friend with a Mac. They selected complete artwork visible, not cropping.

## Resuming
- Preserve the remaining native/production-host limitations. Do not claim they passed.
- No automatic restart on credit replenishment can be guaranteed. This file and Evidence preserve the concrete state for the next session.

## Portable handoff
- Handoff/IOWO-D4-Bible-Study-iOS-Source.zip is prepared and CRC-checked. It contains 4,161 files, the complete offline resources and vendor runtime, with test databases/environments/signing credentials excluded. See Handoff/SHA256SUMS.txt for the final digest.
- Native artwork respects the top safe area so a camera notch does not cover it.
- Production-host access is still not supplied; Docker Hub and local Docker steps are complete. Native Apple build/testing remains for the Mac handoff.

## Restored startup and phone install
- Android 1.0.3 (4) installed in place on the connected test phone. Personal notes and reader database hashes were unchanged after installation. Verified artwork-only interval: 3,002ms; no fatal startup error.
- iOS source has the same ready/three-second presentation state machine, with help/background interruptions restarting the visible interval. Apple SDK compilation and native testing remain pending.
- Previous production 1.0.2 is preserved in the release Archive. Current production/share APK is 1.0.3, with all three ABIs and 32 offline resource hashes verified.
- No Docker publishing was performed for this splash revision. GitHub publication was subsequently requested by the owner.

## Public source preparation
- Public iOS source excludes the large offline databases, extracted Apple runtime, local handoff archive, device evidence and test data. The original verified Apple support archive and vendored pure-Python packages are included.
- `manage.py restore-library --from-archive` restores all manifest-listed reference files from the matching standalone APK or full local iOS handoff ZIP. Existing differing files are preserved; streamed SHA-256 checks and temporary-file cleanup were verified.
- The complete local handoff remains the self-contained transfer option for the Mac developer. Native Apple build/testing remains pending.
