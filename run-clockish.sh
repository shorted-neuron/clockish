#!/usr/bin/env bash
# run-clockish.sh — activate the venv and run clockish.
# With --install-service, write (or update) the systemd unit so clockish
# starts automatically every time the Raspberry Pi boots.
#
# Usage:
#   ./run-clockish.sh [--debug] [--debug-layout] [config.yaml]
#   ./run-clockish.sh --install-service [config.yaml]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
VENV_BIN="$PROJECT_ROOT/.venv/bin"
SERVICE_NAME="clockish"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# ---------------------------------------------------------------------------
# --install-service  — write the unit file and (re)start the service
# ---------------------------------------------------------------------------
if [[ "${1:-}" == "--install-service" ]]; then
    shift

    # First non-flag argument is the config file (optional)
    CONFIG_FILE=""
    for arg in "$@"; do
        [[ "$arg" != --* ]] && { CONFIG_FILE="$arg"; break; }
    done
    # Fall back to user config location
    [[ -z "$CONFIG_FILE" ]] && CONFIG_FILE="$HOME/.config/clockish/clockish-config.yaml"

    if [[ ! -f "$CONFIG_FILE" ]]; then
        echo "ERROR: config file not found: $CONFIG_FILE" >&2
        echo "  Copy a layout from $PROJECT_ROOT/configs/ to get started, e.g.:" >&2
        echo "    cp $PROJECT_ROOT/configs/big-red.yaml $HOME/.config/clockish/clockish-config.yaml" >&2
        exit 1
    fi

    CLOCKISH_BIN="$VENV_BIN/clockish"
    if [[ ! -x "$CLOCKISH_BIN" ]]; then
        echo "ERROR: clockish not found at $CLOCKISH_BIN" >&2
        echo "  Run:  source $VENV_BIN/activate && pip install -e $PROJECT_ROOT" >&2
        exit 1
    fi

    echo "Writing $SERVICE_FILE ..."
    sudo tee "$SERVICE_FILE" > /dev/null <<UNIT
[Unit]
Description=clockish LCD display
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$PROJECT_ROOT
ExecStart=$CLOCKISH_BIN $CONFIG_FILE
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE_NAME"
    if sudo systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        echo "Service is already running — restarting..."
        sudo systemctl restart "$SERVICE_NAME"
    else
        echo "Starting service..."
        sudo systemctl start "$SERVICE_NAME"
    fi
    echo ""
    sudo systemctl status "$SERVICE_NAME" --no-pager -l
    echo ""
    echo "clockish will now start automatically on boot."
    echo "  View logs:   sudo journalctl -u $SERVICE_NAME -f"
    echo "  Stop:        sudo systemctl stop $SERVICE_NAME"
    echo "  Disable:     sudo systemctl disable $SERVICE_NAME"
    exit 0
fi

# ---------------------------------------------------------------------------
# Normal run — activate venv and exec clockish
# ---------------------------------------------------------------------------
CLOCKISH_BIN="$VENV_BIN/clockish"
if [[ ! -x "$CLOCKISH_BIN" ]]; then
    echo "ERROR: clockish entry point not found at $CLOCKISH_BIN" >&2
    echo "  Run ./install.sh first, or:" >&2
    echo "    source $VENV_BIN/activate && pip install -e $PROJECT_ROOT" >&2
    exit 1
fi
source "$VENV_BIN/activate"
exec "$CLOCKISH_BIN" "$@"

