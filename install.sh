#!/usr/bin/env bash
# =============================================================================
# install.sh  —  Bootstrap script for clockish.py
#
# Usage:
#   chmod +x install.sh
#   ./install.sh
#
# What this script does:
#   1. Verifies it is running on a Raspberry Pi / Linux
#   2. Checks and installs required apt system packages
#   3. Verifies that SPI is enabled (offers to enable it via raspi-config)
#   4. Creates a Python virtual environment (.venv)
#   5. Installs all required pip packages into .venv
#   6. Copies the default config to ~/.config/clockish/clockish-config.yaml
#   7. Ensures run-clockish.sh and edit-clockish-config.sh are executable
#   8. Prints a final summary / next-steps message
#
# Run as a regular user with sudo access (NOT as root).
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
ok()      { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
die()     { error "$*"; exit 1; }
section() { echo -e "\n${BOLD}${CYAN}=== $* ===${RESET}"; }

# ---------------------------------------------------------------------------
# 0. Sanity checks
# ---------------------------------------------------------------------------
section "Environment checks"

[[ "$EUID" -eq 0 ]] && die "Do NOT run this script as root. Run as your normal user (sudo will be called when needed)."

OS=$(uname -s)
[[ "$OS" != "Linux" ]] && die "This script is for Linux (Raspberry Pi OS). Detected: $OS"

# Detect Raspberry Pi
if grep -qi "raspberry" /proc/cpuinfo 2>/dev/null || \
   grep -qi "raspberry" /sys/firmware/devicetree/base/model 2>/dev/null; then
    IS_RPI=true
    RPI_MODEL=$(cat /sys/firmware/devicetree/base/model 2>/dev/null | tr -d '\0' || echo "unknown")
    ok "Running on Raspberry Pi: $RPI_MODEL"
else
    IS_RPI=false
    warn "Not detected as a Raspberry Pi — GPIO/SPI checks will be skipped."
fi

# Python version check (need 3.11+)
PYTHON_BIN=$(command -v python3 || true)
[[ -z "$PYTHON_BIN" ]] && die "python3 not found. Install with: sudo apt install python3"
PY_VER=$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
if [[ "$PY_MAJOR" -lt 3 || ( "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 11 ) ]]; then
    die "Python 3.11+ required. Found: $PY_VER"
fi
ok "Python $PY_VER found at $PYTHON_BIN"

# ---------------------------------------------------------------------------
# 1. System (apt) packages
# ---------------------------------------------------------------------------
section "System package checks"

APT_PACKAGES=(
    # Python build tools
    python3-pip
    python3-dev
    python3-venv          # needed to create .venv
    python3-numpy         # packaged version of numpy (for PIL) — much faster to install than via pip
    # Pillow native dependencies
    libopenjp2-7          # JPEG 2000 support
    libfreetype6          # TrueType font rendering
    libjpeg-dev           # JPEG support
    zlib1g-dev            # PNG / zlib support
    # Font files — DejaVuSans.ttf is the default font used by clockish
    fonts-dejavu-core
    # 7-segment display font — used by configs/seven-segment.yaml
    fonts-dseg
    # swig is needed to build rpi-lgpio later
    swig
    # python3-swiglpk # unknown if needed
    # lgpio / GPIO system library (needed by rpi-lgpio pip package)
    python3-libgpiod
    # Timezone database — required by zoneinfo (used for multi-timezone clocks)
    tzdata
    # Useful utilities already installed but listed for completeness
    git
    tmux
    chrony                # NTP (get_ntp_upstream_count() calls chronyc)
    bind9-dnsutils
)

MISSING_APT=()
for pkg in "${APT_PACKAGES[@]}"; do
    if dpkg -s "$pkg" &>/dev/null; then
        ok "  apt: $pkg"
    else
        warn "  apt: $pkg  [MISSING]"
        MISSING_APT+=("$pkg")
    fi
done

if [[ ${#MISSING_APT[@]} -gt 0 ]]; then
    info "Installing missing apt packages: ${MISSING_APT[*]}"
    sudo apt-get update -qq
    sudo apt-get install -y "${MISSING_APT[@]}"
    ok "apt packages installed."
else
    ok "All required apt packages are present."
fi

# ---------------------------------------------------------------------------
# 2. SPI interface check  (Raspberry Pi only)
# ---------------------------------------------------------------------------
section "SPI interface check"

if [[ "$IS_RPI" == true ]]; then
    SPI_ENABLED=false

    # Method 1: check for /dev/spidev0.0
    if [[ -e /dev/spidev0.0 ]]; then
        SPI_ENABLED=true
        ok "SPI device /dev/spidev0.0 found."
    fi

    # Method 2: check /boot/config.txt or /boot/firmware/config.txt
    for BOOT_CFG in /boot/firmware/config.txt /boot/config.txt; do
        if [[ -f "$BOOT_CFG" ]] && grep -q "^dtparam=spi=on" "$BOOT_CFG"; then
            SPI_ENABLED=true
            ok "SPI enabled in $BOOT_CFG"
        fi
    done

    if [[ "$SPI_ENABLED" == false ]]; then
        warn "SPI does not appear to be enabled!"
        echo ""
        echo "  To enable SPI, run ONE of the following:"
        echo "    Option A (interactive):  sudo raspi-config"
        echo "       -> Interface Options -> SPI -> Enable"
        echo ""
        echo "    Option B (non-interactive, then reboot):"
        echo "       sudo raspi-config nonint do_spi 0"
        echo "       sudo reboot"
        echo ""
        read -r -p "  Enable SPI now automatically? [y/N] " REPLY
        if [[ "${REPLY,,}" == "y" ]]; then
            sudo raspi-config nonint do_spi 0
            warn "SPI enabled — a REBOOT IS REQUIRED before SPI will work."
            warn "After rebooting, re-run this script or just run clockish"
            NEEDS_REBOOT=true
        else
            warn "Skipping SPI enable. clockish will fail at runtime without SPI."
        fi
    fi

    # SPI group membership check
    if ! groups | grep -qw "spi"; then
        warn "User '$USER' is not in the 'spi' group."
        info "Adding $USER to spi group..."
        sudo usermod -aG spi "$USER"
        warn "Group change requires logout/login (or reboot) to take effect."
    else
        ok "User '$USER' is in the 'spi' group."
    fi

    # GPIO group membership check
    if ! groups | grep -qw "gpio"; then
        warn "User '$USER' is not in the 'gpio' group."
        info "Adding $USER to gpio group..."
        sudo usermod -aG gpio "$USER"
        warn "Group change requires logout/login (or reboot) to take effect."
    else
        ok "User '$USER' is in the 'gpio' group."
    fi
else
    warn "Skipping SPI check (not a Raspberry Pi)."
fi

# ---------------------------------------------------------------------------
# 3. Virtual environment
# ---------------------------------------------------------------------------
section "Python virtual environment"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

# If heavy packages (numpy, gpiod) are already installed at the system level
# via apt, pass --system-site-packages so the venv inherits them instead of
# letting pip recompile/download them (numpy in particular takes a long time).
VENV_SYSTEM_FLAG=""
if python3 -c "import numpy" 2>/dev/null; then
    VENV_SYSTEM_FLAG="--system-site-packages"
    ok "System numpy detected — venv will use --system-site-packages"
    info "  (apt-installed packages such as python3-numpy will be visible inside the venv)"
else
    info "System numpy not found — creating isolated venv (numpy will be pip-installed)"
fi

if [[ -d "$VENV_DIR" ]]; then
    ok ".venv already exists at $VENV_DIR"
    read -r -p "  Re-create .venv from scratch? [y/N] " REPLY
    if [[ "${REPLY,,}" == "y" ]]; then
        info "Removing existing .venv..."
        rm -rf "$VENV_DIR"
        # shellcheck disable=SC2086
        python3 -m venv $VENV_SYSTEM_FLAG "$VENV_DIR"
        ok ".venv re-created."
    fi
else
    info "Creating .venv at $VENV_DIR ..."
    # shellcheck disable=SC2086
    python3 -m venv $VENV_SYSTEM_FLAG "$VENV_DIR"
    ok ".venv created."
fi

VENV_PY="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

# ---------------------------------------------------------------------------
# 4. Pip packages
# ---------------------------------------------------------------------------
section "Python pip packages"

info "Upgrading pip..."
"$VENV_PIP" install --upgrade pip --quiet

# Core packages required by clockish.
# numpy is intentionally absent here — it is either inherited from the system
# python3-numpy apt package (when the venv was created with --system-site-packages)
# or pulled in automatically as a pyproject.toml dependency of clockish itself.
PIP_PACKAGES=(
    "Pillow>=12.0.0"         # image rendering
    "spidev>=3.6"            # SPI bus interface
    "PyYAML>=6.0"            # clockish.yaml parsing
    "tzdata>=2024.1"         # IANA timezone database for zoneinfo (fallback when system tzdata absent)
)

# GPIO: rpi-lgpio is the recommended drop-in for RPi.GPIO on modern kernels.
# It imports as RPi.GPIO so no code changes are needed.
# lgpio is a system-level library that rpi-lgpio builds on.
if [[ "$IS_RPI" == true ]]; then
    PIP_PACKAGES+=("rpi-lgpio>=0.6")
else
    warn "Not on Raspberry Pi — skipping rpi-lgpio. GPIO will fail at runtime."
fi

info "Installing pip packages..."
for pkg in "${PIP_PACKAGES[@]}"; do
    info "  pip install \"$pkg\""
    "$VENV_PIP" install "$pkg" --quiet
    ok "  installed: $pkg"
done

# Install the clockish package itself (creates the `clockish` entry-point binary).
info "Installing clockish package (pip install -e .) ..."
"$VENV_PIP" install -e "$SCRIPT_DIR" --quiet
ok "clockish package installed — entry point: $VENV_DIR/bin/clockish"

# ---------------------------------------------------------------------------
# 5. Verify key imports
# ---------------------------------------------------------------------------
section "Import verification"

IMPORT_ERRORS=0

check_import() {
    local module="$1"
    local label="${2:-$1}"
    if "$VENV_PY" -c "import $module" 2>/dev/null; then
        ok "  import $label"
    else
        error "  import $label  [FAILED]"
        IMPORT_ERRORS=$((IMPORT_ERRORS + 1))
    fi
}

check_import "PIL"        "Pillow (PIL)"
check_import "numpy"      "numpy"
check_import "yaml"       "PyYAML (yaml)"

if [[ "$IS_RPI" == true ]]; then
    check_import "spidev"     "spidev"
    check_import "RPi.GPIO"   "RPi.GPIO (via rpi-lgpio)"
fi


# ---------------------------------------------------------------------------
# 6. User config file
# ---------------------------------------------------------------------------
section "User configuration"

USER_CFG_DIR="$HOME/.config/clockish"
USER_CFG="$USER_CFG_DIR/clockish-config.yaml"
DEFAULT_CFG="$SCRIPT_DIR/configs/clockish.yaml"

if [[ -f "$USER_CFG" ]]; then
    ok "User config already exists: $USER_CFG"
    info "  (not overwritten — edit it with: ./edit-clockish-config.sh)"
else
    if [[ -f "$DEFAULT_CFG" ]]; then
        mkdir -p "$USER_CFG_DIR"
        cp "$DEFAULT_CFG" "$USER_CFG"
        ok "Default config copied to $USER_CFG"
        info "  Edit it to customise your layout: ./edit-clockish-config.sh"
    else
        warn "Default config not found at $DEFAULT_CFG — skipping user config copy."
    fi
fi

# ---------------------------------------------------------------------------
# 7. Font check
# ---------------------------------------------------------------------------
section "Font check"

FONT_SEARCH_DIRS=(
    /usr/share/fonts/truetype/dejavu
    /usr/share/fonts/truetype
    /usr/share/fonts
)
FONT_FOUND=false
for d in "${FONT_SEARCH_DIRS[@]}"; do
    if [[ -f "$d/DejaVuSans.ttf" ]]; then
        ok "DejaVuSans.ttf found at $d/DejaVuSans.ttf"
        FONT_FOUND=true
        break
    fi
done

if [[ "$FONT_FOUND" == false ]]; then
    warn "DejaVuSans.ttf not found in standard locations."
    warn "clockish will fail when loading fonts."
    info "Install with:  sudo apt install fonts-dejavu-core"
fi

# Check if Pillow can actually load the font
if [[ "$FONT_FOUND" == true ]]; then
    FONT_PATH=$(find "${FONT_SEARCH_DIRS[@]}" -name "DejaVuSans.ttf" 2>/dev/null | head -1)
    "$VENV_PY" -c "
from PIL import ImageFont
try:
    f = ImageFont.truetype('$FONT_PATH', 28)
    print('Pillow font load: OK')
except Exception as e:
    print(f'Pillow font load FAILED: {e}')
    exit(1)
" && ok "Pillow can load DejaVuSans.ttf" || { error "Pillow font load failed"; IMPORT_ERRORS=$((IMPORT_ERRORS + 1)); }
fi

# Check for DSEG 7-segment font (used by configs/seven-segment.yaml)
DSEG_SEARCH_DIRS=(
    /usr/share/fonts/truetype/dseg
    /usr/share/fonts/truetype
    /usr/share/fonts
    "$SCRIPT_DIR/third_party/dseg"
)
DSEG_FOUND=false
for d in "${DSEG_SEARCH_DIRS[@]}"; do
    if [[ -f "$d/DSEG7Classic-Regular.ttf" ]]; then
        ok "DSEG7Classic-Regular.ttf found at $d/DSEG7Classic-Regular.ttf"
        DSEG_FOUND=true
        break
    fi
done

if [[ "$DSEG_FOUND" == false ]]; then
    warn "DSEG7Classic-Regular.ttf not found — seven-segment.yaml and fourteen-segment.yaml will not work."
    info "It should have been installed above via fonts-dseg."
    info "If it's missing, try:  sudo apt install fonts-dseg"
fi

# Check for Nixie One font (used by configs/nixie.yaml)
NIXIE_FOUND=false
NIXIE_SEARCH_DIRS=(
    "$SCRIPT_DIR/third_party/nixie"
    /usr/share/fonts/truetype
    /usr/share/fonts
)
for d in "${NIXIE_SEARCH_DIRS[@]}"; do
    if [[ -f "$d/NixieOne-Regular.ttf" ]]; then
        ok "NixieOne-Regular.ttf found at $d/NixieOne-Regular.ttf"
        NIXIE_FOUND=true
        break
    fi
done

if [[ "$NIXIE_FOUND" == false ]]; then
    warn "NixieOne-Regular.ttf not found — nixie.yaml will not work."
    info "Install with:  bash scripts/download-nixie-font.sh"
fi

# ---------------------------------------------------------------------------
# 8. Helper scripts
# ---------------------------------------------------------------------------
section "Helper scripts"

# SCRIPT_DIR is the project root (install.sh now lives at the repo root).
# Both helper scripts are committed to the repo — just ensure they're executable.
chmod +x "$SCRIPT_DIR/run-clockish.sh"
ok "run-clockish.sh is executable"

chmod +x "$SCRIPT_DIR/edit-clockish-config.sh"
ok "edit-clockish-config.sh is executable"

chmod +x "$SCRIPT_DIR/uninstall.sh"
ok "uninstall.sh is executable"

# ---------------------------------------------------------------------------
# 9. Summary
# ---------------------------------------------------------------------------
section "Installation Summary"

if [[ $IMPORT_ERRORS -eq 0 ]]; then
    ok "All checks passed!"
else
    warn "$IMPORT_ERRORS import check(s) failed — see errors above."
fi

echo ""
echo -e "${BOLD}Your config file:${RESET}"
echo "    $HOME/.config/clockish/clockish-config.yaml"
echo ""
echo -e "${BOLD}To customise the display layout:${RESET}"
echo "    ./edit-clockish-config.sh"
echo "    # or open any file from configs/ and copy it to your config location"
echo ""
echo -e "${BOLD}To run the display once (for testing):${RESET}"
echo "    ./run-clockish.sh"
echo "    ./run-clockish.sh --debug"
echo "    ./run-clockish.sh configs/big-red.yaml   # try a specific layout"
echo ""
echo -e "${BOLD}To install as a systemd service (auto-start on boot):${RESET}"
echo "    ./run-clockish.sh --install-service"
echo "    # or with a specific config:"
echo "    ./run-clockish.sh --install-service configs/big-red.yaml"
echo ""

if [[ "${NEEDS_REBOOT:-false}" == true ]]; then
    echo -e "${YELLOW}${BOLD}*** A REBOOT IS REQUIRED for SPI and/or group changes to take effect. ***${RESET}"
    echo "    sudo reboot"
    echo ""
fi

echo -e "${BOLD}Troubleshooting tips:${RESET}"
echo "  • SPI not working?      sudo raspi-config nonint do_spi 0 && sudo reboot"
echo "  • Permission denied?    sudo usermod -aG spi,gpio \$USER  then reboot"
echo "  • Font errors?          sudo apt install fonts-dejavu-core
  • 7-seg font missing?   sudo apt install fonts-dseg
  • Nixie font missing?   bash scripts/download-nixie-font.sh"
echo "  • RPi.GPIO missing?     source .venv/bin/activate && pip install rpi-lgpio"
echo "  • numpy/Pillow slow?    sudo apt install python3-numpy  (then re-run install.sh)"
echo "  • numpy missing?        source .venv/bin/activate && pip install 'numpy>=2.4'"
echo "  • Service not starting? sudo journalctl -u clockish -n 50"
echo ""

