#!/usr/bin/env bash
# scripts/patch-venv-utf8.sh  --  Inject PYTHONUTF8=1 into a venv's activation scripts.
#
# Usage:
#   bash scripts/patch-venv-utf8.sh            # patches .venv in current directory
#   bash scripts/patch-venv-utf8.sh /path/to/venv
#
# Why this exists
# ---------------
# On Windows (and some minimal Linux installs) Python's open() defaults to the
# system locale encoding (cp1252 / latin-1) rather than UTF-8.  PEP 540 added
# PYTHONUTF8=1 (Python >= 3.7) to force UTF-8 everywhere.  Python 3.15 will
# make UTF-8 mode the default; this script bridges the gap.
#
# Run this script once after creating a venv:
#   python -m venv .venv
#   bash scripts/patch-venv-utf8.sh
#
# The patch is idempotent -- running it twice is safe.
# The patch is lost if the venv is recreated; run this script again after that.

set -euo pipefail

VENV_DIR="${1:-.venv}"

if [ ! -d "$VENV_DIR" ]; then
    echo "ERROR: venv directory not found: $VENV_DIR" >&2
    exit 1
fi

MARKER="# PYTHONUTF8=1 -- injected by scripts/patch-venv-utf8.sh"
patched=0

# ---------------------------------------------------------------------------
# activate  (bash / zsh / POSIX sh)
# ---------------------------------------------------------------------------
ACTIVATE="$VENV_DIR/bin/activate"
if [ -f "$ACTIVATE" ] && ! grep -q "$MARKER" "$ACTIVATE"; then
    cat >> "$ACTIVATE" <<EOF

$MARKER
export PYTHONUTF8=1
EOF
    echo "  patched: $ACTIVATE"
    patched=$((patched + 1))
fi

# ---------------------------------------------------------------------------
# activate.fish  (fish shell)
# ---------------------------------------------------------------------------
ACTIVATE_FISH="$VENV_DIR/bin/activate.fish"
if [ -f "$ACTIVATE_FISH" ] && ! grep -q "$MARKER" "$ACTIVATE_FISH"; then
    cat >> "$ACTIVATE_FISH" <<EOF

$MARKER
set -gx PYTHONUTF8 1
EOF
    echo "  patched: $ACTIVATE_FISH"
    patched=$((patched + 1))
fi

# ---------------------------------------------------------------------------
# activate.csh  (C shell -- present in some venv versions)
# ---------------------------------------------------------------------------
ACTIVATE_CSH="$VENV_DIR/bin/activate.csh"
if [ -f "$ACTIVATE_CSH" ] && ! grep -q "$MARKER" "$ACTIVATE_CSH"; then
    cat >> "$ACTIVATE_CSH" <<EOF

$MARKER
setenv PYTHONUTF8 1
EOF
    echo "  patched: $ACTIVATE_CSH"
    patched=$((patched + 1))
fi

# ---------------------------------------------------------------------------
# Scripts/ (Windows Scripts directory inside a cross-platform venv)
# ---------------------------------------------------------------------------
ACTIVATE_BAT="$VENV_DIR/Scripts/activate.bat"
if [ -f "$ACTIVATE_BAT" ] && ! grep -qi "PYTHONUTF8" "$ACTIVATE_BAT"; then
    # Insert 'set PYTHONUTF8=1' before the first @echo off / before prompt line
    sed -i "1s/^/rem $MARKER\r\nset PYTHONUTF8=1\r\n/" "$ACTIVATE_BAT"
    echo "  patched: $ACTIVATE_BAT"
    patched=$((patched + 1))
fi

echo ""
if [ "$patched" -gt 0 ]; then
    echo "Patched $patched activation script(s) in $VENV_DIR with PYTHONUTF8=1."
else
    echo "All activation scripts in $VENV_DIR already patched (nothing to do)."
fi

