# IOWO D4 Bible Study for iPhone and iPad

This is a standalone iOS source port of the local Android app, version 1.0.4 (5).
It includes an Xcode project, native Swift/Objective-C host, embedded Apple
Python runtime, the shared offline study engine, the existing HTML/CSS/JS
interface, tutorials, and all 32 offline resource files.

**Status:** source and shared-interface validation on Windows. This has not
been compiled against Apple's SDK, installed on an iPhone/iPad, signed for
distribution, or submitted to Apple. Read `MAC_HANDOFF.md` before calling it
a tested iOS release. The Mac build and native tests are the next required step.

## What stays familiar

- The same Bible languages, passage navigation, Thompson Chain Index,
  dictionaries, concordance, book studies, commentaries, outlines and illustrations.
- The same Sermon Notes library, editing, highlighting, case/font tools,
  Clear Format, Copy, Undo, Erase, autosave, conflict handling and account sync.
- The same full-width editor with an 8px left inset and stable tablet headers.
- The same first-open tutorial, searchable outline, annotated screenshots,
  replay controls and optional support-card cadence.
- A static full-screen splash retaining the complete artwork without cropping.
  Bible and notes readiness checks continue behind it.

Native Apple services replace Android-specific APIs: Keychain replaces the
Android Keystore, UIKit/CoreText renders PDFs, the share sheet and document
picker handle exports, and WKWebView hosts the existing interface. The internal
`IOWOAndroid` JavaScript object is a compatibility facade, so the shared study
code can keep its existing workflow; it does not mean the app runs Android.

## Windows / VS Code preview

Windows cannot run Apple's iOS Simulator. This preview runs the real shared
interface and study engine with a fictional local account. It does not test
UIKit, Keychain, Apple's embedded runtime, native PDF output, device memory,
signing or App Store acceptance.

With `uv` installed:

```powershell
uv venv --python 3.13 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements-preview.txt
.venv/Scripts/python.exe -m playwright install chromium webkit
.venv/Scripts/python.exe manage.py verify
.venv/Scripts/python.exe manage.py preview
```

Open the printed `http://127.0.0.1:8766/open-preview?gate=test-gate` link. In
VS Code, select the new interpreter and use the supplied launch configuration.
The first preview copies a writable reader database into `.test-data`; the
bundled databases remain unchanged. The sample account never contacts live
Drive or changes a real person's notes.

Shared-engine tests:

```powershell
.venv/Scripts/python.exe -m unittest discover -s Tests -p test_offline.py
$env:IOWO_BROWSER_TESTS='1'
.venv/Scripts/python.exe -m unittest discover -s Tests -p test_offline.py -k BrowserTests
```

The browser suite includes WebKit at iPhone, iPad portrait and iPad landscape
sizes. Those are browser emulations, not Apple Simulator builds.

## Mac / Xcode

### Restoring a GitHub clone

The public repository contains the source, Xcode project, tutorials, vendored
pure-Python packages and the verified Apple Python support archive. Large
offline databases, local test data, signing credentials and handoff ZIPs are
excluded. The complete local handoff ZIP already includes the offline library.

For a GitHub clone, obtain the matching standalone Android share APK or complete
iOS handoff ZIP from the project owner, then restore the 32 reference files:

```bash
python3.13 manage.py restore-library --from-archive /path/to/IOWO-D4-Bible-Study-1.0.3-share.apk
```

This streams and verifies every file against the checked-in SHA-256 manifest.
It refuses to overwrite an existing file with different contents. It does not
import accounts or personal notes. Run the normal Mac preparation step below
to extract the Apple runtime; the excluded framework directory is reconstructed
from the bundled, verified support archive. All study features remain offline.

