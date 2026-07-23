#!/usr/bin/env python3
"""render_preview.py  --  Render each panel-config to a PNG file without hardware.

Usage:
    python render_preview.py [--outdir docs/previews] [config ...]

Every config renders TWICE, to two different destinations:
  {outdir}/{name}.png       -- 'live' render: real current time/date/cpu%/uptime
                                (hostname/IP/wifi SSID always mocked, see below).
                                Tracked in git; overwritten on each run so a
                                tagged release ships previews that look "now".
  {outdir}/mock/{name}.png  -- 'mock' render: fixed worst-case-width time/date
                                and cpu=100%/huge uptime, for spotting layout
                                regressions via a stable, comparable diff.
                                Gitignored -- local dev/review artifact only.

If no config paths are given, all YAML files under configs/ are rendered.

The script stubs out every hardware dependency (RPi.GPIO, spidev, ILI9486)
so it runs on any platform with Python 3.11+ and Pillow installed.
"""

import argparse
import datetime
import os
import sys
import time
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

# --- pyili9486 stub (replaces ILI9486  --  display.py imports from pyili9486) ---
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
    # total=960 MB (983040 kB), available=648 MB (663552 kB) -> used=312 MB
    "/proc/meminfo":   "MemTotal:         983040 kB\nMemAvailable:     663552 kB\n",
    # /proc/net/wireless: two header lines then one data line per interface.
    # Columns (after split): 0=iface 1=status 2=quality 3=signal(dBm) 4=noise
    # quality "57." -> 57/70  signal "-57." -> -57 dBm (3 of 4 bars in the graphic)
    "/proc/net/wireless": (
        "Inter-| sta-|   Quality        |   Discarded packets               | Missed | WEP\n"
        " face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | mode\n"
        "  wlan0: 0000   57.  -57.  -256        0      0      0      0      0        0\n"
    ),
    "/sys/class/thermal/thermal_zone0/temp": "42000\n",
    "/sys/class/net/wlan0/operstate": "up\n",
    "/run/systemd/timesync/synchronized": "",
}

# /proc/uptime and /proc/stat feed get_uptime_str()/get_cpu_percent() -- the
# only two facts that go "live" (real host value) in non-mock rendering; every
# other stub above always stays faked (hostname/IP/wifi SSID mocking is a
# separate, unconditional patch below -- see 'Preview data patches').
_LIVE_REAL_PROC_PATHS = ("/proc/uptime", "/proc/stat")
_ORIGINAL_PROC_STUBS = dict(_PROC_STUBS)

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

# Capture the real implementations BEFORE any mocking below overwrites them --
# 'live' mode (see _set_render_mode()) restores these instead of using canned
# values, so it shows this host's actual current CPU%/uptime.
_REAL_get_cpu_percent = _ppd.get_cpu_percent
_REAL_get_uptime_str  = _ppd.get_uptime_str

# ---------------------------------------------------------------------------
# Preview data patches  --  replace live system calls with canned values so
# every preview looks realistic regardless of the host machine.
#
# hostname / IP / wifi SSID (via /proc/net/wireless + iwgetid stubs above):
# ALWAYS mocked, in both 'mock' and 'live' render modes -- these don't vary
# in any way worth showing "live", and mocking them keeps previews usable on
# a dev machine with no real network state to report.
# ---------------------------------------------------------------------------
_ppd.get_ip_address      = lambda: "192.168.1.42"
_ppd.get_hostname        = lambda: "raspberrypi"
# get_cpu_percent() diffs two /proc/stat reads; the static stub makes delta=0
# so it always returns 0.0  --  override it directly instead.
_ppd.get_cpu_percent     = lambda: 42.0
# disp= value in the debug panel comes from this module-level variable.
_ppd._last_display_ms    = 198.0
# _FONT_PATH is normally set by _init() (never called in preview); _init_layout()
# and _get_font() both fall back to it for the 'debug' font key and default typeface.
_ppd._FONT_PATH          = _ppd._find_font('DejaVuSans.ttf')
# _args is normally set by _init() from argparse; the 'config_file' fact source
# reads _args.config directly, so it crashes (AttributeError on None) if left unset.
_ppd._args               = types.SimpleNamespace(config=None)

