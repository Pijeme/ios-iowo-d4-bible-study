#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != Darwin ]]; then
    echo 'Run this runtime preparation step on the Mac.' >&2
    exit 1
fi
archive='Vendor/Python-3.13-iOS-support.b18.tar.gz'
expected='d06acf2a9c7964a8689566353a0e707402eff822037de5e29ba0e16040f7047d'
actual="$(shasum -a 256 "$archive" | awk '{print $1}')"
[[ "$actual" == "$expected" ]] || { echo 'Python support archive integrity check failed.' >&2; exit 1; }
# Re-extract the managed vendor package on macOS to restore its original Unix
# file modes after a Windows copy. This never touches app code or user data.
tar -xzf "$archive" -C Vendor
for framework in Vendor/Python.xcframework/*/Python.framework; do
    cp Sources/PythonRuntimePrivacy.xcprivacy "$framework/PrivacyInfo.xcprivacy"
done
echo 'Apple Python runtime prepared with original macOS file modes.'
