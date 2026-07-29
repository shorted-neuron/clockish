#!/usr/bin/env bash
# scripts/render-time-samples.sh  --  Thin wrapper around `clockish-time-samples`
# for when you forget the CLI name/args exist.
#
# Usage:
#   bash scripts/render-time-samples.sh <config.yaml> [config2.yaml ...]
#
# Renders each given config across the curated clock/date sample moments
# (see AGENTS.md "Time-sample rendering") into
# docs/previews/time-samples/{config-name}/{HH}-{MM}.png (gitignored, ad-hoc
# exploratory artifact).
#
# No args: renders both nixie.yaml (12h) and nixie24.yaml (24h) so you get a
# side-by-side 12h/24h comparison out of the box.
#
# Requires the package installed (`pip install -e .` / `.[dev]`) so the
# `clockish-time-samples` entry point is on PATH.

set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

if [ "$#" -eq 0 ]; then
    set -- configs/nixie.yaml configs/nixie24.yaml
fi

_fail=0
for cfg in "$@"; do
    echo "=== $cfg ==="
    if ! clockish-time-samples "$cfg"; then
        echo "FAILED: $cfg" >&2
        _fail=1
    fi
done

exit "$_fail"