# Fixed "now" for every clock/date panel (all timezones) in 'mock' mode,
# instead of the real wall-clock time -- makes mock renders deterministic AND
# exercises worst-case width/ink for time_format/date_format strings, so
# overlap/clipping/vertical-centring problems are visible on every run, not
# just whenever someone happens to render at a lucky moment:
#   22:08:08  -- hour=22 is the sweet spot: 24h/24hs formats (%H, always
#                zero-padded) render any hour as 2 digits regardless, but
#                no-pad 12h formats (%-I, e.g. nixie.yaml/big-red.yaml's
#                "%-I:%M") render hour 1-9 as ONE digit -- narrower than
#                10/11/12. hour=22 -> 12h hour = 22-12 = 10, so BOTH formats
#                get their widest (2-digit) hour from this single value.
#                (hour=20 looked right for 24h but 12h-collapsed to "8:08",
#                narrower than "10:08" -- the bug this constant now avoids.)
#   Wednesday, December 20 2028 -- longest weekday name + longest month name,
#                and "Wednesday"'s 'y' exercises descender-ink clipping/
#                centring (the reason clip_numeric font_behavior exists).
_PREVIEW_NOW = datetime.datetime(2028, 12, 20, 22, 8, 8)

#: Mock-mode uptime -- deliberately huge/wide to exercise the same
#: worst-case-width idea as _PREVIEW_NOW, for the 'uptime' fact source.
#: Matches get_uptime_str()'s real "up {d}d {h}h {m}m" format.
_MOCK_UPTIME_STR = "up 804d 20h 46m"


def _set_render_mode(mock: bool) -> None:
    """Toggle cpu%/uptime facts and /proc stub coverage for this render.

    mock=True:  cpu=100.0, huge fixed uptime string, /proc/uptime+/proc/stat
                stay faked (deterministic, worst-case width).
    mock=False: real get_cpu_percent()/get_uptime_str(), and /proc/uptime +
                /proc/stat are UNstubbed so they read this host's real files.
    """
    if mock:
        _ppd.get_cpu_percent = lambda: 100.0
        _ppd.get_uptime_str  = lambda: _MOCK_UPTIME_STR
        for path in _LIVE_REAL_PROC_PATHS:
            _PROC_STUBS[path] = _ORIGINAL_PROC_STUBS[path]
    else:
        _ppd.get_cpu_percent = _REAL_get_cpu_percent
        _ppd.get_uptime_str  = _REAL_get_uptime_str
        for path in _LIVE_REAL_PROC_PATHS:
            _PROC_STUBS.pop(path, None)


def _config_uses_cpu_fact(cfg: dict) -> bool:
    """True if any panel in cfg is a 'fact' panel with source: cpu."""
    for r in cfg.get("rows", []) or []:
        for p in r.get("panels", []) or []:
            if p.get("type") == "fact" and p.get("source") == "cpu":
                return True
    return False


def _prime_real_cpu_percent_if_used(cfg: dict) -> None:
    """Prime a genuine ~1s CPU-usage delta before rendering, in 'live' mode.

    get_cpu_percent() computes usage as a delta between two /proc/stat reads,
    cached for 1s (see display.py). A single render calls it exactly once, so
    without priming it would report the average utilisation since boot (from
    the (0, 0) baseline) instead of anything resembling "current". Only pay
    the ~1s cost when a config actually shows a cpu fact.
    """
    if not _config_uses_cpu_fact(cfg):
        return
    _ppd.get_cpu_percent()          # discard: seeds _cpu_stat_prev + cache_time
    time.sleep(1.05)                # clear the 1s cache gate in display.py
    # The next call (during the real render below) returns a genuine delta.


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

    # os.getloadavg is not available on Windows  --  stub it
    import os as _os
    if not hasattr(_os, 'getloadavg'):
        _os.getloadavg = lambda: (0.0, 0.0, 0.0)

# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

# Linux strftime %-X (no-pad) directives -> padded equivalents for Windows/macOS
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


def _resolve_preview_dimensions(cfg: dict, config_path: str) -> tuple[int, int]:
    """Resolve (width, height) for a preview render, matching production as closely
    as possible while covering dev machines that have no real display profile.

    Priority:
      1. Inline 'display:' section in the config itself (ground truth, same as prod).
      2. Real display profile file  --  configs/display.yaml alongside the config,
         or ~/.config/clockish/display.yaml (installed by install.sh). Rare on dev
         machines but matches production exactly when present.
      3. 'preview_size: "WxH"' hint  --  for configs built for a specific non-standard
         target (e.g. small.yaml's 240x135 ST7789) where the orientation fallback
         below would be wrong.
      4. 'orientation:' fallback  --  landscape -> 480x320, else -> 320x480.
    """
    disp = cfg.get("display")
    if isinstance(disp, dict) and "width" in disp and "height" in disp:
        return disp["width"], disp["height"]

    profile_path = _ppd._find_display_profile(config_path)
    if profile_path:
        import yaml
        with open(profile_path) as f:
            profile = yaml.safe_load(f) or {}
        profile_disp = profile.get("display", {})
        if "width" in profile_disp and "height" in profile_disp:
            return profile_disp["width"], profile_disp["height"]

    preview_size = cfg.get("preview_size")
    if isinstance(preview_size, str) and "x" in preview_size:
        try:
            w_str, h_str = preview_size.split("x", 1)
            return int(w_str), int(h_str)
        except ValueError:
            pass

    return (480, 320) if cfg.get("orientation") == "landscape" else (320, 480)


