# Mac handoff and native acceptance checks

This folder is a source port prepared on Windows. The Swift parser and shared
engine/browser checks do not replace an Apple SDK build. Your Mac developer
must complete the checks below before distributing an IPA.

## First build

If you cloned GitHub instead of extracting the complete handoff ZIP, first run
`python3.13 manage.py restore-library --from-archive /path/to/the-matching.apk`
using the owner's standalone share APK, or pass the full iOS handoff ZIP.
See README.md for details. The restore verifies all reference-file hashes and
refuses to replace differing existing files. Then follow these same steps.

1. Copy the complete folder, including `Vendor` and `Resources`, to the Mac.
   Keep it outside a cloud placeholder-only folder; the databases must be local.
2. Install full Xcode and an iOS Simulator platform, accept its license and
   select it with `xcode-select` if necessary. Command Line Tools alone cannot
   build iOS apps. Use Xcode 16 or newer with its supported macOS version.
3. In Terminal run `bash Scripts/prepare-mac.sh` once to restore the vendor
   runtime's Unix file modes from its verified original archive after a Windows
   transfer. Then open `IOWOBibleStudy.xcodeproj`. The application targets iOS/iPadOS 16+ and
   device families 1 and 2. Choose the `IOWOBibleStudy` scheme.
4. Select an available iPhone simulator. Run the app, then repeat on an iPad
   in portrait, landscape and split-screen/multitasking widths.
5. Use Product > Test to run `NativeTests`. The target checks valid PDF text,
   pagination and invalid-export handling. Then perform the visual PDF checks
   below. Do not mark native tests passed until Xcode has actually run them.

CLI alternative:

```bash
bash Scripts/build-simulator.sh
xcodebuild -project IOWOBibleStudy.xcodeproj -scheme IOWOBibleStudy \
  -destination 'platform=iOS Simulator,name=YOUR INSTALLED IPHONE SIMULATOR' test
```

The build phase invokes the support package's bundled `install_python` utility
to select the device/simulator standard library, convert Python extension
modules into frameworks and create `.fwork` import markers. It uses ad-hoc
extension signing for a simulator build. Never ship raw Android `.so` or
Windows `.pyd` dependencies in the iOS app.

## Required native checks

- First launch: static complete artwork, first-use tutorial, Skip/Done, replay
  from the splash and Reading Settings. No image cropping in either orientation.
- Initial agreement/login: account session goes to Keychain; passwords are not
  retained. Test after relaunch and after locking/unlocking the device.
- Storage/setup: enough-space check, complete installation of all 32 resources,
  SHA-256 validation, interrupted setup retry, and refusal to replace a modified
  existing reader database. Never use uninstall/clear-data as a routine fix.
- Python preflight: confirm actual iOS SQLite FTS5 and JSON support. The adapter
  runs an in-memory search and JSON query before opening user databases. Resolve
  a failing preflight by fixing the runtime/deployment target, not dropping search.
- Offline: disconnect networking and test all translations, passage navigation,
  Thompson chains, book studies, dictionaries, concordance, all commentary
  collections, outlines and illustrations. Check long pages on a lower-memory
  iPhone as well as an iPad.
- Sermon Notes: create, autosave, relaunch, library filters, editable metadata,
  Scripture previews/insertion, highlights, fonts, case, U/B/I, colours,
  Clear Format, Copy, Undo and Erase. Verify Undo restores erased selected words.
- Keyboard/selection: toolbar opening dismisses the soft keyboard while retaining
  the selected words; dismissing tools restores typing. Test touch selection,
  hardware keyboard, long-press handles and VoiceOver.
- iPad resizing: the tab/tool/banner row stays stable when dragging the study
  divider; content uses its own scroll panes. Test multitasking and rotation.
- PDF: long sermons paginate; headings, indentation, superscript, alignment,
  underline, highlight background, text colours and fonts remain visible. Include
  Cebuano/Tagalog and Greek/Unicode text. Preview, Save to Files, Share and Cancel
  must work on iPhone and iPad without popover crashes or lost notes.
- Sync: use an authorized test account, verify upload/restore/version conflicts,
  offline error messages and account isolation. Test background time expiration
  and force-quit/reopen recovery using the existing request IDs. iOS does not
  guarantee indefinite background execution.
- Recovery: a local web-content process restart, temporary network failure or
  startup error must not delete notes, reading marks or saved drafts.

## Signing and distribution

Select your Apple team for the app and test target. If the bundle ID is already
owned by a different team, use a bundle ID owned by the intended publisher.
For an archive, set `DEVELOPMENT_TEAM` and run `Scripts/build-archive.sh`, or
archive through Xcode. Validate in Organizer before TestFlight/App Store export.
Use current Apple SDK/submission requirements when submitting.

Review the privacy manifest and App Store privacy answers against the actual
website account and optional note-sync behavior. It declares account identifiers
and user content used for app functionality, no tracking, and required reasons
for own preferences/files and disk-capacity checks. Vendor Python extensions
may have their own privacy manifests, which the embedding utility carries over.

Do not include `.test-data`, `.preview-data`, any device backup, `.env`, signing
keys or real account credentials in a build or a transfer archive. The source
folder contains only bundled public/reference content, fictional test fixtures
in tests, and code; user test data is excluded from the prepared transfer archive.

If a native build fails, save the complete Xcode error and the selected target/
architecture to `Evidence` so the next coding session can fix the precise issue.
No native iOS success has been claimed in the Windows validation report.

Startup revision: the original rounded help buttons and full-frame artwork are restored. During preparation, a loading bar and 30 rotating study verses are shown. After readiness, they hide and the artwork appears alone for three visible seconds before the reader opens; help/backgrounding delays this interval until the splash is visible again.
