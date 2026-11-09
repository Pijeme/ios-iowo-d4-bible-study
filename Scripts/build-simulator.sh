#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != Darwin ]]; then
    echo 'Apple Simulator requires a Mac with full Xcode. Use the Windows WebKit preview for shared UI checks.' >&2
    exit 1
fi
bash Scripts/prepare-mac.sh
xcodebuild -project IOWOBibleStudy.xcodeproj -scheme IOWOBibleStudy \
    -configuration Debug -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' \
    -derivedDataPath build/DerivedData CODE_SIGNING_ALLOWED=NO ONLY_ACTIVE_ARCH=YES build
echo 'Built simulator app: build/DerivedData/Build/Products/Debug-iphonesimulator/IOWOBibleStudy.app'
echo 'Boot an iPhone or iPad in Xcode, then: xcrun simctl install booted "build/DerivedData/Build/Products/Debug-iphonesimulator/IOWOBibleStudy.app"'
