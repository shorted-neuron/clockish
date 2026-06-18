#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import argparse
import time
import subprocess
import socket
import shutil
import datetime
import functools
import zoneinfo
import yaml
from contextlib import contextmanager
from PIL import Image, ImageDraw, ImageFont
import PIL.ImageOps
from clockish.colors import rgb_to_hex, BY_NAME
from clockish.drivers import load_driver
from clockish import __version__


# ---------------------------------------------------------------------------
# Debug flag — set by -d / --debug command-line argument
# ---------------------------------------------------------------------------
def _find_default_config() -> str:
    """Search for clockish.yaml in order of preference."""
    candidates = [
        # 1. Next to this file (old fork layout — kept for dev convenience)
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'clockish.yaml'),
        # 2. configs/ directory relative to project root (clockish src-layout)
        # __file__ is src/clockish/display.py → go up 2 levels to reach the project root.
        os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'configs', 'clockish.yaml'),
        # 3. User config directory
        os.path.expanduser('~/.config/clockish/clockish.yaml'),
        # 4. Home directory (simple deployment)
        os.path.expanduser('~/clockish.yaml'),
    ]
    for path in candidates:
        if os.path.isfile(os.path.normpath(path)):
            return os.path.normpath(path)
    # Return the project-root path as the default even if it doesn't exist yet,
    # so the error message shows a meaningful path.
    return os.path.normpath(candidates[1])

_DEFAULT_CONFIG = _find_default_config()

_parser = argparse.ArgumentParser(
    description='Pi Panel Display — config-driven LCD dashboard',
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog='Example: clockish.py --debug my-config.yaml'
)
_parser.add_argument('-d', '--debug', action='store_true', default=False,
                     help='Print per-frame timing to stdout')
_parser.add_argument('--debug-layout', action='store_true', default=False,
                     help='Print row/panel layout info, render one frame, then exit')
_parser.add_argument('config', nargs='?', default=None,
                     metavar='CONFIG',
                     help='Path to YAML config file (default: %(const)s)',
                     const=_DEFAULT_CONFIG)
_args = _parser.parse_args()
DEBUG = _args.debug
DEBUG_LAYOUT = _args.debug_layout


# ---------------------------------------------------------------------------
# Config loader (needed before display init to read display settings)
# ---------------------------------------------------------------------------
def _load_config(path: str | None) -> dict:
    if path is None:
        path = _DEFAULT_CONFIG
    if not os.path.isfile(path):
        sys.exit(f"ERROR: config file not found: {path}")
    print(f"Loading config: {path}")
    with open(path) as f:
        return yaml.safe_load(f)


def _find_display_profile(config_path: str | None) -> str | None:
    """Search for a display profile YAML file (contains only a 'display:' section).

    Search order:
      1. display.yaml alongside the row config file
      2. ~/.config/clockish/display.yaml  (installed by install.sh)
    """
    candidates = []
    if config_path:
        candidates.append(
            os.path.join(os.path.dirname(os.path.abspath(config_path)), 'display.yaml')
        )
    candidates.append(os.path.expanduser('~/.config/clockish/display.yaml'))
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


_config: dict = _load_config(_args.config)

# If the row config has no inline 'display:' section, load it from a
# display profile file.  This lets users switch row configs freely while
# keeping all hardware settings in one place.
if 'display' not in _config:
    _profile_path = _find_display_profile(_args.config)
    if _profile_path:
        print(f"Loading display profile: {_profile_path}")
        with open(_profile_path) as _pf:
            _profile = yaml.safe_load(_pf) or {}
        if 'display' not in _profile:
            sys.exit(f"ERROR: display profile '{_profile_path}' has no 'display:' section")
        _config['display'] = _profile['display']
    else:
        sys.exit(
            "ERROR: no 'display:' section in config and no display profile found.\n"
            "  Tried: display.yaml alongside config, ~/.config/clockish/display.yaml\n"
            "  Fix:   run install.sh to set up a display profile, or copy one from\n"
            "         configs/display/ to ~/.config/clockish/display.yaml"
        )


# ---------------------------------------------------------------------------
# Display dimensions, rotation, and PIL canvas — read from config
# ---------------------------------------------------------------------------
_display_cfg = _config.get('display', {})
width    = _display_cfg.get('width',    320)
height   = _display_cfg.get('height',   480)
rotation = _display_cfg.get('rotation', 0)


image = Image.new("RGB", (width, height))
draw  = ImageDraw.Draw(image)
draw.rectangle((0, 0, width, height), outline=0, fill=0)

padding = 0
top     = padding
bottom  = height - padding
x       = 0

# ---------------------------------------------------------------------------
# Hardware init — driver is selected by display.driver in the config
# (default: ili9486).  All display-section keys are forwarded to the driver.
# ---------------------------------------------------------------------------
lcd = load_driver(_display_cfg).begin()
print(f'Initialized display: {width}x{height} rotation={rotation}, landscape={lcd.is_landscape}, dimensions={lcd.dimensions}')

# Log the orientation hint from the config (landscape | portrait | square).
# This is informational — the physical orientation is set in the display profile.
_orientation = _config.get('orientation')
if _orientation:
    print(f"Layout orientation hint: {_orientation}")

# ---------------------------------------------------------------------------
# Fonts — loaded once on first use, keyed by config name
# ---------------------------------------------------------------------------

# Project root: src/clockish/display.py → up two levels
_PROJECT_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..')
)

