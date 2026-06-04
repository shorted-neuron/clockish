#!/usr/bin/env bash
# uninstall.sh — remove the clockish systemd service and optionally user config.
#
# Usage:
#   ./uninstall.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="clockish"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
USER_CFG_DIR="$HOME/.config/clockish"

# ---------------------------------------------------------------------------
# Service removal
# ---------------------------------------------------------------------------
if [[ ! -f "$SERVICE_FILE" ]]; then
    echo "No service file found at $SERVICE_FILE — skipping service removal."
else
    echo "Found service: $SERVICE_FILE"
    echo ""
    read -r -p "Stop and remove the $SERVICE_NAME service? [y/N] " REPLY
    if [[ "${REPLY,,}" == "y" ]]; then
        "$SCRIPT_DIR/run-clockish.sh" --remove-service
    else
        echo "Service left in place."
    fi
fi

# ---------------------------------------------------------------------------
# User config removal
# ---------------------------------------------------------------------------
echo ""
if [[ -d "$USER_CFG_DIR" ]]; then
    echo "Found user config directory: $USER_CFG_DIR"
    read -r -p "Remove $USER_CFG_DIR and all its contents? [y/N] " REPLY
    if [[ "${REPLY,,}" == "y" ]]; then
        rm -rf "$USER_CFG_DIR"
        echo "Removed $USER_CFG_DIR"
    else
        echo "User config left in place."
    fi
fi

echo ""
echo "Done."

