#!/usr/bin/env python3
# -*- coding: utf-8 -*-

VERSION = "26.6.0"

import sys
import os
import argparse

# (sys.path manipulation removed — clockish is now a proper installed package)

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
from spidev import SpiDev
from pyili9486 import ILI9486, Origin, SKU
from pyili9486.gpio.rpilgpio_facade import RPiLGPIOFacade
from clockish.colors import rgb_to_hex, BY_NAME

# ---------------------------------------------------------------------------
# Hardware configuration for ILI9486 display on Raspberry Pi
# Adjust pin numbers to match your wiring if needed.
# ---------------------------------------------------------------------------

# SPI bus and device (typically SPI0, CE0)
SPI_BUS = 0
SPI_DEVICE = 0

# GPIO pin numbers (BCM numbering)
DC_PIN = 24    # Data/Command pin
RST_PIN = 25   # Reset pin

spi: SpiDev

# ---------------------------------------------------------------------------
# Debug flag — set by -d / --debug command-line argument
# ---------------------------------------------------------------------------
def _find_default_config() -> str:
    """Search for clockish.yaml in order of preference."""
    candidates = [
        # 1. Next to this file (old fork layout — kept for dev convenience)
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'clockish.yaml'),
        # 2. configs/ directory relative to project root (clockish src-layout)
        os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'configs', 'clockish.yaml'),
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

_config: dict = _load_config(_args.config)


# ---------------------------------------------------------------------------
# Display dimensions, rotation, and PIL canvas — read from config
# ---------------------------------------------------------------------------
_display_cfg = _config.get('display', {})
width    = _display_cfg.get('width',    320)
height   = _display_cfg.get('height',   480)
rotation = _display_cfg.get('rotation', 0)

# Map user-friendly rotation degrees to pyili9486 Origin constants.
# The ILI9486 library uses the origin corner to determine orientation.
_ROTATION_TO_ORIGIN = {
    0:   Origin.UPPER_LEFT,
    90:  Origin.UPPER_RIGHT,
    180: Origin.LOWER_RIGHT,
    270: Origin.LOWER_LEFT,
}
_origin = _ROTATION_TO_ORIGIN.get(rotation, Origin.UPPER_RIGHT)

# Display SKU — controls pixel format and init sequence.
# MPI3501: standard 3.5" RPi display (RGB666, default)
# MHS3528: alternate 3.5" RPi display (RGB565)
# Set 'sku' in the [display] section of your config file if needed.
_SKU_MAP = {'MPI3501': SKU.MPI3501, 'MHS3528': SKU.MHS3528}
_sku = _SKU_MAP.get(_display_cfg.get('sku', 'MPI3501').upper(), SKU.MPI3501)

image = Image.new("RGB", (width, height))
draw  = ImageDraw.Draw(image)
draw.rectangle((0, 0, width, height), outline=0, fill=0)

padding = 0
top     = padding
bottom  = height - padding
x       = 0

# ---------------------------------------------------------------------------
# Hardware init
# ---------------------------------------------------------------------------
# RPiLGPIOFacade handles GPIO.setmode + pin setup internally.
_gpio = RPiLGPIOFacade(dc_pin=DC_PIN, rs_pin=RST_PIN)
spi = SpiDev(SPI_BUS, SPI_DEVICE)
spi.mode = 0b10
spi.max_speed_hz = 64000000
lcd = ILI9486(spi=spi, gpio_facade=_gpio, origin=_origin, sku=_sku).begin()
print(f'Initialized display: {width}x{height} rotation={rotation}, landscape={lcd.is_landscape}, dimensions={lcd.dimensions}')

# ---------------------------------------------------------------------------
# Fonts — loaded once on first use, keyed by config name
# ---------------------------------------------------------------------------
def _find_font(name: str) -> str:
    """Locate a TrueType font file by searching common system directories.

    Falls back to the bare filename (Pillow will try its own search paths).
    """
    search_dirs = [
        '/usr/share/fonts/truetype/dejavu',
        '/usr/share/fonts/truetype',
        '/usr/share/fonts',
        '/usr/local/share/fonts',
        os.path.dirname(os.path.abspath(__file__)),   # alongside this script
    ]
    for d in search_dirs:
        candidate = os.path.join(d, name)
        if os.path.isfile(candidate):
            return candidate
    return name   # fall back; Pillow will raise a clear error if not found

_FONT_PATH = _find_font('DejaVuSans.ttf')
_FONTS: dict = {}