def _find_font(name: str) -> str:
    """Locate a TrueType font file by searching common system directories.

    Search order:
      1. Standard DejaVu location
      2. /usr/share/fonts/truetype/dseg  — installed by: sudo apt install fonts-dseg
      3. Other system font directories
      4. Alongside this script
      5. Every direct subdirectory of third_party/ — covers vendored fonts such as
         third_party/dseg/   (scripts/download-dseg-font.sh)
         third_party/nixie/  (scripts/download-nixie-font.sh)
         … and any future additions automatically.

    Falls back to the bare filename (Pillow will raise a clear error if not found).
    """
    search_dirs = [
        '/usr/share/fonts/truetype/dejavu',
        '/usr/share/fonts/truetype/dseg',                              # apt: fonts-dseg
        '/usr/share/fonts/truetype',
        '/usr/share/fonts',
        '/usr/local/share/fonts',
        os.path.dirname(os.path.abspath(__file__)),                    # alongside this script
    ]
    # Automatically include every direct subdirectory of third_party/ so that
    # any future vendored font is discovered without touching this file.
    _tp = os.path.join(_PROJECT_ROOT, 'third_party')
    if os.path.isdir(_tp):
        for _sub in sorted(os.listdir(_tp)):
            _subpath = os.path.join(_tp, _sub)
            if os.path.isdir(_subpath):
                search_dirs.append(_subpath)

    for d in search_dirs:
        candidate = os.path.join(d, name)
        if os.path.isfile(candidate):
            return candidate
    return name   # fall back; Pillow will raise a clear error if not found

_FONT_PATH = _find_font('DejaVuSans.ttf')
_FONTS: dict = {}

# ---------------------------------------------------------------------------
# Standard font scale — each name maps to a fraction of the display height.
# These built-in names are loaded on first use, using the font file set by
# 'default_font' in the config (falls back to DejaVu Sans).
#
# Scale (8 steps, largest → smallest):
#   giant  ≈ 68%  — fills a tall clock row
#   huge   ≈ 45%
#   big    ≈ 30%
#   med    ≈ 20%
#   normal ≈ 12%  — date / subtitle rows
#   small  ≈  8%
#   tiny   ≈  5%  — info / status rows
#   micro  ≈  3%
#
# Custom fonts defined in the 'fonts:' config section may override these names
# or introduce new ones.  Sizes in 'fonts:' may be integers (pixels) or
# percentage strings like "68%" (resolved against the display height).
# ---------------------------------------------------------------------------
BUILTIN_FONT_SCALE: dict[str, float] = {
    'giant':  0.68,
    'huge':   0.45,
    'big':    0.30,
    'med':    0.20,
    'normal': 0.12,
    'small':  0.08,
    'tiny':   0.05,
    'micro':  0.03,
}


def _resolve_dimension(raw, base: int) -> int:
    """Resolve a config dimension value to an integer number of pixels.

    Accepts:
      str ending in '%'  — percentage of *base*, e.g. "68%"
      float in 0.0–1.0   — fraction of *base*, e.g. 0.68
      int / float > 1.0  — direct pixel value (rounded to int)
    """
    if isinstance(raw, str) and raw.endswith('%'):
        return max(1, int(base * float(raw[:-1]) / 100))
    f = float(raw)
    if 0.0 <= f <= 1.0:
        return max(1, int(base * f))
    return max(1, int(f))


def _get_font(name: str) -> ImageFont.FreeTypeFont:
    if not _FONTS:
        # Use 'default_font' from config as the typeface for all built-in
        # scale names; fall back to DejaVu Sans if not set.
        _default_font_file = _config.get('default_font')
        _scale_font_path = _find_font(_default_font_file) if _default_font_file else _FONT_PATH
        for _scale_name, _fraction in BUILTIN_FONT_SCALE.items():
            _px = max(1, int(height * _fraction))
            _FONTS[_scale_name] = ImageFont.truetype(_scale_font_path, _px)
    if name not in _FONTS:
        # Try to load a custom font defined in the config's 'fonts:' section.
        # Entries with both 'file' and 'size' are loaded here; file-only entries
        # are resolved at layout time by _init_layout() and should not reach here.
        custom = _config.get('fonts', {}).get(name)
        if custom and isinstance(custom, dict) and 'size' in custom:
            font_path = _find_font(custom.get('file', 'DejaVuSans.ttf'))
            size_px = _resolve_dimension(custom['size'], height)
            _FONTS[name] = ImageFont.truetype(font_path, size_px)
        else:
            return _FONTS.get('normal', ImageFont.load_default())
    return _FONTS[name]


def dump_font_metrics():
    """Print ascent/descent/bbox for every configured font size."""
    for name, f in _FONTS.items():
        try:
            asc, desc = f.getmetrics()
        except AttributeError:
            asc = desc = -1
        bbox_ag = f.getbbox("Ag|")
        bbox_0  = f.getbbox("0")
        print(f"  font={name:8s}  ascent={asc:3d}  descent={desc:3d}"
              f"  cell={asc+desc:3d}  bbox(Ag|)={bbox_ag}  bbox(0)={bbox_0}")


# Convenience aliases used by legacy display functions
bigfont   = _get_font('big')
medfont   = _get_font('med')
font      = _get_font('normal')
smallfont = _get_font('small')
tiny      = _get_font('tiny')

if DEBUG:
    print("Font metrics:")
    dump_font_metrics()



# ---------------------------------------------------------------------------
# Color helper — looks up any palette name from pyili9486.colors
# ---------------------------------------------------------------------------
def _color(name: str) -> str:
    """Return a hex color string for a palette name or hex value (case-insensitive).

    Falls back to '#888888' (middle grey) and emits a stderr warning if the
    name is not in the palette and is not a valid RGB hex string.
    """
    key = name.upper()
    if key in BY_NAME:
        return rgb_to_hex(BY_NAME[key])
    # Accept #RGB, #RRGGBB, RGB, RRGGBB
    stripped = name.lstrip('#')
    if len(stripped) in (3, 6) and all(c in '0123456789abcdefABCDEF' for c in stripped):
        return name if name.startswith('#') else f'#{name}'
    print(f"WARNING: unknown color '{name}' — defaulting to grey", file=sys.stderr)
    return '#888888'


