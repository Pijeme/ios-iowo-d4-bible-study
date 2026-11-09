#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${DEVELOPMENT_TEAM:?Set DEVELOPMENT_TEAM to your Apple Developer team ID.}"
bash Scripts/prepare-mac.sh
xcodebuild -project IOWOBibleStudy.xcodeproj -scheme IOWOBibleStudy \
    -configuration Release -destination 'generic/platform=iOS' \
    -archivePath build/IOWOBibleStudy.xcarchive \
    DEVELOPMENT_TEAM="$DEVELOPMENT_TEAM" archive
echo 'Open build/IOWOBibleStudy.xcarchive in Xcode Organizer for validation and the chosen distribution method.'