def _get_font(name: str) -> ImageFont.FreeTypeFont:
    if not _FONTS:
        _FONTS['huge']   = ImageFont.truetype(_FONT_PATH, 160)
        _FONTS['big']    = ImageFont.truetype(_FONT_PATH, 48)
        _FONTS['med']    = ImageFont.truetype(_FONT_PATH, 36)
        _FONTS['normal'] = ImageFont.truetype(_FONT_PATH, 28)
        _FONTS['small']  = ImageFont.truetype(_FONT_PATH, 20)
        _FONTS['tiny']   = ImageFont.truetype(_FONT_PATH, 14)
    return _FONTS.get(name, _FONTS['normal'])


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
        'version':      lambda: VERSION,
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

    Every row MUST have a 'height' key.  Warns on stderr if the total
    height exceeds the display height; content that overflows is clipped.
    """
    result = []
    y = top
    for r in rows:
        h = int(r.get('height', 30))
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
    colors_cfg  = p.get('colors', {})
    time_color  = colors_cfg.get('time',  _C_WHITE)
    label_color = colors_cfg.get('label', _C_WHITE)
    time_f      = _get_font(p.get('time_font',  'normal'))
    label_f     = _get_font(p.get('label_font', 'normal'))
    fmt         = _TIME_FORMATS.get(p.get('time_format', '24h'), '%H:%M')
    time_str    = now.strftime(fmt).upper()
    label_str   = p.get('label', '')

    if DEBUG_LAYOUT:
        cell_h = _font_height(time_f)
        ink_top = _font_ink_top(time_f)
        ink_h   = cell_h - ink_top
        print(f"      [clock] font={p.get('time_font','normal')} cell_h={cell_h} "
              f"ink_top={ink_top} ink_h={ink_h}  str='{time_str}'")

    justify = p.get('justify', 'center')
    _draw_text_line(d, px, py, pw, ph, time_str, time_f, time_color, justify=justify)

    if label_str:
        time_w = time_f.getbbox(time_str)[2]
        _draw_text_line(d, px + time_w + 6, py, pw, ph, label_str, label_f, label_color, justify='left')


def _render_date_panel(p: dict, px: int, py: int, pw: int, ph: int,
                        now: datetime.datetime, d: ImageDraw.ImageDraw) -> None:
    colors_cfg = p.get('colors', {})
    date_color = colors_cfg.get('date', _C_WHITE)
    date_f     = _get_font(p.get('date_font', 'normal'))
    date_str   = now.strftime(p.get('date_format', '%a %b %-d, %Y'))

    if DEBUG_LAYOUT:
        cell_h  = _font_height(date_f)
        ink_top = _font_ink_top(date_f)
        ink_h   = cell_h - ink_top
        print(f"      [date]  font={p.get('date_font','normal')} cell_h={cell_h} "
              f"ink_top={ink_top} ink_h={ink_h}  str='{date_str}'")

    _draw_text_line(d, px, py, pw, ph, date_str, date_f, date_color,
                    x_offset=4, justify=p.get('justify', 'center'))


def _render_fact_panel(p: dict, px: int, py: int, pw: int, ph: int,
                        d: ImageDraw.ImageDraw) -> None:
    f      = _get_font(p.get('font', 'normal'))
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
                    _get_font(p.get('font', 'normal')),
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
    font_h  = _font_height(tiny)
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
        #if i == 0 and font_h > ph:
        #    break
        #if i > 0 and row_h < font_h:
        #    break
        d.text((px, y), line, font=tiny, fill=c)


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
        print(f"row {row_idx} '{row_name}': ry={ry} rh={rh} rw={rw}  "
              f"panels={len(panels)} widths={widths}")
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
        for r in _config.get('rows', []):
            for p in r.get('panels', []):
                if p.get('type') in ('clock', 'date'):
                    tz = p.get('timezone', 'local')
                    if tz not in tz_cache:
                        tz_cache[tz] = _now_in_tz(tz)

    layout = _measure_rows(_config.get('rows', []))

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
# Entry point (callable by `clockish` CLI command)
# ---------------------------------------------------------------------------
def main():
    """Entry point — delegates to the module-level __main__ block."""
    import runpy
    runpy.run_module('clockish.display', run_name='__main__', alter_sys=True)


# ---------------------------------------------------------------------------
# Legacy / alternate display functions
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
    try:
        lcd.idle()
        lcd.idle(False)   # idle(True) can cause fonts/colors to render weirdly

        if DEBUG_LAYOUT:
            # Single render then exit so debug output is easy to capture.
            show_rows()
            sys.exit(0)

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
        # Do not blank the display... if program exits we want to see when it got stuck
        # blank()
        # lcd.display(image)
        # GPIO.cleanup() is not needed — rpi-lgpio facade does not require it.  will it cause the LCD to blank-to-white?
        spi.close()
