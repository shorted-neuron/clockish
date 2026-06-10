#!/usr/bin/env bash
# download-nixie-font.sh — download the Nixie One font into third_party/nixie/
# ---------------------------------------------------------------------------
# Nixie One is an open-source Google Font by Jovanny Lemonad that recreates
# the warm glowing cathode style of vintage Nixie tube displays.
# License: SIL Open Font License 1.1  (https://scripts.sil.org/OFL)
# Source:  https://github.com/google/fonts/tree/main/ofl/nixieone
#
# After running this script the font file will be in:
#   third_party/nixie/NixieOne-Regular.ttf
# and clockish's _find_font() will discover it automatically.
#
# Note: unlike DSEG there is no apt package for Nixie One, so this script
# is the recommended installation method.
# ---------------------------------------------------------------------------

set -euo pipefail

GOOGLE_FONTS_BASE="https://raw.githubusercontent.com/google/fonts/main/ofl/nixieone"
TTF_FILE="NixieOne-Regular.ttf"
OFL_FILE="OFL.txt"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEST_DIR="${PROJECT_ROOT}/third_party/nixie"

mkdir -p "${DEST_DIR}"

echo "==> Downloading Nixie One from Google Fonts GitHub…"
curl -fsSL -o "${DEST_DIR}/${TTF_FILE}" "${GOOGLE_FONTS_BASE}/${TTF_FILE}"
curl -fsSL -o "${DEST_DIR}/LICENSE-OFL.txt" "${GOOGLE_FONTS_BASE}/${OFL_FILE}"

echo ""
echo "Font installed to ${DEST_DIR}/${TTF_FILE}"
echo "License:          ${DEST_DIR}/LICENSE-OFL.txt"
echo ""
echo "Done.  You can now use nixie.yaml."

