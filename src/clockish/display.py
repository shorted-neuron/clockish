#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import datetime
import functools
import json
import os
import re
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zoneinfo # before yaml
from contextlib import contextmanager # before yaml
import yaml

# syslog: Unix/Linux only
try:
    import syslog
    _SYSLOG_AVAILABLE = True
except ImportError:
    _SYSLOG_AVAILABLE = False

from PIL import Image, ImageDraw, ImageFont
import PIL.ImageOps

from clockish import __version__
from clockish.colors import rgb_to_hex, BY_NAME
from clockish.drivers import load_driver
from clockish.transforms import apply_transforms


# ---------------------------------------------------------------------------
# Debug flag  --  set by -d / --debug command-line argument
# ---------------------------------------------------------------------------
def _find_default_config() -> str:
    """Search for clockish.yaml in order of preference."""
    candidates = [
        # 1. Next to this file (old fork layout  --  kept for dev convenience)
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'clockish.yaml'),
        # 2. configs/ directory relative to project root (clockish src-layout)
        # __file__ is src/clockish/display.py -> go up 2 levels to reach the project root.
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
    description='Pi Panel Display  --  config-driven LCD dashboard',
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

# ---------------------------------------------------------------------------
# Module-level globals  --  populated by _init(), called from main().
# Functions may read these globals; nothing should depend on them being set
# before main() calls _init().
# ---------------------------------------------------------------------------
_args          = None
DEBUG: bool    = False
DEBUG_LAYOUT: bool = False
_config: dict  = {}
_display_cfg: dict = {}
width: int     = 320
height: int    = 480
rotation: int  = 0
image: Image.Image           = Image.new("RGB", (320, 480))
draw:  ImageDraw.ImageDraw   = ImageDraw.Draw(image)
padding: int   = 0
top: int       = 0
bottom: int    = 480
x: int         = 0
lcd            = None
_orientation   = None
_FONT_PATH: str = ''
_C_WHITE:    str = '#ffffff'
_C_DARKGREY: str = '#404040'
_C_GREY:     str = '#808080'
_C_GREEN:    str = '#00ff00'
_C_BROWN:    str = '#964b00'
_C_BLACK:    str = '#000000'
bigfont = medfont = font = smallfont = tiny = None
_cpu_stat_prev: tuple[int, int] = (0, 0)

# url-fact panel caching
_remote_fact_cache: dict = {}  # {panel_id: {value, last_fetch_time, interval_secs}}
_refresh_remote_cache_flag: bool = False  # Set by SIGUSR1 handler to invalidate cache


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


# Fonts  --  loaded once on first use, keyed by config name
# ---------------------------------------------------------------------------

# Project root: src/clockish/display.py -> up two levels
_PROJECT_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..')
)

def _find_font(name: str) -> str:
    """Locate a TrueType font file by searching common system directories.

    Search order:
      1. Standard DejaVu location
      2. /usr/share/fonts/truetype/dseg   --  installed by: sudo apt install fonts-dseg
      3. Other system font directories
      4. Alongside this script
      5. Every direct subdirectory of third_party/  --  covers vendored fonts such as
         third_party/dseg/   (scripts/download-dseg-font.sh)
         third_party/nixie/  (scripts/download-nixie-font.sh)
         ... and any future additions automatically.

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

_FONTS: dict = {}
#: True once the named-scale fonts (giant/huge/.../micro) are loaded into
#: _FONTS. Other code (_init_layout()) also writes directly into _FONTS, so
#: checking "is _FONTS empty" isn't a reliable load-once signal -- this flag is.
_SCALE_FONTS_LOADED: bool = False

# ---------------------------------------------------------------------------
# Standard font scale  --  each name maps to a fraction of the display height.
# These built-in names are loaded on first use, using the font file set by
# 'default_font' in the config (falls back to DejaVu Sans).
#
# Scale (8 steps, largest -> smallest):
#   giant  ~ 68%   --  fills a tall clock row
#   huge   ~ 45%
#   big    ~ 30%
#   med    ~ 20%
#   normal ~ 12%   --  date / subtitle rows
#   small  ~  8%
#   tiny   ~  5%   --  info / status rows
#   micro  ~  3%
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
      str ending in '%'    --  percentage of *base*, e.g. "68%"
      str ending in 'px'   --  explicit pixel value, e.g. "40px" (never a fraction)
      float in 0.0 - 1.0     --  fraction of *base*, e.g. 0.68
      int / float > 1.0    --  direct pixel value (rounded to int)

    Plain ints are ALWAYS pixels, even '1'  --  a literal-1-pixel divider row
    is common; treating int 1 as a 1.0 (=100%) fraction would silently blow
    it up to the full display height/width.
    """
    if isinstance(raw, str) and raw.endswith('%'):
        return max(1, int(base * float(raw[:-1]) / 100))
    if isinstance(raw, str) and raw.endswith('px'):
        return max(1, int(round(float(raw[:-2]))))
    if isinstance(raw, int) and not isinstance(raw, bool):
        return max(1, raw)
    f = float(raw)
    if 0.0 <= f <= 1.0:
        return max(1, int(base * f))
    return max(1, int(f))