First run `bash Scripts/prepare-mac.sh` on the Mac to restore the original
runtime file modes after a Windows transfer. Open `IOWOBibleStudy.xcodeproj` in full Xcode, select an iPhone or iPad simulator,
and build the `IOWOBibleStudy` scheme. No Android SDK or Android source folder
is needed. The Apple Python support package and pure-Python dependencies are
already present. A simulator build does not need a paid developer account.

```bash
bash Scripts/build-simulator.sh
```

For a signed device/archive build, configure your own Apple team and bundle
identifier in Xcode, then follow `MAC_HANDOFF.md`. Signing identities and account
credentials are deliberately absent. The Android production signing key is
not an Apple signing certificate and is not included.

## Important platform differences

iOS grants a limited period of background execution, unlike Android's
foreground sync service. The app requests that available period, while keeping
the existing durable per-note request IDs and sync conflict/version behavior.
If iOS suspends or terminates the app, reopen it and retry Sync Notes when
needed. Do not promise uninterrupted backup after force-quitting.

Android private data is not directly moved into an iPhone container. Sign in
with the same account and use the existing two-way sync to restore notes.
SQLite content remains architecture-independent. Existing user data is never
replaced by the resource installer; a differing existing database produces a
recovery message instead of an overwrite.

The uncompressed study resources occupy approximately 2.16 GB. First setup
also needs writable private copies plus headroom, so allow several GB of free
space on the device/simulator. A Mac build needs additional working space.

## Folder guide

- `Sources`: native host, secure vault, PDF/export, safe resource installation,
  launch storyboard, icon assets, Info and privacy manifests.
- `Python/app`: unchanged shared engine plus the iOS host adapter.
- `Python/site-packages`: vendored pure-Python requirements and licenses.
- `Resources`: shared web app, tutorials, branding and complete offline library.
- `Vendor`: verified BeeWare Apple Python support package and original archive.
- `Tests`: shared offline/browser checks and Xcode-native PDF tests.
- `Evidence`: local validation results. `WORK_PROGRESS.md` records resumable work.

`python manage.py project` regenerates the checked-in Xcode project on Windows
or macOS if project settings need changing. No GitHub publishing is performed.

References: [Python on iOS](https://docs.python.org/3.13/using/ios.html),
[BeeWare Apple support](https://github.com/beeware/Python-Apple-support),
[Apple simulator setup](https://developer.apple.com/documentation/safari-developer-tools/installing-xcode-and-simulators).

Startup revision: the original rounded help buttons and full-frame artwork are restored. During preparation, a loading bar and 30 rotating study verses are shown. After readiness, they hide and the artwork appears alone for three visible seconds before the reader opens; help/backgrounding delays this interval until the splash is visible again.

## Bible spacing update, 15 November 2026

Version 1.0.4 (5) includes the website spacing fixes from commit `6859a40`.
The original NIV PDF and Biblica USFM archives were checked against the active
source SHA-256 hashes, then all 92,507 verse entries were parsed and compared.
Only missing ASCII spaces differed: 1,936 NIV verses (2,160 spaces), 3,662
Cebuano verses (3,985 spaces), and 3,080 Tagalog verses (3,350 spaces).
No letters, punctuation, existing spaces, or personal writing are replaced.

`runtime/bible-spacing-20261115.json` contains source hashes, verse hashes and
insertion offsets. Both apps ship the identical verified update. It applies
automatically after initial resource setup and before an existing reader starts.
A verified private recovery backup is made before applying changes. Earlier
verse revisions remain available, and valid highlights, bookmarks, reading
positions, red-letter spans and chain links are mapped to the corrected text.
Unverifiable saved selections remain attached to their original verse revision.
The update records a completion marker and safely resumes after interruption.
Allow temporary free storage for validation and a backup of the reader database;
the backup stays in the device's private `study/backups` directory.

The original bundled database and its resource hashes are retained so existing
APK/library restore archives continue to work. The installed writable reader
receives the corrections; do not overwrite it with a new reference database.
The correction JSON uses LF endings so Git checkouts preserve its checksum.
