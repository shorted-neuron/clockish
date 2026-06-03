# Deploying clockish to a Raspberry Pi

## Prerequisites on the Pi

### 1. Enable SPI

SPI must be on before any of this will work.

```bash
sudo raspi-config
# → Interface Options → SPI → Enable
# Reboot if prompted.
```

Verify it's on:
```bash
ls /dev/spidev*
# Should show: /dev/spidev0.0  /dev/spidev0.1
```

### 2. Python 3.9+

```bash
python3 --version   # must be 3.9 or newer
# Raspberry Pi OS Bookworm and Ubuntu 22.04+ ship Python 3.11+ — you're fine.
# Pi OS Bullseye ships 3.9 — also fine.
# If you're on Buster (3.7), upgrade the OS first.
```

### 3. git and pip

```bash
sudo apt update
sudo apt install -y git python3-pip python3-venv
```

---

## Option A — Deploy from Windows using rsync (recommended for active dev)

From a Linux shell on Windows (Git Bash, WSL, etc.):

```bash
# Substitute your actual username and hostname:
bash scripts/deploy.sh youruser@raspberrypi.local

# Or set it as an env variable:
export PI_HOST=youruser@192.168.1.42
bash scripts/deploy.sh
```

This rsyncs the project, creates a venv on the Pi if needed, and runs
`pip install -e .`.

---

## Option B — Clone directly on the Pi

```bash
# On the Pi:
git clone <your-repo-url> ~/clockish
cd ~/clockish

python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install -e .
```

---

## Copying your config file

The display reads a YAML config file.  The examples are in `configs/`.
Copy the one closest to your setup to the Pi:

```bash
# From Windows (PowerShell) — substitute your username and hostname:
scp configs/clockish.yaml youruser@raspberrypi.local:~/clockish/configs/

# Or on the Pi after cloning — configs/ is already there.
```

The app searches for the config in this order:
1. Path you pass on the command line  ← **use this for testing**
2. `configs/clockish-config.yaml` relative to the project root
3. `~/.config/clockish/clockish-config.yaml`
4. `~/clockish-config.yaml`

---

## Running the display

```bash
# On the Pi, in the project directory with the venv active:
source .venv/bin/activate

# Run with explicit config (safest during testing):
clockish configs/clockish.yaml

# Or use the installed CLI command (after pip install -e .):
clockish configs/clockish.yaml

# Debug mode — prints per-frame timing:
clockish --debug configs/clockish.yaml

# Debug-layout mode — renders one frame, prints layout info, then exits:
# (great for checking your config without running the full loop)
clockish --debug-layout configs/clockish.yaml
```

---

## Troubleshooting

### `ModuleNotFoundError: No module named 'RPi'`

The Pi-only packages are installed automatically on Pi hardware.
If you see this on the Pi, double-check you activated the venv:
```bash
source .venv/bin/activate
which python   # should show .venv/bin/python
```

### `FileNotFoundError: /dev/spidev0.0`

SPI is not enabled.  Run `sudo raspi-config` → Interface Options → SPI.

### `PermissionError: /dev/spidev0.0`

Your user isn't in the `spi` group:
```bash
sudo usermod -aG spi $USER
# Log out and back in, then retry.
```

### `RuntimeError: No access to /dev/mem`

GPIO access issue.  Either run with `sudo` (not recommended for production)
or install `rpi-lgpio` which uses the modern `/dev/gpiochip` interface
and doesn't need root:
```bash
pip install rpi-lgpio   # already in pyproject.toml — should be installed
```

### Display shows garbage / wrong colours

Check `rotation` in your config file matches how the display is physically mounted.
Valid values: 0, 90, 180, 270.

### Config file not found

Pass it explicitly:
```bash
clockish /full/path/to/clockish.yaml
```

---

## Running as a service (after testing is working)

Create `/etc/systemd/system/clockish.service`:

```ini
[Unit]
Description=clockish LCD display
After=network-online.target
Wants=network-online.target

[Service]
User=youruser
WorkingDirectory=/home/youruser/clockish
ExecStart=/home/youruser/clockish/.venv/bin/clockish /home/youruser/clockish/configs/clockish-config.yaml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable clockish
sudo systemctl start clockish
sudo systemctl status clockish
```

---

## Updating the app from Windows

After making changes:

```bash
# From Windows (Git Bash / WSL):
bash scripts/deploy.sh youruser@raspberrypi.local

# The deploy script rsyncs changes and re-runs pip install -e .
# No need to restart the venv; if running as a service, restart it:
ssh youruser@raspberrypi.local "sudo systemctl restart clockish"
```
