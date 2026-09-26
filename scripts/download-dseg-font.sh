#!/usr/bin/env bash
# download-dseg-font.sh  --  download the DSEG 7-segment font into third_party/dseg/
# ---------------------------------------------------------------------------
# DSEG is an open-source 7-segment / 14-segment TrueType font by keshikan.
# License: SIL Open Font License 1.1  (https://scripts.sil.org/OFL)
# Source:  https://github.com/keshikan/DSEG
#
# After running this script the font files will be in:
#   third_party/dseg/
# and clockish's _find_font() will discover them automatically.
#
# Alternatively, on Debian / Raspberry Pi OS / Ubuntu you can install
# the font system-wide and skip this script entirely:
#   sudo apt install fonts-dseg
# ---------------------------------------------------------------------------

set -euo pipefail

DSEG_DIRVERSION="v0.46"
DSEG_VERSION="v046"
DSEG_ZIP="fonts-DSEG_${DSEG_VERSION}.zip"
DSEG_URL="https://github.com/keshikan/DSEG/releases/download/${DSEG_DIRVERSION}/${DSEG_ZIP}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEST_DIR="${PROJECT_ROOT}/third_party/dseg"

echo "==> Downloading DSEG v${DSEG_VERSION} from GitHub..."
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

curl -fsSL -o "${TMP_DIR}/${DSEG_ZIP}" "${DSEG_URL}"

echo "==> Extracting..."
unzip -q "${TMP_DIR}/${DSEG_ZIP}" -d "${TMP_DIR}/dseg-extracted"

# The zip contains a nested directory like "DSEG-font-0.46/".
# Find all TTF files and copy them flat into third_party/dseg/.
mkdir -p "${DEST_DIR}"

# Copy the OFL license
find "${TMP_DIR}/dseg-extracted" -iname "OFL*.txt" -exec cp {} "${DEST_DIR}/LICENSE-OFL.txt" \; 2>/dev/null || true

# Copy every TTF file directly into the dest dir (flatten subdirectories)
TTF_COUNT=0
while IFS= read -r -d '' ttf; do
    cp "${ttf}" "${DEST_DIR}/"
    TTF_COUNT=$((TTF_COUNT + 1))
done < <(find "${TMP_DIR}/dseg-extracted" -iname "*.ttf" -print0)

echo "==> Copied ${TTF_COUNT} TTF files to ${DEST_DIR}/"
echo ""
echo "Font variants now available (set these as 'file:' in your config):"
ls "${DEST_DIR}"/*.ttf 2>/dev/null | xargs -I{} basename {} | sort
echo ""
echo "Done.  You can now use seven-segment.yaml (or any config with DSEG7 fonts)."
