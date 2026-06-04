#!/usr/bin/env python3
"""render_preview.py — Render each panel-config to a PNG file without hardware.

Usage:
    python render_preview.py [--outdir docs/previews] [config ...]

If no config paths are given, all YAML files under configs/ are rendered.
Output PNGs are written to --outdir (default: docs/previews/).

The script stubs out every hardware dependency (RPi.GPIO, spidev, ILI9486)
so it runs on any platform with Python 3.11+ and Pillow installed.
"""

import argparse
import datetime
import os
import sys
import types

# ---------------------------------------------------------------------------
# Locate repo root and add src/ to path.
# This file lives at src/clockish/render_preview.py, so the repo root is
# two directories up.
# ---------------------------------------------------------------------------
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
_SRC  = os.path.join(_REPO, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# ---------------------------------------------------------------------------
# Stub hardware modules BEFORE importing clockish
# ---------------------------------------------------------------------------

# --- RPi.GPIO stub ---
_gpio_mod = types.ModuleType("RPi.GPIO")
_gpio_mod.BCM = 11
_gpio_mod.setmode = lambda *a, **kw: None
_gpio_mod.setup   = lambda *a, **kw: None
_gpio_mod.output  = lambda *a, **kw: None
_rpi_mod = types.ModuleType("RPi")
_rpi_mod.GPIO = _gpio_mod
sys.modules["RPi"]      = _rpi_mod
sys.modules["RPi.GPIO"] = _gpio_mod

# --- spidev stub ---
class _SpiDevStub:
    mode = 0
    max_speed_hz = 0
    def __init__(self, *a, **kw): pass
    def close(self): pass
    def xfer2(self, *a, **kw): return []

_spidev_mod = types.ModuleType("spidev")
_spidev_mod.SpiDev = _SpiDevStub
sys.modules["spidev"] = _spidev_mod

# --- pyili9486 stub (replaces ILI9486 — display.py imports from pyili9486) ---
class _SKUStub:
    MPI3501 = 'MPI3501'
    MHS3528 = 'MHS3528'

class _OriginStub:
    UPPER_LEFT  = 0
    UPPER_RIGHT = 1
    LOWER_RIGHT = 2
    LOWER_LEFT  = 3

class _ILI9486Stub:
    """No-op LCD that just absorbs display() calls."""
    def __init__(self, *a, **kw): pass
    def begin(self):        return self
    def display(self, img): pass
    def idle(self, *a, **kw): pass
    @property
    def is_landscape(self): return False
    @property
    def dimensions(self):   return (320, 480)

class _RPiLGPIOFacadeStub:
    def __init__(self, *a, **kw): pass

_pyili_mod = types.ModuleType("pyili9486")
_pyili_mod.ILI9486 = _ILI9486Stub
_pyili_mod.Origin  = _OriginStub
_pyili_mod.SKU     = _SKUStub

_pyili_gpio_mod         = types.ModuleType("pyili9486.gpio")
_pyili_gpio_facade_mod  = types.ModuleType("pyili9486.gpio.rpilgpio_facade")
_pyili_gpio_facade_mod.RPiLGPIOFacade = _RPiLGPIOFacadeStub

sys.modules["pyili9486"]                        = _pyili_mod
sys.modules["pyili9486.gpio"]                   = _pyili_gpio_mod
sys.modules["pyili9486.gpio.rpilgpio_facade"]   = _pyili_gpio_facade_mod

# --- yaml stub (only if not installed) ---
try:
    import yaml  # noqa: F401
except ImportError:
    import json as _json
    _yaml_mod = types.ModuleType("yaml")
    _yaml_mod.safe_load = _json.loads
    sys.modules["yaml"] = _yaml_mod

# ---------------------------------------------------------------------------
# Stub /proc files for system-info helpers
# ---------------------------------------------------------------------------
import builtins as _builtins
_real_open = _builtins.open

_PROC_STUBS: dict[str, str] = {
    "/proc/uptime":    "123456.78 234567.89\n",
    "/proc/stat":      "cpu  100 0 50 800 20 5 5 0 0 0\n",
    "/proc/meminfo":   "MemTotal:        4096000 kB\nMemAvailable:    2048000 kB\n",
    # /proc/net/wireless: two header lines then one data line per interface.
    # Columns (after split): 0=iface 1=status 2=quality 3=signal(dBm) 4=noise
    # quality "57." → 57/70  signal "-57." → -57 dBm (3 of 4 bars in the graphic)
    "/proc/net/wireless": (
        "Inter-| sta-|   Quality        |   Discarded packets               | Missed | WEP\n"
        " face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | mode\n"
        "  wlan0: 0000   57.  -57.  -256        0      0      0      0      0        0\n"
    ),
    "/sys/class/thermal/thermal_zone0/temp": "42000\n",
    "/sys/class/net/wlan0/operstate": "up\n",
    "/run/systemd/timesync/synchronized": "",
}

import io as _io

class _StubFile:
    """A file-like object backed by a static string."""
    def __init__(self, content: str):
        self._buf = _io.StringIO(content)

    def __enter__(self):        return self
    def __exit__(self, *a):     pass
    def read(self):             return self._buf.read()
    def readline(self):         return self._buf.readline()
    def readlines(self):        return self._buf.readlines()
    def __iter__(self):         return iter(self._buf.readlines())

def _patched_open(name, *args, **kwargs):
    if isinstance(name, str) and name in _PROC_STUBS:
        return _StubFile(_PROC_STUBS[name])
    return _real_open(name, *args, **kwargs)

_builtins.open = _patched_open

# ---------------------------------------------------------------------------
# Stub subprocess.check_output for wgstatus / chronyc / timedatectl / iwgetid
# ---------------------------------------------------------------------------
import subprocess as _subprocess
_real_check_output = _subprocess.check_output

def _patched_check_output(cmd, *args, **kwargs):
    cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
    if "wgstatus" in cmd_str:
        return b"wg: stub"
    if "chronyc" in cmd_str:
        return b"2 sources online\n0 sources offline\n"
    if "timedatectl" in cmd_str:
        return b"NTP=yes\nNTPSynchronized=yes\n"
    if "iwgetid" in cmd_str:
        return b"Preview\n"
    return _real_check_output(cmd, *args, **kwargs)

_subprocess.check_output = _patched_check_output

# ---------------------------------------------------------------------------
# Patch argparse so clockish's module-level _parser.parse_args()
# returns safe defaults regardless of our own sys.argv.
# ---------------------------------------------------------------------------
import argparse as _argparse
_real_parse_args = _argparse.ArgumentParser.parse_args

def _safe_parse_args(self, args=None, namespace=None):
    # Let clockish's parser receive an empty args list.
    return _real_parse_args(self, args=[], namespace=namespace)

_argparse.ArgumentParser.parse_args = _safe_parse_args


# ---------------------------------------------------------------------------
# Patch ImageFont.truetype to fall back to a system font on non-Linux platforms.
# This handles the DejaVuSans.ttf lookup failing on Windows/macOS.
# ---------------------------------------------------------------------------
from PIL import ImageFont as _ImageFont

_real_truetype = _ImageFont.truetype

# Ordered list of fallback fonts to try when the requested path fails.
_FALLBACK_FONTS = [
    # Windows
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\consola.ttf",
    r"C:\Windows\Fonts\cour.ttf",
    # macOS
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    # Linux extras
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
]

def _patched_truetype(font=None, size=10, *args, **kwargs):
    try:
        return _real_truetype(font, size, *args, **kwargs)
    except (OSError, IOError):
        for fb in _FALLBACK_FONTS:
            if os.path.isfile(fb):
                return _real_truetype(fb, size, *args, **kwargs)
        # Last resort: PIL default bitmap font (no size)
        return _ImageFont.load_default()

_ImageFont.truetype = _patched_truetype

# ---------------------------------------------------------------------------
# Now import clockish.display as a regular module.
# All hardware stubs are already in sys.modules so no physical hardware is
# touched.  A standard import never executes the `if __name__ == '__main__'`
# block, so the main loop does not run.
# ---------------------------------------------------------------------------
import clockish.display as _ppd

# Restore parse_args (cleanup)
_argparse.ArgumentParser.parse_args = _real_parse_args

# ---------------------------------------------------------------------------
# Post-load patches for non-Linux platform compatibility
# ---------------------------------------------------------------------------
import platform as _platform

if _platform.system() != "Linux":
    _NO_PAD_INLINE = {
        '%-d': '%d', '%-m': '%m', '%-H': '%H', '%-I': '%I',
        '%-M': '%M', '%-S': '%S', '%-j': '%j', '%-y': '%y',
    }
    # Fix TIME_FORMATS: remove Linux-only %-I (no-pad hour)
    if hasattr(_ppd, '_TIME_FORMATS'):
        for k, v in _ppd._TIME_FORMATS.items():
            for src, dst in _NO_PAD_INLINE.items():
                v = v.replace(src, dst)
            _ppd._TIME_FORMATS[k] = v

    # os.getloadavg is not available on Windows — stub it
    import os as _os
    if not hasattr(_os, 'getloadavg'):
        _os.getloadavg = lambda: (0.0, 0.0, 0.0)

# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

# Linux strftime %-X (no-pad) directives → padded equivalents for Windows/macOS
_NO_PAD_MAP = {
    '%-d': '%d', '%-m': '%m', '%-H': '%H', '%-I': '%I',
    '%-M': '%M', '%-S': '%S', '%-j': '%j', '%-y': '%y',
}

def _normalize_strftime_formats(obj) -> None:
    """Walk config tree replacing Linux-only %-X strftime directives in-place."""
    _FORMAT_KEYS = {'date_format', 'time_format'}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _FORMAT_KEYS and isinstance(v, str):
                for src, dst in _NO_PAD_MAP.items():
                    v = v.replace(src, dst)
                obj[k] = v
            else:
                _normalize_strftime_formats(v)
    elif isinstance(obj, list):
        for item in obj:
            _normalize_strftime_formats(item)


def render_config(config_path: str, out_path: str) -> None:
    """Load a YAML config, render one frame, and save to out_path (PNG)."""
    import yaml
    from PIL import Image, ImageDraw
    import platform

    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    # On non-Linux platforms, %-d etc. are not supported by strftime.
    # Normalize them to the zero-padded equivalents (%d etc.) in format strings.
    if platform.system() != "Linux":
        _normalize_strftime_formats(cfg)

    # Resolve colors in the loaded config (same as production code)
    cfg_copy = cfg.copy()
    _ppd._resolve_colors(cfg_copy)

    disp   = cfg_copy.get("display", {})
    w      = disp.get("width",  320)
    h      = disp.get("height", 480)

    img  = Image.new("RGB", (w, h))
    drw  = ImageDraw.Draw(img)
    drw.rectangle((0, 0, w, h), fill=0)

    # Build timezone cache
    tz_cache: dict = {}
    for r in cfg_copy.get("rows", []):
        for p in r.get("panels", []):
            if p.get("type") in ("clock", "date"):
                tz = p.get("timezone", "local")
                if tz not in tz_cache:
                    tz_cache[tz] = _ppd._now_in_tz(tz)

    layout = _ppd._measure_rows(cfg_copy.get("rows", []))

    timings: dict = {}
    t0 = 0.0   # not timing here; debug panel will show 0 ms — acceptable

    for row_idx, (r, ry, rh) in enumerate(layout):
        panels = r.get("panels", [])
        if not panels:
            continue

        row_bg  = r.get("background", _ppd._C_BLACK)
        row_img = Image.new("RGB", (w, rh), row_bg)
        row_drw = ImageDraw.Draw(row_img)

        widths = _ppd._resolve_panel_widths(panels, w, row_idx)
        px = 0
        for p, pw in zip(panels, widths):
            _ppd._dispatch_panel(p, px, 0, pw, rh, tz_cache, timings, t0, row_drw)
            px += pw

        img.paste(row_img, (0, ry))

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    img.save(out_path)
    print(f"  Saved: {out_path}  ({w}x{h})")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render clockish panel-configs to PNG preview images."
    )
    parser.add_argument(
        "--outdir", default=os.path.join(_REPO, "docs", "previews"),
        help="Directory to write PNG files into (default: docs/previews/)"
    )
    parser.add_argument(
        "configs", nargs="*",
        help="YAML config files to render (default: all files in configs/)"
    )
    args = parser.parse_args()

    configs = args.configs
    if not configs:
        panel_cfg_dir = os.path.join(_REPO, "configs")
        configs = sorted(
            os.path.join(panel_cfg_dir, f)
            for f in os.listdir(panel_cfg_dir)
            if f.endswith(".yaml") or f.endswith(".yml")
        )

    if not configs:
        print("No config files found.", file=sys.stderr)
        sys.exit(1)

    print(f"Rendering {len(configs)} config(s) → {args.outdir}/")
    for cfg_path in configs:
        name    = os.path.splitext(os.path.basename(cfg_path))[0]
        out     = os.path.join(args.outdir, f"{name}.png")
        print(f"  [{name}]")
        try:
            render_config(cfg_path, out)
        except Exception as exc:
            print(f"  ERROR rendering {cfg_path}: {exc}", file=sys.stderr)
            import traceback; traceback.print_exc()


if __name__ == "__main__":
    main()