def _get_font(name: str) -> ImageFont.FreeTypeFont:
    global _SCALE_FONTS_LOADED
    if not _SCALE_FONTS_LOADED:
        # Use 'default_font' from config as the typeface for all built-in
        # scale names; fall back to DejaVu Sans if not set.
        _default_font_file = _config.get('default_font')
        _scale_font_path = _find_font(_default_font_file) if _default_font_file else _FONT_PATH
        for _scale_name, _fraction in BUILTIN_FONT_SCALE.items():
            _px = max(1, int(height * _fraction))
            _FONTS[_scale_name] = ImageFont.truetype(_scale_font_path, _px)
        _SCALE_FONTS_LOADED = True
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



# ---------------------------------------------------------------------------
# Color helper  --  looks up any palette name from pyili9486.colors
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
    print(f"WARNING: unknown color '{name}'  --  defaulting to grey", file=sys.stderr)
    return '#888888'


# ---------------------------------------------------------------------------
# Color resolution  --  walk the config once at startup, replacing every color
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

    ssid = signal_dbm = quality = "n/a"
    connected = False
    try:
        with open("/proc/net/wireless") as f:
            lines = f.readlines()
        for line in lines[2:]:
            parts = line.split()
            if parts:
                quality   = parts[2].rstrip('.')
                signal_dbm    = parts[3].rstrip('.')
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
        ssid = subprocess.check_output(
            ["iwgetid", "-r"], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        ssid = "N/A"
    status      = "WiFi OK" if connected else "No Wifi"
    signal_str  = f"{signal_dbm}dBm" if signal_dbm != "n/a" else "n/a"
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
        # wifi facts  --  raw values from the cached get_wifi_info() tuple
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
# url-fact panel helpers
# ---------------------------------------------------------------------------

def _parse_interval(interval_str: str) -> int:
    """Convert interval string (e.g., '5m', '30s', '1h') to seconds."""
    interval_str = interval_str.strip()
    if interval_str.endswith('s'):
        return int(float(interval_str[:-1]))
    elif interval_str.endswith('m'):
        return int(float(interval_str[:-1]) * 60)
    elif interval_str.endswith('h'):
        return int(float(interval_str[:-1]) * 3600)
    # Fallback to seconds if no unit (shouldn't happen if validator works)
    return int(float(interval_str))

def _extract_value_by_regex(response_text: str, pattern: str) -> str | None:
    """Extract first capture group from regex match, or None if no match."""
    try:
        match = re.search(pattern, response_text)
        if match:
            return match.group(1)
    except Exception:
        pass
    return None

def _extract_value_by_json_path(response_text: str, json_path: str) -> tuple[str | None, bool]:
    """Extract value from JSON response using dot notation path.

    Supports two modes:
    1. Simple key (no dots): extract from first object in JSON
       Example: json_path='tempF' on {"286114a10300004b": {...}} → first object's tempF
    2. Dot notation: navigate nested dicts
       Example: json_path='data.temp' → response['data']['temp']

    Returns:
        (value_str, is_missing_key) tuple
        - value_str: extracted value as string, or None if error
        - is_missing_key: True if JSON key doesn't exist, False if other error
    """
    try:
        data = json.loads(response_text)
        if not isinstance(data, dict):
            return (None, False)

        json_path = json_path.strip()

        # Simple key (no dots): try the root object first, then fall back to
        # scanning one level deep for a wrapped/nested object.
        if '.' not in json_path:
            if json_path in data:
                value = data.get(json_path)
                if value is None:
                    return (None, True)  # Key present but null -- treat as missing
                return (str(value), False)

            # Root key absent: look for it inside the first nested object
            # (e.g. {"<device-id>": {"tempF": 71.8}}).
            for obj in data.values():
                if isinstance(obj, dict):
                    value = obj.get(json_path)
                    if value is None:
                        return (None, True)  # Key missing
                    return (str(value), False)
            # No dict objects found
            return (None, True)

        # Dot notation: traverse nested keys
        keys = json_path.split('.')
        for key in keys:
            if isinstance(data, dict):
                data = data.get(key)
                if data is None:
                    return (None, True)  # Key missing
            else:
                return (None, True)  # Can't traverse non-dict
        return (str(data), False) if data is not None else (None, True)
    except Exception:
        return (None, False)  # Parse error, not missing key

def _log_warning(msg: str) -> None:
    """Log warning to both stderr and syslog (if available)."""
    print(f"WARNING: {msg}", file=sys.stderr)
    if _SYSLOG_AVAILABLE:
        try:
            syslog.syslog(syslog.LOG_WARNING, f"clockish: {msg}")
        except Exception:
            pass

def _fetch_and_extract(url: str, pattern: str | None, json_path: str | None,
                       timeout: int, verify_ssl: bool, fallback: str) -> str:
    """Fetch URL and extract value using pattern or json_path.

    Returns:
    - Extracted value (on success)
    - "?" (if JSON key missing; logs warning)
    - fallback (on network/fetch error)
    """
    try:
        # Create SSL context (ignore certificate for https by default)
        if url.lower().startswith('https://'):
            ctx = ssl.create_default_context()
            if not verify_ssl:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
        else:
            ctx = None

        # Fetch with timeout
        req = urllib.request.Request(url)
        req.add_header('User-Agent', f'clockish/{__version__}')
        if ctx:
            response = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        else:
            response = urllib.request.urlopen(req, timeout=timeout)

        response_text = response.read().decode('utf-8')

        # Extract value
        if pattern:
            value = _extract_value_by_regex(response_text, pattern)
            return value if value is not None else fallback
        else:  # json_path
            value, is_missing_key = _extract_value_by_json_path(response_text, json_path)
            if is_missing_key:
                _log_warning(f"url-fact: JSON key '{json_path}' not found in {url}")
                return "?"
            return value if value is not None else fallback
    except Exception as e:
        if DEBUG:
            print(f"DEBUG: url-fact fetch failed: {url} -> {e}")
        return fallback

def _handle_sigusr1(signum, frame):
    """SIGUSR1 handler: invalidate all remote fact cache entries."""
    global _refresh_remote_cache_flag
    _refresh_remote_cache_flag = True
    if DEBUG:
        print("DEBUG: SIGUSR1 received; invalidating remote fact cache")


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
        return int(bbox[3] - bbox[1])

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
        return int(f.getbbox("Ag|")[1])
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# font_behavior  --  per-row/per-panel control over sizing & vertical centring.
#
#   default        current behaviour: fixed font_size:, ink metrics from the
#                  "Ag|" reference glyphs (ascender+descender+full-height bar).
#   scale          ignore font_size:'s resolved size (keep its resolved font
#                  file); every draw, pick the largest point size where the
#                  text fits BOTH the panel's width and height. Aspect-
#                  preserving (a single TrueType point size scales uniformly).
#   scale_numeric  like 'scale', but ink metrics (used for BOTH the fit search
#                  and vertical centring) come from "0123456789" instead of
#                  the "Ag|" reference -- fixes numeric-only content (clock,
#                  cpu%, temp, ...) which has no descenders like "Ag|" assumes.
#   stretch_y      like 'scale', but constrained by height only -- width may
#                  overflow/clip depending on justify.
#   stretch_x      fixed font_size: (height set once at load, like 'default'),
#                  but every draw, non-uniformly stretches the rendered glyphs
#                  horizontally to exactly fill the panel's width. True
#                  anisotropic stretch -- unlike scale/stretch_y (a single
#                  point size, always uniform), this renders to an offscreen
#                  image and resizes width-only, so it needs the row's Image
#                  object (not just ImageDraw); falls back to 'default' if
#                  that isn't available. 'justify' is moot (always fills the
#                  full width edge-to-edge); use 'padding:' to inset instead.
#
# Resolved once per panel in _init_layout() (row default, panel override) and
# stored back onto the panel dict as 'font_behavior'; renderers just read it.
# ---------------------------------------------------------------------------
KNOWN_FONT_BEHAVIORS: frozenset[str] = frozenset({
    'default', 'scale', 'scale_numeric', 'stretch_y', 'stretch_x',
})

#: id(font) -> (ink_top, ink_h) computed from digits only. Calculated once per
#: font object on first use (never per-draw); font objects are already cached
#: for the lifetime of the process in _FONTS, so this dict stays small.
_NUMERIC_INK_CACHE: dict[int, tuple[int, int]] = {}


def _numeric_ink_metrics(f: ImageFont.FreeTypeFont) -> tuple[int, int]:
    """Ink top/height for this font using digits only (no descenders)."""
    key = id(f)
    cached = _NUMERIC_INK_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        bbox = f.getbbox("0123456789")
        ink_top = int(bbox[1])
        ink_h = int(bbox[3] - bbox[1])
    except Exception:
        ink_top, ink_h = _font_ink_top(f), _font_height(f)
    _NUMERIC_INK_CACHE[key] = (ink_top, ink_h)
    return ink_top, ink_h


#: (font_path, text, avail_w, avail_h, axis) -> fitted FreeTypeFont.
#: Static configs re-render the same text most frames (fact/text panels), so
#: this cache makes 'scale'/'stretch_y' free except when content changes.
_FIT_FONT_CACHE: dict[tuple, ImageFont.FreeTypeFont] = {}


def _fit_font(path: str, text: str, avail_w: int, avail_h: int,
               axis: str, numeric: bool = False) -> ImageFont.FreeTypeFont:
    """Binary-search the largest point size of *path* where *text* fits.

    axis='height': constrain by ink height <= avail_h only (width may overflow).
    axis='both':   constrain by ink height <= avail_h AND text width <= avail_w.
    numeric=True:  measure ink height from "0123456789" (_numeric_ink_metrics)
                   instead of the "Ag|" reference -- used by 'scale_numeric'.
    """
    key = (path, text, avail_w, avail_h, axis, numeric)
    cached = _FIT_FONT_CACHE.get(key)
    if cached is not None:
        return cached

    def _fits(size: int) -> bool:
        f = ImageFont.truetype(path, size)
        if numeric:
            ink_top, ink_h = _numeric_ink_metrics(f)
        else:
            ink_top = _font_ink_top(f)
            ink_h = _font_height(f) - ink_top
        if ink_h > avail_h:
            return False
        if axis == 'both' and f.getbbox(text)[2] > avail_w:
            return False
        return True

    lo, hi, best_size = 1, max(1, avail_h * 2), 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if _fits(mid):
            best_size = mid
            lo = mid + 1
        else:
            hi = mid - 1

    fitted = ImageFont.truetype(path, best_size)
    _FIT_FONT_CACHE[key] = fitted
    return fitted


# ---------------------------------------------------------------------------
# Row layout  --  uses fixed heights from config (required on every row)
# ---------------------------------------------------------------------------
def _measure_rows(rows: list) -> list:
    """Return a list of (row_dict, y, height) tuples.

    Every row MUST have a 'height' key.  The value may be an integer (pixels),
    a float 0.0 - 1.0 (fraction of display height), or a percentage string such
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
            f"by {total - height}px  --  bottom rows will be clipped.",
            file=sys.stderr,
        )

    return result


# ---------------------------------------------------------------------------
# Layout pre-computation  --  runs once at startup; cached in _LAYOUT.
# ---------------------------------------------------------------------------
_LAYOUT: list = []   # list of (row_dict, y, row_height_px)

# Fraction of row height used when font_size is 'auto'.
_AUTO_FONT_FRACTION = 0.75


def _init_layout() -> None:
    """Pre-compute row layout and resolve font references in panel dicts.

    Two ways to specify a font on a panel:

      font_size: <name|%|px|auto>
        Uses default_font (or DejaVu) as the typeface.  Named scale names
        (giant, huge, ...) are pre-loaded at startup.  'auto' sizes to 75% of
        the row height.  '%' and 'Npx' resolve against display height / literal
        pixels respectively; a plain number is treated as a fraction (<=1.0)
        or literal pixels (>1.0) of display height.

      font: <name>   [+ font_size: <name|%|px|auto>]
        'name' is a key in the fonts: section (file-only entry) or the
        built-in 'debug' alias (always DejaVuSans, immune to default_font).
        Size comes from font_size: if given, otherwise defaults to 'auto'.
        At init time the panel dict is rewritten: font_size: receives the
        resolved synthetic key and font: is consumed.

    All resolved fonts are registered in _FONTS under synthetic names so
    renderers just call _get_font(p.get('font_size', 'normal')) as usual.

    Also resolves 'font_behavior' per panel (panel value > row default >
    'default') -- see KNOWN_FONT_BEHAVIORS docs above _fit_font().
    """
    global _LAYOUT
    rows = _config.get('rows', [])
    layout = _measure_rows(rows)   # height-overflow warning printed here, once

    _default_font_file = _config.get('default_font')
    _auto_path = _find_font(_default_font_file) if _default_font_file else _FONT_PATH

    # Map font reference names -> resolved TTF paths.
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
        if isinstance(font_size_raw, str) and font_size_raw.endswith('px'):
            return max(1, int(round(float(font_size_raw[:-2]))))
        if isinstance(font_size_raw, str) and font_size_raw in BUILTIN_FONT_SCALE:
            return max(1, int(height * BUILTIN_FONT_SCALE[font_size_raw]))
        f = float(font_size_raw)
        if 0.0 < f <= 1.0:
            return max(1, int(height * f))
        return max(1, int(f))

    for row_idx, (r, ry, rh) in enumerate(layout):
        row_behavior = r.get('font_behavior')
        for p in r.get('panels', []):
            # Resolve effective font_behavior once: panel override > row
            # default > 'default'. Renderers just read p['font_behavior'].
            behavior = p.get('font_behavior') or row_behavior or 'default'
            if behavior not in KNOWN_FONT_BEHAVIORS:
                behavior = 'default'
            p['font_behavior'] = behavior

            font_ref  = p.get('font')      # which TTF file to use
            font_size = p.get('font_size') # size spec

            if font_ref is not None:
                # font: attribute specified  --  resolve (file, size) -> synthetic key.
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

            elif font_size is not None and font_size not in BUILTIN_FONT_SCALE:
                # No font: attribute, and font_size isn't a bare named scale
                # (giant/huge/...) -- i.e. it's 'auto', an explicit '%'/'px'
                # string, or a plain number. Resolve against default_font.
                # 'auto' is row-relative so it's keyed by row height (rh);
                # everything else resolves to an absolute pixel size, so it's
                # keyed (and cached/shared) by that pixel size instead.
                size_px = _resolve_size(font_size, rh)
                syn_name = f'_auto_{rh}' if font_size == 'auto' else f'_sz_{size_px}'
                if syn_name not in _FONTS:
                    _FONTS[syn_name] = ImageFont.truetype(_auto_path, size_px)
                    if DEBUG:
                        print(f"  font_size '{font_size}' (no font:) -> "
                              f"{size_px}px  (key={syn_name})")
                p['font_size'] = syn_name

            # Named scale (or unset, defaults to 'normal') with no font: ->
            # handled by _get_font() at render time using default_font.

    _LAYOUT = layout

    # Pre-compute panel widths once; warning fires only once here.
    for _wi, (_wr, _wy, _wh) in enumerate(_LAYOUT):
        _wp = _wr.get('panels', [])
        if _wp:
            _wr['_widths'] = _resolve_panel_widths(_wp, width, _wi)

    # Stagger url-fact panel cache initialization to spread fetches over time.
    # Collect all url-fact panels with their intervals.
    url_fact_panels = []
    for _r, _, _ in _LAYOUT:
        for _p in _r.get('panels', []):
            if _p.get('type') == 'url-fact':
                _interval_str = _p.get('interval', '5m')
                _interval_secs = _parse_interval(_interval_str)
                url_fact_panels.append((_p, _interval_secs))

    # Stagger the fetch times so they don't all happen at once.
    # If there are N panels with interval I, space them out over the interval.
    if url_fact_panels:
        now = time.monotonic()
        for _idx, (_p, _interval_secs) in enumerate(url_fact_panels):
            # Offset into the interval window so that panel 0 fetches now,
            # panel 1 fetches after interval/N, etc.
            if len(url_fact_panels) > 1:
                _stagger_offset = (_interval_secs * _idx) / len(url_fact_panels)
            else:
                _stagger_offset = 0
            # Set last_fetch_time in the past so first fetch happens after stagger delay
            _cache_key = id(_p)
            _remote_fact_cache[_cache_key] = {
                'value': _p.get('fallback', 'n/a'),
                'last_fetch_time': now - _interval_secs + _stagger_offset,
                'interval_secs': _interval_secs,
            }
            if DEBUG:
                print(f"  url-fact panel {_idx}: interval={_interval_secs}s, stagger_offset={_stagger_offset:.1f}s")




# ---------------------------------------------------------------------------
# Panel renderers  --  uniform signature: (p, px, py, pw, ph, ...)
#   px, py  = top-left origin of the panel's allocated rectangle
#   pw, ph  = width and height of that rectangle
# ---------------------------------------------------------------------------

def _draw_text_stretch_x(img: Image.Image, px: int, py: int, pw: int, ph: int,
                          text: str, f: ImageFont.FreeTypeFont, color: str,
                          x_offset: int) -> None:
    """Render *text* at its natural (unstretched) size, then non-uniformly
    resize width-only to exactly fill [px+x_offset, px+pw). Height is fixed
    (same ink metrics as 'default'); this is the one font_behavior that needs
    the row's actual Image (not just ImageDraw) since Pillow has no API for
    anisotropic font scaling -- only a full offscreen render + resize achieves
    a horizontal-only stretch. 'justify' is moot (always fills the full
    width); use 'padding:' on the panel to inset instead.
    """
    ink_top = _font_ink_top(f)
    ink_h = max(1, _font_height(f) - ink_top)
    nat_w = max(1, int(f.getbbox(text)[2]))
    avail_w = max(1, pw - x_offset)

    tmp = Image.new('RGBA', (nat_w, ink_h), (0, 0, 0, 0))
    ImageDraw.Draw(tmp).text((0, -ink_top), text, font=f, fill=color)
    stretched = tmp.resize((avail_w, ink_h), Image.Resampling.BILINEAR)

    paste_y = py + max(0, (ph - ink_h) // 2)
    img.paste(stretched, (px + x_offset, paste_y), mask=stretched)


def _draw_text_line(d: ImageDraw.ImageDraw, px: int, py: int, pw: int, ph: int,
                    text: str, f: ImageFont.FreeTypeFont, color: str,
                    x_offset: int = 0, justify: str = 'center',
                    behavior: str = 'default',
                    img: 'Image.Image | None' = None) -> None:
    """Draw a single line of text within the panel rect.

    Vertical placement: always centred within [py, py+ph).
    Horizontal placement controlled by `justify`:
      'center'  --  ink centred within [px+x_offset, px+pw)
      'left'    --  ink starts at px+x_offset
      'right'   --  ink ends at px+pw

    `behavior` (see KNOWN_FONT_BEHAVIORS docs above _fit_font()):
      'default'                 --  use *f* as given (fixed size from font_size:).
      'scale'/'stretch_y'       --  re-fit *f* to (pw-x_offset, ph) every call
                                     via _fit_font(), using f.path as the typeface,
                                     with "Ag|"-reference ink metrics.
      'scale_numeric'           --  like 'scale', but both the fit search and
                                     the vertical-centring ink metrics come
                                     from digits only (0-9), not "Ag|".
      'stretch_x'               --  fixed size from font_size:; anisotropic
                                     horizontal-only resize via `img` (falls
                                     back to 'default' if `img` is None).
    """
    if behavior == 'stretch_x':
        if img is not None:
            _draw_text_stretch_x(img, px, py, pw, ph, text, f, color, x_offset)
            return
        behavior = 'default'  # no Image available -- degrade gracefully

    if behavior in ('scale', 'stretch_y', 'scale_numeric') and getattr(f, 'path', None):
        axis = 'height' if behavior == 'stretch_y' else 'both'
        avail_w = max(1, pw - x_offset)
        f = _fit_font(str(f.path), text, avail_w, ph, axis, numeric=(behavior == 'scale_numeric'))

    if behavior == 'scale_numeric':
        ink_top, ink_h = _numeric_ink_metrics(f)
    else:
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
                         now: datetime.datetime, d: ImageDraw.ImageDraw,
                         img: 'Image.Image | None' = None) -> None:
    color       = p.get('color', _C_WHITE)
    time_f      = _get_font(p.get('font_size', 'normal'))
    _time_format = p.get('time_format')
    if _time_format is None:
        fmt = '%H:%M'
    else:
        fmt = _TIME_FORMATS.get(_time_format, _time_format)
    time_str    = now.strftime(fmt).upper()
    time_str    = apply_transforms(time_str, p.get('transform'), debug=DEBUG)
    label_str   = p.get('label', '')

    if DEBUG_LAYOUT:
        cell_h = _font_height(time_f)
        ink_top = _font_ink_top(time_f)
        ink_h   = cell_h - ink_top
        print(f"      [clock] font_size={p.get('font_size','normal')} cell_h={cell_h} "
              f"ink_top={ink_top} ink_h={ink_h}  str='{time_str}'")

    justify = p.get('justify', 'center')
    behavior = p.get('font_behavior', 'default')
    _draw_text_line(d, px, py, pw, ph, time_str, time_f, color, justify=justify,
                     behavior=behavior, img=img)

    if label_str:
        time_w = int(time_f.getbbox(time_str)[2])
        _draw_text_line(d, px + time_w + 6, py, pw, ph, label_str, time_f, color,
                         justify='left', behavior=behavior, img=img)


def _render_date_panel(p: dict, px: int, py: int, pw: int, ph: int,
                        now: datetime.datetime, d: ImageDraw.ImageDraw,
                        img: 'Image.Image | None' = None) -> None:
    color    = p.get('color', _C_WHITE)
    date_f   = _get_font(p.get('font_size', 'normal'))
    date_str = now.strftime(p.get('date_format', '%a %b %-d, %Y'))
    date_str = apply_transforms(date_str, p.get('transform'), debug=DEBUG)

    if DEBUG_LAYOUT:
        cell_h  = _font_height(date_f)
        ink_top = _font_ink_top(date_f)
        ink_h   = cell_h - ink_top
        print(f"      [date]  font_size={p.get('font_size','normal')} cell_h={cell_h} "
              f"ink_top={ink_top} ink_h={ink_h}  str='{date_str}'")

    _draw_text_line(d, px, py, pw, ph, date_str, date_f, color,
                    x_offset=4, justify=p.get('justify', 'center'),
                    behavior=p.get('font_behavior', 'default'), img=img)


def _render_fact_panel(p: dict, px: int, py: int, pw: int, ph: int,
                        d: ImageDraw.ImageDraw,
                        img: 'Image.Image | None' = None) -> None:
    f      = _get_font(p.get('font_size', 'normal'))
    color  = p.get('color', _C_WHITE)
    source = p['source']
    value  = _get_fact(source)
    value  = apply_transforms(value, p.get('transform'), debug=DEBUG)

    if 'label' not in p:
        text = value
    elif p['label'] == 'default':
        text = _FACT_DEFAULT_LABELS.get(source, '') + value
    else:
        text = p['label'] + value

    _draw_text_line(d, px, py, pw, ph, text, f, color, justify=p.get('justify', 'center'),
                    behavior=p.get('font_behavior', 'default'), img=img)


def _render_url_fact_panel(p: dict, px: int, py: int, pw: int, ph: int,
                            d: ImageDraw.ImageDraw,
                            img: 'Image.Image | None' = None) -> None:
    """Render a url-fact panel: fetch from URL, extract, cache, display."""
    global _refresh_remote_cache_flag, _remote_fact_cache

    f      = _get_font(p.get('font_size', 'normal'))
    color  = p.get('color', _C_WHITE)
    url    = p.get('url', '')
    pattern = p.get('pattern')
    json_path = p.get('json_path')
    interval_str = p.get('interval', '5m')
    timeout = p.get('timeout', 5)
    verify_ssl = p.get('verify_ssl', False)
    fallback = p.get('fallback', 'n/a')
    label = p.get('label', '')

    # Parse interval to seconds
    interval_secs = _parse_interval(interval_str)

    # Generate cache key (use id() of the panel dict as unique ID)
    cache_key = id(p)

    # Check if cache needs refresh (SIGUSR1 was received)
    if _refresh_remote_cache_flag:
        _remote_fact_cache.clear()
        _refresh_remote_cache_flag = False

    # Check cache; fetch if expired
    now = time.monotonic()
    if cache_key not in _remote_fact_cache:
        # First fetch: happens immediately
        value = _fetch_and_extract(url, pattern, json_path, timeout, verify_ssl, fallback)
        _remote_fact_cache[cache_key] = {'value': value, 'last_fetch_time': now, 'interval_secs': interval_secs}
    else:
        cache_entry = _remote_fact_cache[cache_key]
        last_fetch = cache_entry['last_fetch_time']
        if now - last_fetch >= interval_secs:
            # Interval expired: fetch fresh value
            value = _fetch_and_extract(url, pattern, json_path, timeout, verify_ssl, fallback)
            cache_entry['value'] = value
            cache_entry['last_fetch_time'] = now
        else:
            # Use cached value
            value = cache_entry['value']

    # Apply transforms to the raw cached value each render (cheap; lets
    # config edits to 'transform:' take effect without cache invalidation).
    value = apply_transforms(value, p.get('transform'), debug=DEBUG)

    # Render like fact panel
    if label:
        text = label + value
    else:
        text = value

    _draw_text_line(d, px, py, pw, ph, text, f, color, justify=p.get('justify', 'center'),
                    behavior=p.get('font_behavior', 'default'), img=img)


def _render_text_panel(p: dict, px: int, py: int, pw: int, ph: int,
                        d: ImageDraw.ImageDraw,
                        img: 'Image.Image | None' = None) -> None:
    """Static text panel  --  renders p['label'], optionally transformed."""
    label = apply_transforms(p.get('label', ''), p.get('transform'), debug=DEBUG)
    _draw_text_line(d, px, py, pw, ph,
                    label,
                    _get_font(p.get('font_size', 'normal')),
                    p.get('color', _C_WHITE),
                    justify=p.get('justify', 'center'),
                    behavior=p.get('font_behavior', 'default'), img=img)



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
            # -50 or better -> 4, -60 -> 3, -70 -> 2, weaker -> 1
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
    # No padding  --  the arc geometry already leaves enough visual margin.
    draw_w    = pw
    draw_h    = ph

    # The graphic needs: width = 2*R_max, height = R_max  (fan height = radius)
    # So fit into (draw_w, draw_h):  R = min(draw_w//2, draw_h)
    R_max     = max(4, min(draw_w // 2, draw_h))

    # Stroke width: proportional to R_max, at least 1
    stroke    = max(1, R_max // 8)

    num_arcs  = 3   # 3 arcs -> 4 levels (0 + one per arc)
    dot_r     = max(2, R_max // 7)
    gap       = max(1, R_max // 12)   # gap between dot edge and first arc
    # Distribute arcs evenly in (dot_r + gap .. R_max)
    arc_band  = R_max - dot_r - gap
    r_step    = arc_band // num_arcs if num_arcs else arc_band

    # Centre the graphic: fan occupies R_max wide (x2) and R_max tall
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
    # arc i (0-based) lights when level > i  (arc0 -> level>=1, arc1 -> level>=2, ...)
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
    line_h = _resolve_dimension(p.get('height', 2), ph)
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
                    target_draw: ImageDraw.ImageDraw,
                    target_img: 'Image.Image | None' = None) -> None:
    """Dispatch to the correct panel renderer based on panel type.

    'padding:' (integer px, all 4 sides, default 1) insets the rect passed to
    the specific renderer below -- background fill above still covers the
    full, unpadded panel cell.
    """
    # Fill panel background if explicitly set (overrides row background).
    if 'background' in p:
        target_draw.rectangle((px, py, px + pw - 1, py + ph - 1),
                               fill=p['background'])

    pad = p.get('padding', 1)
    if not isinstance(pad, (int, float)) or isinstance(pad, bool) or pad < 0:
        pad = 1
    pad = int(pad)
    px, py = px + pad, py + pad
    pw, ph = max(1, pw - 2 * pad), max(1, ph - 2 * pad)

    pt = p.get('type', '')
    if pt == 'clock':
        _render_clock_panel(p, px, py, pw, ph, tz_cache[p.get('timezone', 'local')], target_draw, target_img)
    elif pt == 'date':
        _render_date_panel(p, px, py, pw, ph, tz_cache[p.get('timezone', 'local')], target_draw, target_img)
    elif pt == 'fact':
        _render_fact_panel(p, px, py, pw, ph, target_draw, target_img)
    elif pt == 'url-fact':
        _render_url_fact_panel(p, px, py, pw, ph, target_draw, target_img)
    elif pt == 'text':
        _render_text_panel(p, px, py, pw, ph, target_draw, target_img)
    elif pt == 'divider':
        _render_divider_panel(p, px, py, pw, ph, target_draw)
    elif pt == 'wifi_graphic':
        _render_wifi_graphic_panel(p, px, py, pw, ph, target_draw)
    elif pt == 'debug':
        _render_debug_panel(p, px, py, pw, ph, timings, t0, target_draw)
    # 'blank'  --  space reserved, nothing to draw


def _resolve_panel_widths(panels: list, row_w: int, row_idx: int) -> list[int]:
    """Compute pixel width for each panel in a row.

    Each panel may specify 'width' as:
      - integer pixels:  width: 120
      - "Npx" string:    width: "44px"  (explicit pixels, never a fraction)
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
        else:
            fixed_widths[i] = _resolve_dimension(raw, row_w)

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
            f"exceeds row width {row_w}px by {total - row_w}px  --  rightmost panels clipped.",
            file=sys.stderr,
        )
    return widths




