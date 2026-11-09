#!/bin/bash
set -euo pipefail

# The application code has a different name from the lowercase Python home,
# so this works on the usual case-insensitive macOS build filesystem too.
mkdir -p "$CODESIGNING_FOLDER_PATH/StudyEngine"
rsync -a --delete --exclude '__pycache__' --exclude '*.pyc' \
    "$PROJECT_DIR/Python/" "$CODESIGNING_FOLDER_PATH/StudyEngine/"

# Use the verified support package's own architecture selection and .fwork
# conversion. Its license and source are retained under Vendor.
if [[ "${ARCHS:-}" == *" "* ]]; then
    export ARCHS="${NATIVE_ARCH_ACTUAL:-arm64}"
fi
export EXPANDED_CODE_SIGN_IDENTITY="${EXPANDED_CODE_SIGN_IDENTITY:--}"
export EXPANDED_CODE_SIGN_IDENTITY_NAME="${EXPANDED_CODE_SIGN_IDENTITY_NAME:-Ad Hoc}"
source "$PROJECT_DIR/Vendor/Python.xcframework/build/utils.sh"
install_python "Vendor/Python.xcframework" "StudyEngine/app" "StudyEngine/site-packages"

# A source/package update must never introduce desktop or Android binaries.
if find "$CODESIGNING_FOLDER_PATH/StudyEngine" -type f \( -name '*.so' -o -name '*.pyd' -o -name '*.dll' \) | grep -q .; then
    echo 'Unprocessed native binary in bundled Python code.' >&2
    exit 1
fi
