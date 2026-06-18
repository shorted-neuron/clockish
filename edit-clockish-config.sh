#!/usr/bin/env bash
# edit-clockish-config.sh  --  open the clockish config in your preferred editor.
#
# Usage:
#   ./edit-clockish-config.sh              # auto-detect config location
#   ./edit-clockish-config.sh myfile.yaml  # explicit path

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"

# ---------------------------------------------------------------------------
# Resolve the config file
# ---------------------------------------------------------------------------
if [[ $# -gt 0 ]]; then
    # Explicit path supplied  --  use it directly
    CONFIG_FILE="$1"
    if [[ ! -f "$CONFIG_FILE" ]]; then
        echo "ERROR: file not found: $CONFIG_FILE" >&2
        exit 1
    fi
else
    # Search the same locations clockish searches, in the same order:
    #   1. ~/.config/clockish/clockish-config.yaml  (recommended user location)
    #   2. <project>/configs/clockish.yaml          (repo default)
    #   3. ~/clockish.yaml                          (home directory fallback)
    declare -a CANDIDATES=(
        "$HOME/.config/clockish/clockish-config.yaml"
        "$PROJECT_ROOT/configs/clockish.yaml"
        "$HOME/clockish.yaml"
    )
    CONFIG_FILE=""
    for candidate in "${CANDIDATES[@]}"; do
        if [[ -f "$candidate" ]]; then
            CONFIG_FILE="$candidate"
            break
        fi
    done

    if [[ -z "$CONFIG_FILE" ]]; then
        echo "No config file found.  Searched:"
        for c in "${CANDIDATES[@]}"; do echo "  $c"; done
        echo ""
        echo "To create one from the default layout:"
        echo "  mkdir -p ~/.config/clockish"
        echo "  cp $PROJECT_ROOT/configs/clockish.yaml ~/.config/clockish/clockish-config.yaml"
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# Pick an editor: $VISUAL -> $EDITOR -> nano -> vim -> vi
# ---------------------------------------------------------------------------
EDITOR_CMD="${VISUAL:-${EDITOR:-}}"
if [[ -z "$EDITOR_CMD" ]]; then
    for e in nano vim vi; do
        if command -v "$e" &>/dev/null; then
            EDITOR_CMD="$e"
            break
        fi
    done
fi

if [[ -z "$EDITOR_CMD" ]]; then
    echo "ERROR: no editor found.  Set \$EDITOR or install nano:" >&2
    echo "  sudo apt install nano" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Tell the user what we found, then ask before opening
# ---------------------------------------------------------------------------
echo "Config file : $CONFIG_FILE"
echo "Editor      : $EDITOR_CMD"
echo ""
read -r -p "Open this file? [Y/n] " REPLY
REPLY="${REPLY:-y}"
if [[ "${REPLY,,}" != "y" ]]; then
    echo "Cancelled."
    exit 0
fi

exec "$EDITOR_CMD" "$CONFIG_FILE"

