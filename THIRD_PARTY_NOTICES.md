# Runtime and dependency record

The Apple Python runtime is the official BeeWare `Python-Apple-support`
3.13-b18 iOS package (Python 3.13.18), downloaded from its public release.
The original archive and its component version record are retained in `Vendor`.
Archive SHA-256: `d06acf2a9c7964a8689566353a0e707402eff822037de5e29ba0e16040f7047d`.
The support project is MIT licensed; CPython and its bundled components retain
their own licenses in the distribution. Its supplied embedding utility is used
without rewriting the extension-framework packaging logic.

The application packages are vendored from the already-audited distributions:
Flask 3.1.2, requests 2.32.5, MarkupSafe 3.0.3, Jinja2 3.1.6,
Werkzeug 3.1.9, blinker 1.9.0, click 8.5.1, itsdangerous 2.2.0,
charset-normalizer 3.5.2, idna 3.20, urllib3 2.8.0 and certifi 2026.10.23.
Their distribution metadata/license files are included under
`Python/site-packages/*.dist-info`.

Only pure-Python package code and package data are included there. Optional
Windows/Android native acceleration modules were excluded; MarkupSafe's own
`_native` fallback and charset-normalizer's Python implementation are used.
The Windows wheel RECORD/WHEEL metadata is provenance information and is not
used to install a Windows binary on iOS. A source verification rejects native
`.so`, `.pyd`, `.dll` and `.dylib` files in the vendored application packages.

No ReportLab/Pillow dependency is bundled in the iOS app. Those are desktop
preview/test dependencies only. Actual iOS PDF export uses UIKit/CoreText.
Fonts and existing study-resource credits remain with the copied app assets.

References: [Python Apple Support](https://github.com/beeware/Python-Apple-support),
[Python iOS embedding](https://docs.python.org/3.13/using/ios.html),
[Apple privacy manifest reasons](https://developer.apple.com/documentation/technotes/tn3183-adding-required-reason-api-entries-to-your-privacy-manifest).

The two vendored Python framework slices also contain a project-specific
privacy manifest for this app's own-file metadata, storage-capacity checks and
elapsed-time/timer usage. Their executable binaries are unchanged. The original
runtime archive remains intact for provenance. The Mac archive must still be
validated against Apple's current SDK/privacy requirements.