def _render_row(r: dict, row_idx: int, ry: int, rw: int, rh: int,
                tz_cache: dict, timings: dict, t0: float) -> None:
    """Render a row: panels are laid out left-to-right with computed widths.

    Every row is treated the same regardless of panel count  --  a single-panel
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

    widths = r.get('_widths') or _resolve_panel_widths(panels, rw, row_idx)
    if DEBUG_LAYOUT:
        auto_px = max(1, int(rh * _AUTO_FONT_FRACTION))
        print(f"row {row_idx} '{row_name}': ry={ry} rh={rh} rw={rw}  "
              f"panels={len(panels)} widths={widths}  "
              f"auto={auto_px}px ({_AUTO_FONT_FRACTION*100:.0f}% of {rh}px)")
    px = 0
    for p, pw in zip(panels, widths):
        if DEBUG_LAYOUT:
            print(f"  panel type={p.get('type','?')} px={px} pw={pw}")
        _dispatch_panel(p, px, 0, pw, rh, tz_cache, timings, t0, row_draw, row_img)
        px += pw

    image.paste(row_img, (0, ry))


# ---------------------------------------------------------------------------
# Main display function  --  called once per second
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
# One-time initialization  --  called by main() before the display loop.
# ---------------------------------------------------------------------------
def _init() -> None:
    """Parse args, load config, init hardware, fonts, colors, and layout.

    All module-level globals used by renderers are set here.  Nothing outside
    _init() / main() should depend on them being available at import time.
    """
    global _args, DEBUG, DEBUG_LAYOUT
    global _config, _display_cfg, width, height, rotation
    global image, draw, padding, top, bottom, x
    global lcd, _orientation
    global _FONT_PATH
    global _C_WHITE, _C_DARKGREY, _C_GREY, _C_GREEN, _C_BROWN, _C_BLACK
    global bigfont, medfont, font, smallfont, tiny
    global _cpu_stat_prev

    # --- Arguments ---------------------------------------------------------
    _args = _parser.parse_args()
    DEBUG = _args.debug
    DEBUG_LAYOUT = _args.debug_layout

    # --- Config ------------------------------------------------------------
    _config = _load_config(_args.config)

    # Validate config at startup: print all issues; errors are prominent but
    # do NOT abort startup (clockish will attempt to run in a degraded state).
    try:
        from clockish.config_validator import validate_config_dict as _validate_cfg
        _cfg_path = _args.config or _DEFAULT_CONFIG
        _vr = _validate_cfg(_config, path=_cfg_path, run_yamllint=False)
        if _vr.issues:
            _vr.print_summary(file=sys.stderr)
            if _vr.has_errors:
                print(
                    "WARNING: config has errors (listed above). "
                    "clockish will attempt to continue but may behave unexpectedly.",
                    file=sys.stderr,
                )
    except Exception as _ve:
        print(f"WARNING: config validation failed unexpectedly: {_ve}", file=sys.stderr)

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

    # --- Display dimensions and PIL canvas ---------------------------------
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

    # --- Hardware ----------------------------------------------------------
    lcd = load_driver(_display_cfg).begin()
    print(f'Initialized display: {width}x{height} rotation={rotation}, '
          f'landscape={lcd.is_landscape}, dimensions={lcd.dimensions}')

    _orientation = _config.get('orientation')
    if _orientation:
        print(f"Layout orientation hint: {_orientation}")

    # --- Fonts -------------------------------------------------------------
    _FONT_PATH = _find_font('DejaVuSans.ttf')

    bigfont   = _get_font('big')
    medfont   = _get_font('med')
    font      = _get_font('normal')
    smallfont = _get_font('small')
    tiny      = _get_font('tiny')

    if DEBUG:
        print("Font metrics:")
        dump_font_metrics()

    # --- Colors ------------------------------------------------------------
    _C_WHITE    = _color('WHITE')
    _C_DARKGREY = _color('DARKGREY')
    _C_GREY     = _color('GREY')
    _C_GREEN    = _color('GREEN')
    _C_BROWN    = _color('BROWN')
    _C_BLACK    = _color('BLACK')
    _resolve_colors(_config)

    # --- CPU stats (prime for first get_cpu_percent() call) ----------------
    _cpu_stat_prev = _read_cpu_stat()

    # --- Layout (calls _resolve_panel_widths; safe here since all fns defined) --
    _init_layout()

    # --- Register signal handlers -------------------------------------------
    # SIGUSR1: invalidate url-fact cache (gracefully ignored on Windows)
    try:
        signal.signal(signal.SIGUSR1, _handle_sigusr1)
        if DEBUG:
            print("SIGUSR1 signal handler registered for url-fact cache invalidation")
    except (AttributeError, ValueError):
        # Windows doesn't have SIGUSR1; silently continue
        pass


# ---------------------------------------------------------------------------
# Entry point  --  called by the `clockish` console script and by __main__.py
# ---------------------------------------------------------------------------
def main():
    """Parse args, initialize hardware, then run the display loop."""
    global DEBUG_LAYOUT
    _init()
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
        # Do not blank the display on exit  --  lets you see where it stopped.
        # GPIO.cleanup() is not needed  --  rpi-lgpio facade handles it.
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
# Main loop  --  call show_rows() once per second
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    main()
