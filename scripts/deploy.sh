#!/usr/bin/env bash
# deploy.sh — copy the project to a Raspberry Pi and (re)install it.
#
# Usage (from your Windows terminal via WSL, or from another Linux machine):
#   bash scripts/deploy.sh user@raspberrypi.local
#
# Or set PI_HOST in your environment:
#   export PI_HOST=dilbert@192.168.1.42
#   bash scripts/deploy.sh
#
# Requirements on the Pi: Python 3.9+, pip, ssh server running.

set -euo pipefail

PI_HOST="${1:-${PI_HOST:-}}"

if [[ -z "$PI_HOST" ]]; then
    echo "ERROR: no target host supplied."
    echo "Usage: $0 user@hostname"
    echo "  or:  PI_HOST=user@hostname $0"
    exit 1
fi

# ~ is expanded by the remote shell; $HOME in a quoted string is not.
REMOTE_DIR='~/clockish'

echo "==> Deploying to ${PI_HOST}:~/clockish"

# rsync: exclude Windows/dev artifacts, keep the src/ and third_party/ trees
rsync -avz --progress \
    --exclude='.git/' \
    --exclude='.venv/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='.idea/' \
    --exclude='dist/' \
    --exclude='build/' \
    --exclude='*.egg-info/' \
    . "${PI_HOST}:${REMOTE_DIR}"

echo "==> Installing / upgrading on the Pi"
ssh "${PI_HOST}" bash <<'REMOTE'
set -euo pipefail
cd ~/clockish

# Create venv if missing
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi

source .venv/bin/activate
pip install --upgrade pip
pip install -e .
echo "==> Done."
echo "    To run:  source ~/clockish/.venv/bin/activate"
echo "             clockish ~/clockish/configs/clockish-config.yaml"
REMOTE