# Pre-resolved constants for colors used by hardcoded UI elements (after _color is defined)
_C_WHITE    = _color('WHITE')
_C_DARKGREY = _color('DARKGREY')
_C_GREY     = _color('GREY')
_C_GREEN    = _color('GREEN')
_C_BROWN    = _color('BROWN')
_C_BLACK    = _color('BLACK')


# ---------------------------------------------------------------------------
# Color resolution — walk the config once at startup, replacing every color
# string with its resolved hex value so renderers never do lookups per frame.
# ---------------------------------------------------------------------------
def _resolve_colors(cfg: dict) -> None:
    """Resolve all color fields in the config tree in-place."""
    COLOR_KEYS = {'color', 'background'}

    def _walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in COLOR_KEYS and isinstance(v, str):
                    obj[k] = _color(v)
                elif k == 'colors' and isinstance(v, dict):
                    for ck, cv in v.items():
                        if isinstance(cv, str):
                            v[ck] = _color(cv)
                else:
                    _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(cfg)

_resolve_colors(_config)


# ---------------------------------------------------------------------------
# Timing utilities
# ---------------------------------------------------------------------------
@contextmanager
def timed_section(label: str, timings: dict):
    t0 = time.perf_counter()
    yield
    timings[label] = time.perf_counter() - t0


