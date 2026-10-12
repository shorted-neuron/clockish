#!/usr/bin/env bash
# run-clockish.sh  --  activate the venv and run clockish.
# With --install-service, write (or update) the systemd unit so clockish
# starts automatically every time the Raspberry Pi boots.
#
# Usage:
#   ./run-clockish.sh [--debug] [--debug-layout] [config.yaml]
#   ./run-clockish.sh --install-service [config.yaml]
#   ./run-clockish.sh --install-service [config.yaml] --service-name NAME

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
VENV_BIN="$PROJECT_ROOT/.venv/bin"
SERVICE_NAME="clockish"

# --service-name NAME (or --service-name=NAME) picks the systemd unit for --install-service and
# --remove-service, so a second instance on one machine does not overwrite the first. It is taken
# out of the argument list here. Same name rule as scripts/setup-testbed-sudoers.sh.
SERVICE_NAME_GIVEN=""
_args=()
while (( $# )); do
    case "$1" in
        --service-name)
            [[ $# -ge 2 ]] || { echo "ERROR: --service-name needs a value" >&2; exit 2; }
            SERVICE_NAME="$2"; SERVICE_NAME_GIVEN=1; shift 2 ;;
        --service-name=*)
            SERVICE_NAME="${1#*=}"; SERVICE_NAME_GIVEN=1; shift ;;
        *)
            _args+=("$1"); shift ;;
    esac
done
set -- "${_args[@]+"${_args[@]}"}"
if [[ ! "$SERVICE_NAME" =~ ^[A-Za-z][A-Za-z0-9_-]*$ ]]; then
    echo "ERROR: bad service name: '$SERVICE_NAME' (letters, digits, - and _; start with a letter)" >&2
    exit 2
fi
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# ---------------------------------------------------------------------------
# -h / --help   --  show wrapper usage then pass through to clockish -h
# ---------------------------------------------------------------------------
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    cat <<'USAGE'
run-clockish.sh  --  wrapper around the clockish display engine

Wrapper-specific options (handled here, not passed to clockish):
  --install-service [config.yaml]
      Write (or update) /etc/systemd/system/clockish.service and start it.
      Defaults to ~/.config/clockish/clockish-config.yaml if no config given.
      clockish will then start automatically on every boot.

  --remove-service
      Stop the clockish service, disable it, and delete the unit file.

  --service-name NAME
      With either option above, use /etc/systemd/system/NAME.service instead of
      clockish.service (default: clockish). For a second instance on one machine,
      run from its own checkout and user, e.g. --service-name clockish-oled.

All other options and arguments are forwarded to clockish:

USAGE
    "$VENV_BIN/clockish" --help 2>&1 || true
    exit 0
fi

if [[ -n "$SERVICE_NAME_GIVEN" && "${1:-}" != "--install-service" && "${1:-}" != "--remove-service" ]]; then
    echo "ERROR: --service-name only applies to --install-service and --remove-service" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# --remove-service   --  stop, disable, and delete the unit file
# ---------------------------------------------------------------------------
if [[ "${1:-}" == "--remove-service" ]]; then
    if [[ ! -f "$SERVICE_FILE" ]]; then
        echo "No service file found at $SERVICE_FILE  --  nothing to remove."
        exit 0
    fi
    echo "Stopping and disabling $SERVICE_NAME ..."
    sudo systemctl stop    "$SERVICE_NAME" 2>/dev/null || true
    sudo systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    sudo rm -f "$SERVICE_FILE"
    sudo systemctl daemon-reload
    echo "Service removed."
    exit 0
fi

# ---------------------------------------------------------------------------
# --install-service   --  write the unit file and (re)start the service
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

    DESCRIPTION="clockish LCD display"
    [[ "$SERVICE_NAME" != "clockish" ]] && DESCRIPTION="$DESCRIPTION ($SERVICE_NAME)"

    echo "Writing $SERVICE_FILE ..."
    sudo tee "$SERVICE_FILE" > /dev/null <<UNIT
[Unit]
Description=$DESCRIPTION
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
        echo "Service is already running  --  restarting..."
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
# Normal run  --  activate venv and exec clockish
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