def render_config(config_path: str, out_path: str, mock: bool) -> None:
    """Load a YAML config, render one frame, and save to out_path (PNG).

    mock=True:  fixed worst-case time/date, cpu=100%, huge fixed uptime.
    mock=False: real current time/date (per panel timezone) and this host's
                real cpu%/uptime (hostname/IP/wifi SSID still always mocked).
    """
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

    _set_render_mode(mock)
    if not mock:
        _prime_real_cpu_percent_if_used(cfg_copy)

    w, h = _resolve_preview_dimensions(cfg_copy, config_path)

    # Sync the display module's globals so _get_font()'s named-scale fractions
    # (giant/huge/big/.../micro) and _init_layout()'s row-relative sizing are
    # computed against THIS config's canvas, not whatever the previous config
    # in the batch happened to leave behind.
    _ppd.width  = w
    _ppd.height = h
    _ppd.top    = 0
    _ppd.bottom = h

    # Fully clear the font cache  --  named scales are lazily computed from
    # module height/width on first use and cached forever; a partial eviction
    # (custom names only) leaves stale, wrongly-scaled built-in fonts behind
    # when configs with different canvas sizes render in the same process.
    _ppd._FONTS.clear()
    # Also reset the load-once flag so each config's named-scale fonts
    # (giant/normal/...) get reloaded fresh, not skipped.
    _ppd._SCALE_FONTS_LOADED = False

    _ppd._config = cfg_copy
    # 'config_file' fact source reads _args.config directly.
    _ppd._args.config = config_path

    # Run the real layout pass  --  resolves font: / font_size: auto into
    # row-relative synthetic font entries and pre-computes panel widths,
    # exactly as production _init() does. Keeps preview 1:1 with the real
    # renderer instead of a parallel reimplementation that can drift.
    _ppd._init_layout()

    img  = Image.new("RGB", (w, h))
    drw  = ImageDraw.Draw(img)
    drw.rectangle((0, 0, w, h), fill=0)

    # Build timezone cache. 'mock' mode maps every timezone to the same fixed
    # _PREVIEW_NOW (deterministic, worst-case-width). 'live' mode uses each
    # panel's real current time in its own timezone, like production does.
    tz_cache: dict = {}
    for r in cfg_copy.get("rows", []):
        for p in r.get("panels", []):
            if p.get("type") in ("clock", "date"):
                tz = p.get("timezone", "local")
                if tz not in tz_cache:
                    tz_cache[tz] = _PREVIEW_NOW if mock else _ppd._now_in_tz(tz)

    timings: dict = {'ntp': 0.001, 'tz': 0.0, 'draw': 0.076}
    # Back-date t0 so the debug panel's prep= value shows a realistic 91 ms.
    t0 = time.perf_counter() - 0.091

    for r, ry, rh in _ppd._LAYOUT:
        panels = r.get("panels", [])
        if not panels:
            continue

        row_bg  = r.get("background", _ppd._C_BLACK)
        row_img = Image.new("RGB", (w, rh), row_bg)
        row_drw = ImageDraw.Draw(row_img)

        widths = r.get('_widths', [])
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
        help="Base directory for live renders (default: docs/previews/); "
             "mock renders go to {outdir}/mock/."
    )
    parser.add_argument(
        "--skip-live", action="store_true",
        help="Skip the 'live' (real time/cpu/uptime) render -- mock only.",
    )
    parser.add_argument(
        "--skip-mock", action="store_true",
        help="Skip the 'mock' (worst-case, deterministic) render -- live only.",
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

    mock_dir = os.path.join(args.outdir, "mock")
    modes = []
    if not args.skip_live:
        modes.append((False, args.outdir))
    if not args.skip_mock:
        modes.append((True, mock_dir))

    print(f"Rendering {len(configs)} config(s) x {len(modes)} mode(s) -> {args.outdir}/")
    for cfg_path in configs:
        name = os.path.splitext(os.path.basename(cfg_path))[0]
        print(f"  [{name}]")
        for mock, out_dir in modes:
            out = os.path.join(out_dir, f"{name}.png")
            try:
                render_config(cfg_path, out, mock=mock)
            except Exception as exc:
                label = "mock" if mock else "live"
                print(f"  ERROR rendering {cfg_path} ({label}): {exc}", file=sys.stderr)
                import traceback; traceback.print_exc()


if __name__ == "__main__":
    main()