def timed_display(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        t0 = time.perf_counter()
        result = func(*args, **kwargs)
        elapsed = time.perf_counter() - t0
        print(f"[timing] {func.__name__}: {elapsed*1000:.1f} ms")
        return result
    return wrapper


# ---------------------------------------------------------------------------
# System-info helpers
# ---------------------------------------------------------------------------
def get_ip_address():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "N/A"

def get_hostname():
    return socket.gethostname()

def get_uptime_str():
    with open("/proc/uptime") as f:
        uptime_seconds = float(f.read().split()[0])
    days  = int(uptime_seconds // 86400)
    hours = int((uptime_seconds % 86400) // 3600)
    mins  = int((uptime_seconds % 3600) // 60)
    if days > 0:    return f"up {days}d {hours}h {mins}m"
    elif hours > 0: return f"up {hours}h {mins}m"
    else:           return f"up {mins}m"

def get_cpu_load():
    return os.getloadavg()[0]

def _read_cpu_stat() -> tuple[int, int]:
    """Read cumulative (idle, total) jiffies from /proc/stat cpu line."""
    with open("/proc/stat") as f:
        line = f.readline()          # first line: "cpu  ..."
    fields = [int(x) for x in line.split()[1:]]
    # fields: user nice system idle iowait irq softirq steal guest guest_nice
    idle  = fields[3] + (fields[4] if len(fields) > 4 else 0)  # idle + iowait
    total = sum(fields)
    return idle, total

# Prime the first sample at import time so the first call to get_cpu_percent()
# returns a meaningful value rather than 0%.
_cpu_stat_prev: tuple[int, int] = _read_cpu_stat()
_cpu_percent_cached: float = 0.0
_cpu_percent_cache_time: float = -999.0

def get_cpu_percent() -> float:
    """Return CPU utilisation as 0-100%, sampled from /proc/stat.

    Re-samples at most once per second; between samples returns the last value.
    This is the same method used by top/vmstat and works correctly regardless
    of CPU count or load average.
    """
    global _cpu_stat_prev, _cpu_percent_cached, _cpu_percent_cache_time
    now = time.monotonic()
    if now - _cpu_percent_cache_time < 1.0:
        return _cpu_percent_cached
    idle_now, total_now = _read_cpu_stat()
    idle_prev, total_prev = _cpu_stat_prev
    total_delta = total_now - total_prev
    idle_delta  = idle_now  - idle_prev
    if total_delta > 0:
        _cpu_percent_cached = max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0))
    _cpu_stat_prev        = (idle_now, total_now)
    _cpu_percent_cache_time = now
    return _cpu_percent_cached

def get_mem_usage():
    meminfo = {}
    with open("/proc/meminfo") as f:
        for line in f:
            parts = line.split()
            meminfo[parts[0].rstrip(':')] = int(parts[1])
    total_mb = meminfo['MemTotal'] // 1024
    avail_mb = meminfo['MemAvailable'] // 1024
    used_mb  = total_mb - avail_mb
    pct = used_mb * 100 / total_mb if total_mb else 0
    return f"Mem: {used_mb}/{total_mb} MB  {pct:.2f}%"

def get_disk_usage():
    usage = shutil.disk_usage("/")
    total_gb = usage.total // (1024 ** 3)
    used_gb  = usage.used  // (1024 ** 3)
    pct = usage.used * 100 / usage.total if usage.total else 0
    return f"Disk: {used_gb}/{total_gb} GB  {pct:.0f}%"

def get_cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            temp_mc = int(f.read().strip())
        return f"CPU Temp: {temp_mc / 1000:.1f} C"
    except Exception:
        return "CPU Temp: N/A"

def get_ntp_status():
    # Cache result for 60 seconds to avoid repeated subprocess calls.
    if not hasattr(get_ntp_status, '_cache_time'):
        get_ntp_status._cache_time  = -999.0
        get_ntp_status._cache_value = ":-( ntp )-:"
    now = time.monotonic()
    if now - get_ntp_status._cache_time < 60:
        return get_ntp_status._cache_value
    ntp_ok = synced = False
    try:
        ntp_ok = synced = os.path.exists("/run/systemd/timesync/synchronized")
    except Exception:
        pass
    if not ntp_ok:
        try:
            out = subprocess.check_output(["timedatectl", "show"], stderr=subprocess.DEVNULL).decode()
            props = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
            ntp_ok = props.get("NTP", "no").lower() == "yes"
            synced = props.get("NTPSynchronized", "no").lower() == "yes"
        except Exception:
            pass
    result = " +NTP+" if (ntp_ok and synced) else ":-( ntp )-:"
    get_ntp_status._cache_value = result
    get_ntp_status._cache_time  = now
    return result

def get_ntp_upstream_count():
    # Cache result for 60 seconds.
    if not hasattr(get_ntp_upstream_count, '_cache_time'):
        get_ntp_upstream_count._cache_time  = -999.0
        get_ntp_upstream_count._cache_value = "?"
    now = time.monotonic()
    if now - get_ntp_upstream_count._cache_time < 60:
        return get_ntp_upstream_count._cache_value
    try:
        out = subprocess.check_output(["chronyc", "activity"], stderr=subprocess.DEVNULL).decode()
        for line in out.splitlines():
            if "sources online" in line:
                result = line.split()[0]
                break
        else:
            result = "?"
    except Exception:
        result = "?"
    get_ntp_upstream_count._cache_value = result
    get_ntp_upstream_count._cache_time  = now
    return result

def get_wireguard_status():
    """Run wgstatus.sh and return its output as a single string (cached 30s)."""
    if not hasattr(get_wireguard_status, '_cache_time'):
        get_wireguard_status._cache_time  = -999.0
        get_wireguard_status._cache_value = "wg: N/A"
    now = time.monotonic()
    if now - get_wireguard_status._cache_time < 30:
        return get_wireguard_status._cache_value
    try:
        result = subprocess.check_output(
            "/usr/local/bin/wgstatus.sh || echo 'wgstatus.sh error'",
            shell=True, stderr=subprocess.DEVNULL
        ).decode("utf-8").strip()
    except Exception:
        result = "wg: error"
    get_wireguard_status._cache_value = result
    get_wireguard_status._cache_time  = now
    return result


def get_wifi_info():
    now = time.monotonic()
    if (hasattr(get_wifi_info, '_cache_time')
            and now - get_wifi_info._cache_time < 10):
        return get_wifi_info._cache_value

    ssid = signal = quality = "N/A"
    connected = False
    try:
        with open("/proc/net/wireless") as f:
            lines = f.readlines()
        for line in lines[2:]:
            parts = line.split()
            if parts:
                quality   = parts[2].rstrip('.')
                signal    = parts[3].rstrip('.')
                connected = True
                break
    except Exception:
        pass
    try:
        with open("/sys/class/net/wlan0/operstate") as f:
            connected = f.read().strip() == "up"
    except Exception:
        pass
    try:
        ssid = subprocess.check_output(["iwgetid", "-r"], stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        ssid = "N/A"
    status      = "WiFi OK" if connected else "No Wifi"
    signal_str  = f"{signal}dBm" if signal != "N/A" else "N/A"
    ssid_str    = ssid if ssid else "N/A"
    quality_str = quality if quality != "N/A" else "N/A"
    get_wifi_info._cache_value = (status, ssid_str, signal_str, quality_str)
    get_wifi_info._cache_time  = now
    return get_wifi_info._cache_value

def _get_fact(source: str) -> str:
    """Return the raw value for a named system-info source (no label prefix)."""
    return {
        'ip':           get_ip_address,
        'hostname':     get_hostname,
        'uptime':       get_uptime_str,
        'version':      lambda: __version__,
        'config_file':  lambda: os.path.basename(_args.config) if _args.config else os.path.basename(_DEFAULT_CONFIG),
        'cpu':          lambda: f"{get_cpu_percent():.1f}%",
        'cpu_load':     lambda: f"{get_cpu_load():.2f}",
        'mem':          get_mem_usage,
        'disk':         get_disk_usage,
        'temp':         get_cpu_temp,
        'ntp_status':   get_ntp_status,
        'ntp_upstream': get_ntp_upstream_count,
        'ntp_all':      lambda: get_ntp_status() + " " + get_ntp_upstream_count(),
        'wireguard':    get_wireguard_status,
        # wifi facts — raw values from the cached get_wifi_info() tuple
        #   (status, ssid, signal_dbm, quality)
        'wifi_status':  lambda: get_wifi_info()[0],
        'wifi_ssid':    lambda: get_wifi_info()[1],
        'wifi_signal':  lambda: get_wifi_info()[2],
        'wifi_quality': lambda: get_wifi_info()[3],
        'wifi_all':     lambda: "  ".join(get_wifi_info()),
    }.get(source, lambda: source)()


# Default labels shown when a fact panel has label: "default"
_FACT_DEFAULT_LABELS: dict[str, str] = {
    'ip':           'ip ',
    'hostname':     'host ',
    'uptime':       '',
    'version':      'v',
    'config_file':  'cfg ',
    'cpu':          'cpu ',
    'cpu_load':     'load ',
    'mem':          '',
    'disk':         '',
    'temp':         '',
    'ntp_status':   'ntp ',
    'ntp_upstream': 'ntp sources ',
    'ntp_all':      'ntp ',
    'wireguard':    'wg ',
    'wifi_status':  'wifi ',
    'wifi_ssid':    'ssid ',
    'wifi_signal':  'signal ',
    'wifi_quality': 'quality ',
    'wifi_all':     'wifi ',
}


# ---------------------------------------------------------------------------
# Timezone helper
# ---------------------------------------------------------------------------
_TIME_FORMATS = {
    '12h':  '%-I:%M %p',
    '24h':  '%H:%M',
    '24hs': '%H:%M:%S',
}

def _now_in_tz(tz_name: str) -> datetime.datetime:
    if tz_name.lower() == 'local':
        return datetime.datetime.now()
    return datetime.datetime.now(zoneinfo.ZoneInfo(tz_name))


# ---------------------------------------------------------------------------
# Font metric helpers
# ---------------------------------------------------------------------------
def _font_height(f: ImageFont.FreeTypeFont) -> int:
    """Return the full cell height (ascent + descent)."""
    try:
        ascent, descent = f.getmetrics()
        return ascent + descent
    except AttributeError:
        bbox = f.getbbox("Ag|")
        return bbox[3] - bbox[1]

def _center_y(py: int, ph: int, ink_top: int, ink_h: int) -> int:
    """Compute the y coordinate to pass to PIL's draw.text() so that the
    visible ink is vertically centred within the [py, py+ph) band.

    PIL places the cell (ascent+descent) starting at the given y, so the ink
    starts at  y + ink_top.  We want:

        ink_start = py + (ph - ink_h) // 2
        y         = ink_start - ink_top

    If that y is negative (font taller than row) we clamp at -ink_top so we
    lose only the blank space above the ink, never actual glyph pixels.
    """
    ink_start = py + max(0, (ph - ink_h) // 2)
    return max(py - ink_top, ink_start - ink_top)


def _font_ink_top(f: ImageFont.FreeTypeFont) -> int:
    """Return the y offset from the cell top to the first ink pixel."""
    try:
        return f.getbbox("Ag|")[1]
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Row layout — uses fixed heights from config (required on every row)
# ---------------------------------------------------------------------------
def _measure_rows(rows: list) -> list:
    """Return a list of (row_dict, y, height) tuples.

    Every row MUST have a 'height' key.  The value may be an integer (pixels),
    a float 0.0–1.0 (fraction of display height), or a percentage string such
    as "15%" (resolved against the display height).  Warns on stderr if the
    total height exceeds the display height; content that overflows is clipped.
    """
    result = []
    y = top
    for r in rows:
        h = _resolve_dimension(r.get('height', 30), height)
        result.append((r, y, h))
        y += h

    total = sum(h for _, _, h in result)
    if total > height:
        print(
            f"WARNING: row total height {total}px exceeds display height {height}px "
            f"by {total - height}px — bottom rows will be clipped.",
            file=sys.stderr,
        )

    return result


# ---------------------------------------------------------------------------
# Layout pre-computation — runs once at startup; cached in _LAYOUT.
# ---------------------------------------------------------------------------
_LAYOUT: list = []   # list of (row_dict, y, row_height_px)

# Fraction of row height used when font_size is 'auto'.
_AUTO_FONT_FRACTION = 0.75


def _init_layout() -> None:
    """Pre-compute row layout and resolve font references in panel dicts.

    Two ways to specify a font on a panel:

      font_size: <name|%|px|auto>
        Uses default_font (or DejaVu) as the typeface.  Named scale names
        (giant, huge, …) are pre-loaded at startup.  'auto' sizes to 75% of
        the row height.  '%' and integer values resolve against display height.

      font: <name>   [+ font_size: <name|%|px|auto>]
        'name' is a key in the fonts: section (file-only entry) or the
        built-in 'debug' alias (always DejaVuSans, immune to default_font).
        Size comes from font_size: if given, otherwise defaults to 'auto'.
        At init time the panel dict is rewritten: font_size: receives the
        resolved synthetic key and font: is consumed.

    All resolved fonts are registered in _FONTS under synthetic names so
    renderers just call _get_font(p.get('font_size', 'normal')) as usual.
    """
    global _LAYOUT
    rows = _config.get('rows', [])
    layout = _measure_rows(rows)   # height-overflow warning printed here, once

    _default_font_file = _config.get('default_font')
    _auto_path = _find_font(_default_font_file) if _default_font_file else _FONT_PATH

    # Map font reference names → resolved TTF paths.
    # 'debug' is always DejaVuSans regardless of default_font.
    _font_files: dict = {'debug': _FONT_PATH}
    for fname, fentry in _config.get('fonts', {}).items():
        if isinstance(fentry, dict) and 'file' in fentry:
            _font_files[fname] = _find_font(fentry['file'])

    def _resolve_size(font_size_raw, rh: int) -> int:
        """Resolve a font_size value to pixels (floor, never round up)."""
        if font_size_raw is None or font_size_raw == 'auto':
            return max(1, int(rh * _AUTO_FONT_FRACTION))
        if isinstance(font_size_raw, str) and font_size_raw.endswith('%'):
            return max(1, int(height * float(font_size_raw[:-1]) / 100))
        if isinstance(font_size_raw, str) and font_size_raw in BUILTIN_FONT_SCALE:
            return max(1, int(height * BUILTIN_FONT_SCALE[font_size_raw]))
        f = float(font_size_raw)
        if 0.0 < f <= 1.0:
            return max(1, int(height * f))
        return max(1, int(f))

    for r, ry, rh in layout:
        for p in r.get('panels', []):
            font_ref  = p.get('font')      # which TTF file to use
            font_size = p.get('font_size') # size spec

            if font_ref is not None:
                # font: attribute specified — resolve (file, size) → synthetic key.
                # 'debug' defaults to 'micro' when no font_size is given;
                # all other font references default to 'auto' (75% of row height).
                file_path = _font_files.get(font_ref, _auto_path)
                if font_size is None and font_ref == 'debug':
                    font_size = 'micro'
                size_px   = _resolve_size(font_size, rh)
                file_stem = os.path.splitext(os.path.basename(file_path))[0]
                syn_name  = f'_res_{file_stem}_{size_px}'
                if syn_name not in _FONTS:
                    _FONTS[syn_name] = ImageFont.truetype(file_path, size_px)
                    if DEBUG:
                        print(f"  font '{font_ref}' -> {os.path.basename(file_path)} "
                              f"{size_px}px  (key={syn_name})")
                p['font_size'] = syn_name
                del p['font']   # consumed; renderers only use font_size

            elif font_size == 'auto':
                # No font: but font_size: auto — default_font at row-relative size
                auto_name = f'_auto_{rh}'
                if auto_name not in _FONTS:
                    px = max(1, int(rh * _AUTO_FONT_FRACTION))
                    _FONTS[auto_name] = ImageFont.truetype(_auto_path, px)
                    if DEBUG:
                        print(f"  auto font '{auto_name}': {px}px "
                              f"({_AUTO_FONT_FRACTION*100:.0f}% of {rh}px row)")
                p['font_size'] = auto_name

            # Named scale, explicit %, or integer with no font: →
            # handled by _get_font() at render time using default_font.

    _LAYOUT = layout


# Pre-compute layout and resolve 'auto' font references once at startup.
# Must be called after: font aliases (populates _FONTS), _resolve_colors (resolves
# color fields), and all helper functions used by _measure_rows/_init_layout.
_init_layout()


# ---------------------------------------------------------------------------
# Panel renderers — uniform signature: (p, px, py, pw, ph, ...)
#   px, py  = top-left origin of the panel's allocated rectangle
#   pw, ph  = width and height of that rectangle
# ---------------------------------------------------------------------------

def _draw_text_line(d: ImageDraw.ImageDraw, px: int, py: int, pw: int, ph: int,
                    text: str, f: ImageFont.FreeTypeFont, color: str,
                    x_offset: int = 0, justify: str = 'center') -> None:
    """Draw a single line of text within the panel rect.

    Vertical placement: always centred within [py, py+ph).
    Horizontal placement controlled by `justify`:
      'center' — ink centred within [px+x_offset, px+pw)
      'left'   — ink starts at px+x_offset
      'right'  — ink ends at px+pw
    """
    ink_top = _font_ink_top(f)
    ink_h   = _font_height(f) - ink_top
    cy      = _center_y(py, ph, ink_top, ink_h)

    text_w = f.getbbox(text)[2]   # pixel width of rendered text

    if justify == 'right':
        tx = px + pw - text_w
    elif justify == 'center':
        available = pw - x_offset
        tx = px + x_offset + max(0, (available - text_w) // 2)
    else:  # 'left' or anything else
        tx = px + x_offset

    d.text((tx, cy), text, font=f, fill=color)


def _render_clock_panel(p: dict, px: int, py: int, pw: int, ph: int,
                         now: datetime.datetime, d: ImageDraw.ImageDraw) -> None:
    color       = p.get('color', _C_WHITE)
    time_f      = _get_font(p.get('font_size', 'normal'))
    _time_format = p.get('time_format')
    if _time_format is None:
        fmt = '%H:%M'
    else:
        fmt = _TIME_FORMATS.get(_time_format, _time_format)
    time_str    = now.strftime(fmt).upper()
    label_str   = p.get('label', '')

    if DEBUG_LAYOUT:
        cell_h = _font_height(time_f)
        ink_top = _font_ink_top(time_f)
        ink_h   = cell_h - ink_top
        print(f"      [clock] font_size={p.get('font_size','normal')} cell_h={cell_h} "
              f"ink_top={ink_top} ink_h={ink_h}  str='{time_str}'")

    justify = p.get('justify', 'center')
    _draw_text_line(d, px, py, pw, ph, time_str, time_f, color, justify=justify)

    if label_str:
        time_w = time_f.getbbox(time_str)[2]
        _draw_text_line(d, px + time_w + 6, py, pw, ph, label_str, time_f, color, justify='left')


def _render_date_panel(p: dict, px: int, py: int, pw: int, ph: int,
                        now: datetime.datetime, d: ImageDraw.ImageDraw) -> None:
    color    = p.get('color', _C_WHITE)
    date_f   = _get_font(p.get('font_size', 'normal'))
    date_str = now.strftime(p.get('date_format', '%a %b %-d, %Y'))

    if DEBUG_LAYOUT:
        cell_h  = _font_height(date_f)
        ink_top = _font_ink_top(date_f)
        ink_h   = cell_h - ink_top
        print(f"      [date]  font_size={p.get('font_size','normal')} cell_h={cell_h} "
              f"ink_top={ink_top} ink_h={ink_h}  str='{date_str}'")

    _draw_text_line(d, px, py, pw, ph, date_str, date_f, color,
                    x_offset=4, justify=p.get('justify', 'center'))


def _render_fact_panel(p: dict, px: int, py: int, pw: int, ph: int,
                        d: ImageDraw.ImageDraw) -> None:
    f      = _get_font(p.get('font_size', 'normal'))
    color  = p.get('color', _C_WHITE)
    source = p['source']
    value  = _get_fact(source)

    if 'label' not in p:
        text = value
    elif p['label'] == 'default':
        text = _FACT_DEFAULT_LABELS.get(source, '') + value
    else:
        text = p['label'] + value

    _draw_text_line(d, px, py, pw, ph, text, f, color, justify=p.get('justify', 'center'))


def _render_text_panel(p: dict, px: int, py: int, pw: int, ph: int,
                        d: ImageDraw.ImageDraw) -> None:
    """Static text panel — renders p['label'] as-is, no data lookup."""
    _draw_text_line(d, px, py, pw, ph,
                    p.get('label', ''),
                    _get_font(p.get('font_size', 'normal')),
                    p.get('color', _C_WHITE),
                    justify=p.get('justify', 'center'))



def _render_wifi_graphic_panel(p: dict, px: int, py: int, pw: int, ph: int,
                                d: ImageDraw.ImageDraw) -> None:
    """Draw a wifi signal-strength graphic (arcs + dot) inside the panel.

    4 levels (0 = no signal / disconnected, 1-4 = signal strength).
    The graphic fills the available cell as large as possible, centred both
    horizontally and vertically.

    Panel config keys:
      color      - arc/dot colour when connected (default: white)
      background - already filled by _dispatch_panel; respected automatically
    """
    status, ssid_str, signal_str, quality_str = get_wifi_info()
    connected = (status == "WiFi OK")

    # Determine bar level 0-4 from signal dBm or quality
    level = 0
    if connected:
        try:
            dbm = float(signal_str.replace('dBm', '').strip())
            # -50 or better → 4, -60 → 3, -70 → 2, weaker → 1
            if   dbm >= -50: level = 4
            elif dbm >= -60: level = 3
            elif dbm >= -70: level = 2
            else:            level = 1
        except (ValueError, AttributeError):
            try:
                q = float(quality_str)
                level = max(1, min(4, int(q / 18) + 1))
            except (ValueError, AttributeError):
                level = 1

    arc_color = p.get('color', _C_WHITE)
    dim_color = '#333333'
    if not connected:
        arc_color = _color('DARKGREY')

    # --- Geometry ---
    # The wifi fan is a quarter-circle arc set, so it is half as tall as wide.
    # We want it to fill the cell tightly.
    #
    # No padding — the arc geometry already leaves enough visual margin.
    draw_w    = pw
    draw_h    = ph

    # The graphic needs: width = 2*R_max, height = R_max  (fan height = radius)
    # So fit into (draw_w, draw_h):  R = min(draw_w//2, draw_h)
    R_max     = max(4, min(draw_w // 2, draw_h))

    # Stroke width: proportional to R_max, at least 1
    stroke    = max(1, R_max // 8)

    num_arcs  = 3   # 3 arcs → 4 levels (0 + one per arc)
    dot_r     = max(2, R_max // 7)
    gap       = max(1, R_max // 12)   # gap between dot edge and first arc
    # Distribute arcs evenly in (dot_r + gap .. R_max)
    arc_band  = R_max - dot_r - gap
    r_step    = arc_band // num_arcs if num_arcs else arc_band

    # Centre the graphic: fan occupies R_max wide (×2) and R_max tall
    fan_w = R_max * 2
    fan_h = R_max   # top of outermost arc to dot centre

    # Horizontal centre of panel cell
    cx = px + pw // 2
    # Vertical: centre the fan_h within the cell, then shift 10% of cell height toward top
    top_y  = py + max(0, (ph - fan_h) // 2 - ph // 10)
    base_y = top_y + fan_h    # dot centre y

    # --- Draw dot ---
    d.ellipse((cx - dot_r, base_y - dot_r, cx + dot_r, base_y + dot_r),
              fill=arc_color)

    # --- Draw arcs ---
    # arc i (0-based) lights when level > i  (arc0 → level≥1, arc1 → level≥2, …)
    for i in range(num_arcs):
        r  = dot_r + gap + r_step * (i + 1)
        bx = cx - r
        by = base_y - r
        ex = cx + r
        ey = base_y + r
        color = arc_color if (connected and level >= i + 1) else dim_color
        d.arc((bx, by, ex, ey), start=215, end=325, fill=color, width=stroke)

    # --- Not connected: red X over the graphic ---
    if not connected:
        cross_w = max(1, R_max // 6)
        xs = cx - R_max // 2
        xe = cx + R_max // 2
        ys = top_y
        ye = base_y
        d.line((xs, ys, xe, ye), fill=_color('RED'), width=cross_w)
        d.line((xe, ys, xs, ye), fill=_color('RED'), width=cross_w)


def _render_divider_panel(p: dict, px: int, py: int, pw: int, ph: int,
                           d: ImageDraw.ImageDraw) -> None:
    clr    = p.get('color', _C_DARKGREY)
    line_h = int(p.get('height', 2))
    line_y = py + (ph - line_h) // 2
    if DEBUG_LAYOUT:
        print(f"      [divider] line_h={line_h} line_y={line_y}")
    d.rectangle((px, line_y, px + pw - 1, line_y + line_h - 1), fill=clr)


def _render_debug_panel(p: dict, px: int, py: int, pw: int, ph: int,
                         timings: dict, t0: float, d: ImageDraw.ImageDraw) -> None:
    prep_ms = (time.perf_counter() - t0) * 1000
    row_h   = ph // 4
    f       = _get_font(p.get('font_size', 'micro'))
    steps   = list(timings.items())
    half    = len(steps) // 2 + len(steps) % 2
    lines = [
        f"prep={prep_ms:.0f}ms  disp={_last_display_ms:.0f}ms",
        f"ntp={timings.get('ntp',0)*1000:.0f}  tz={timings.get('tz',0)*1000:.0f}  draw={timings.get('draw',0)*1000:.0f}",
        "  ".join(f"{k[:3]}={v*1000:.0f}" for k, v in steps[:half]),
        "  ".join(f"{k[:3]}={v*1000:.0f}" for k, v in steps[half:]),
    ]
    c = p.get('color', _C_BROWN)
    for i, line in enumerate(lines):
        y = py + row_h * i
        d.text((px, y), line, font=f, fill=c)


def _dispatch_panel(p: dict, px: int, py: int, pw: int, ph: int,
                    tz_cache: dict, timings: dict, t0: float,
                    target_draw: ImageDraw.ImageDraw) -> None:
    """Dispatch to the correct panel renderer based on panel type."""
    # Fill panel background if explicitly set (overrides row background).
    if 'background' in p:
        target_draw.rectangle((px, py, px + pw - 1, py + ph - 1),
                               fill=p['background'])
    pt = p.get('type', '')
    if pt == 'clock':
        _render_clock_panel(p, px, py, pw, ph, tz_cache[p.get('timezone', 'local')], target_draw)
    elif pt == 'date':
        _render_date_panel(p, px, py, pw, ph, tz_cache[p.get('timezone', 'local')], target_draw)
    elif pt == 'fact':
        _render_fact_panel(p, px, py, pw, ph, target_draw)
    elif pt == 'text':
        _render_text_panel(p, px, py, pw, ph, target_draw)
    elif pt == 'divider':
        _render_divider_panel(p, px, py, pw, ph, target_draw)
    elif pt == 'wifi_graphic':
        _render_wifi_graphic_panel(p, px, py, pw, ph, target_draw)
    elif pt == 'debug':
        _render_debug_panel(p, px, py, pw, ph, timings, t0, target_draw)
    # 'blank' — space reserved, nothing to draw


def _resolve_panel_widths(panels: list, row_w: int, row_idx: int) -> list[int]:
    """Compute pixel width for each panel in a row.

    Each panel may specify 'width' as:
      - integer pixels:  width: 120
      - float fraction:  width: 0.33   (proportion of row_w)
      - percentage str:  width: "44%"
    Panels without 'width' share the remaining space equally.

    Returns a list of integer pixel widths, one per panel.
    Emits a warning if the total exceeds row_w.
    """
    n = len(panels)
    fixed_widths = {}   # index -> px for panels with explicit width
    auto_indices = []   # indices of panels that need auto-sizing

    for i, p in enumerate(panels):
        raw = p.get('width')
        if raw is None:
            auto_indices.append(i)
        elif isinstance(raw, str) and raw.endswith('%'):
            fixed_widths[i] = int(row_w * float(raw[:-1]) / 100)
        elif isinstance(raw, float) and raw <= 1.0:
            fixed_widths[i] = int(row_w * raw)
        else:
            fixed_widths[i] = int(raw)

    # Distribute remaining pixels evenly among auto panels
    used = sum(fixed_widths.values())
    remaining = max(0, row_w - used)
    auto_w = (remaining // len(auto_indices)) if auto_indices else 0

    widths = []
    for i in range(n):
        widths.append(fixed_widths.get(i, auto_w))

    total = sum(widths)
    if total > row_w:
        print(
            f"WARNING: row {row_idx} panel widths total {total}px "
            f"exceeds row width {row_w}px by {total - row_w}px — rightmost panels clipped.",
            file=sys.stderr,
        )
    return widths


def _render_row(r: dict, row_idx: int, ry: int, rw: int, rh: int,
                tz_cache: dict, timings: dict, t0: float) -> None:
    """Render a row: panels are laid out left-to-right with computed widths.

    Every row is treated the same regardless of panel count — a single-panel
    row is just a multi-panel row with one panel.
    """
    panels = r.get('panels', [])
    if not panels:
        return

    row_name = r.get('name', f'row[{row_idx}]')

    # Fill row sub-image with row background; panels may override with their own bg.
    row_bg   = r.get('background', _C_BLACK)
    row_img  = Image.new("RGB", (rw, rh), row_bg)
    row_draw = ImageDraw.Draw(row_img)

    widths = _resolve_panel_widths(panels, rw, row_idx)
    if DEBUG_LAYOUT:
        auto_px = max(1, int(rh * _AUTO_FONT_FRACTION))
        print(f"row {row_idx} '{row_name}': ry={ry} rh={rh} rw={rw}  "
              f"panels={len(panels)} widths={widths}  "
              f"auto={auto_px}px ({_AUTO_FONT_FRACTION*100:.0f}% of {rh}px)")
    px = 0
    for p, pw in zip(panels, widths):
        if DEBUG_LAYOUT:
            print(f"  panel type={p.get('type','?')} px={px} pw={pw}")
        _dispatch_panel(p, px, 0, pw, rh, tz_cache, timings, t0, row_draw)
        px += pw

    image.paste(row_img, (0, ry))


# ---------------------------------------------------------------------------
# Main display function — called once per second
# ---------------------------------------------------------------------------
_last_display_ms: float = 0.0

def show_rows():
    global _last_display_ms
    t0 = time.perf_counter()
    timings = {}

    with timed_section("ntp", timings):
        ntp_status = get_ntp_status() + " " + get_ntp_upstream_count()

    # Snapshot all timezones referenced by all panels in all rows.
    with timed_section("tz", timings):
        tz_cache: dict[str, datetime.datetime] = {}
        for r, _ry, _rh in _LAYOUT:
            for p in r.get('panels', []):
                if p.get('type') in ('clock', 'date'):
                    tz = p.get('timezone', 'local')
                    if tz not in tz_cache:
                        tz_cache[tz] = _now_in_tz(tz)

    layout = _LAYOUT

    with timed_section("draw", timings):
        draw.rectangle((0, 0, width - 1, height - 1), fill=0)  # fill gaps between rows

        for row_idx, (r, ry, rh) in enumerate(layout):
            _render_row(r, row_idx, ry, width, rh, tz_cache, timings, t0)

    t_disp = time.perf_counter()
    lcd.display(image)
    _last_display_ms = (time.perf_counter() - t_disp) * 1000

    if DEBUG:
        total_ms = (time.perf_counter() - t0) * 1000
        parts = ", ".join(f"{k}={v*1000:.0f}ms" for k, v in timings.items())
        print(f"show_rows: total={total_ms:.0f}ms  disp={_last_display_ms:.0f}ms  [{parts}]")


# ---------------------------------------------------------------------------
# Entry point — called by the `clockish` console script and by __main__.py
# ---------------------------------------------------------------------------
def main():
    """Run the display loop.  All module-level init has already happened."""
    global DEBUG_LAYOUT
    try:
        lcd.idle()
        lcd.idle(False)   # idle(True) can cause fonts/colors to render weirdly

        if DEBUG_LAYOUT:
            # Single render then exit so debug output is easy to capture.
            show_rows()
            sys.exit(0)

        if DEBUG:
            # Print the full layout once at startup using the same DEBUG_LAYOUT
            # code path, then continue with the normal timed loop.
            DEBUG_LAYOUT = True
            show_rows()
            DEBUG_LAYOUT = False

        while True:
            show_rows()
            # Sleep until the next whole second boundary.
            now_mono = time.monotonic()
            sleep_s  = 1.0 - (now_mono % 1.0)
            if sleep_s > 0.001:
                time.sleep(sleep_s)

    except KeyboardInterrupt:
        pass
    finally:
        # Do not blank the display on exit — lets you see where it stopped.
        # GPIO.cleanup() is not needed — rpi-lgpio facade handles it.
        lcd.close()


# ---------------------------------------------------------------------------
# utility / alternate display functions
# ---------------------------------------------------------------------------
@timed_display
def blank():
    draw.rectangle((0, 0, width, height), fill=0)
    lcd.display(image)

@timed_display
def show_invert():
    im = PIL.ImageOps.invert(image)
    lcd.display(im)


# ---------------------------------------------------------------------------
# Main loop — call show_rows() once per second
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    main()

