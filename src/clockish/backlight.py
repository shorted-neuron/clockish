"""Backlight brightness control -- optional `display.backlight:` config block.

Runs a background thread that computes the current brightness level -- from
EITHER a fixed day/night `schedule:` list OR a sun-following `curve: sun`
(mutually exclusive; see config_validator.py) -- and writes it to the
display's backlight control device. See AGENTS.md ("backlight") and
configs/display/framebuffer.yaml for the config shape and worked examples.

Only one control `method` exists today: 'sysfs' (writes an integer 0-255 to
a /sys/class/backlight/<device>/brightness file -- the standard Linux
backlight-class interface most DSI panels expose). The `method` key and the
per-method dispatch table (`_METHODS` below) exist so other methods (e.g. a
GPIO on/off pin, or PWM) can be added later without reshaping this module --
none of that is implemented yet.

`curve: sun` needs today's sunrise/sunset, which display.py's background
`_sun_times_worker` already fetches into its own `_SUN_TIMES` global --
rather than importing display.py here (circular import; it already imports
this module, plus it pulls in hardware-driver code this module has no
business depending on), `start_backlight()` takes a `get_sun_times`
callable that display.py wires up to read its own state.

TODO: st7789 (and other non-sysfs) backlight control -- different hardware
access, path/method TBD; needs testing against real hardware.
"""

import datetime
import math
import threading
from collections.abc import Callable

DEBUG: bool = False

#: Signature display.py's `get_sun_times` callable must match: return
#: today's (sunrise, sunset) as a pair of datetimes (naive or tz-aware, but
#: matching each other), or None if not known yet.
GetSunTimes = Callable[[], tuple[datetime.datetime, datetime.datetime] | None]

_get_sun_times: GetSunTimes | None = None

_CHECK_INTERVAL_SECS = 600  # 10 minutes -- see AGENTS.md, no need for finer granularity

_backlight_thread: threading.Thread | None = None
_backlight_stop_event = threading.Event()
_backlight_wake_event = threading.Event()
_last_written_value: int | None = None


def _parse_hhmm(value: str) -> int:
    """Parse an 'HH:MM' string into minutes-since-midnight."""
    hh, mm = value.split(':')
    return int(hh) * 60 + int(mm)


def _in_range(now_min: int, start_min: int, end_min: int) -> bool:
    """Whether now_min falls in [start_min, end_min] (inclusive), wrapping past midnight if end < start."""
    if start_min <= end_min:
        return start_min <= now_min <= end_min
    return now_min >= start_min or now_min <= end_min


def resolve_scheduled_value(
    schedule: list[dict],
    min_: int,
    max_: int,
    now: datetime.datetime | None = None,
) -> int:
    """Return the brightness value (int, 0-255) the schedule specifies for `now`.

    Time not covered by any schedule entry falls back to the midpoint
    between min_ and max_, rounded to the nearest int.
    """
    now = now or datetime.datetime.now()
    now_min = now.hour * 60 + now.minute
    for entry in schedule:
        start_min = _parse_hhmm(entry['start'])
        end_min = _parse_hhmm(entry['end'])
        if _in_range(now_min, start_min, end_min):
            return int(entry['value'])
    return round((min_ + max_) / 2)


def resolve_sun_curve_value(
    sunrise: datetime.datetime,
    sunset: datetime.datetime,
    min_: int,
    max_: int,
    now: datetime.datetime | None = None,
) -> int:
    """Return the brightness value (int) for a sun-following `curve: sun`.

    Below the horizon (before sunrise or at/after sunset): `min_` -- see
    AGENTS.md for why night uses min rather than off_value or a separate key.
    Above the horizon: a cosine ease from `min_` at sunrise/sunset up to
    `max_` at solar noon (the midpoint between sunrise and sunset), so
    brightness changes smoothly with no visible slope kinks.
    """
    if now is None:
        now = datetime.datetime.now(tz=sunrise.tzinfo) if sunrise.tzinfo is not None else datetime.datetime.now()

    if now < sunrise or now >= sunset:
        return min_

    daylight_secs = (sunset - sunrise).total_seconds()
    if daylight_secs <= 0:
        return min_

    t = (now - sunrise).total_seconds() / daylight_secs  # 0 at sunrise, 1 at sunset
    # Full-period cosine bump: 0 at t=0 and t=1 (matches the flat min_ region
    # either side with zero slope, so there's no kink at sunrise/sunset), 1 at
    # t=0.5 (solar noon). A half-period (1 - cos(pi*t))/2 would be a monotonic
    # sunrise-to-sunset ramp instead -- not what "peaks at solar noon" means.
    return round(min_ + (max_ - min_) * (1 - math.cos(2 * math.pi * t)) / 2)


def _write_sysfs(device: str, value: int) -> bool:
    """Write `value` to /sys/class/backlight/<device>/brightness. Returns success."""
    path = f"/sys/class/backlight/{device}/brightness"
    try:
        with open(path, 'w') as f:
            f.write(str(value))
        return True
    except OSError as e:
        print(f"WARNING: backlight: failed writing {value} to {path}: {e}")
        return False


_METHODS = {
    'sysfs': _write_sysfs,
}


def _resolve_value(cfg: dict, now: datetime.datetime | None = None) -> int:
    """Dispatch to the fixed schedule or the sun-following curve, whichever `cfg` uses.

    `now` defaults to the real current time; pass one to resolve the value
    for an arbitrary moment (scripts/backlight_hardware_test.py simulates a
    whole day this way).
    """
    min_, max_ = cfg.get('min', 0), cfg.get('max', 255)

    if cfg.get('curve') == 'sun':
        sun_times = _get_sun_times() if _get_sun_times is not None else None
        if sun_times is None:
            if DEBUG:
                print("DEBUG: backlight: curve: sun but sun times not available yet, using midpoint")
            return round((min_ + max_) / 2)
        sunrise, sunset = sun_times
        return resolve_sun_curve_value(sunrise, sunset, min_=min_, max_=max_, now=now)

    return resolve_scheduled_value(cfg.get('schedule', []), min_=min_, max_=max_, now=now)


def _apply(cfg: dict, now: datetime.datetime | None = None) -> None:
    """Compute the currently-due value and write it if it changed since the last write.

    `now` defaults to the real current time (what the worker thread uses);
    pass one to apply the value due at a simulated moment.
    """
    global _last_written_value
    method = cfg.get('method')
    write_fn = _METHODS.get(method)
    if write_fn is None:
        if DEBUG:
            print(f"DEBUG: backlight: unknown or unimplemented method {method!r}, skipping")
        return

    value = _resolve_value(cfg, now=now)
    if value == _last_written_value:
        return

    device = cfg.get('device', '')
    if write_fn(device, value):
        _last_written_value = value
        if cfg.get('logging', False):
            print(f"backlight: brightness -> {value} (device={device})")


def _backlight_worker(cfg: dict) -> None:
    """Background daemon: re-check the schedule every _CHECK_INTERVAL_SECS.

    Woken early by _backlight_wake_event.set() (e.g., on config reload).
    """
    event = _backlight_wake_event
    stop_event = _backlight_stop_event
    while not stop_event.is_set():
        if event.wait(timeout=_CHECK_INTERVAL_SECS):
            # woke early (config reload) -> clear and loop to re-apply immediately
            event.clear()
            if stop_event.is_set():
                break
            _apply(cfg)
            continue
        # timeout expired -> time to re-check the schedule
        _apply(cfg)


def start_backlight(display_cfg: dict, get_sun_times: GetSunTimes | None = None) -> None:
    """Start the backlight scheduler thread, if `display_cfg['backlight']` is present.

    Applies the current scheduled value synchronously before returning, so a
    restart never leaves the backlight at a stale level (e.g. full brightness
    at 2am) until the first background tick.

    `get_sun_times` is only needed for `curve: sun` configs -- see the
    module docstring for why it's a callable instead of an import.
    """
    global _backlight_thread, _get_sun_times
    cfg = display_cfg.get('backlight')
    if not cfg:
        return

    _get_sun_times = get_sun_times
    stop_backlight()
    _backlight_stop_event.clear()
    _backlight_wake_event.clear()

    _apply(cfg)  # synchronous initial write, before the first frame is ever shown

    t = threading.Thread(target=_backlight_worker, args=(cfg,), daemon=True)
    _backlight_thread = t
    t.start()


def stop_backlight() -> None:
    global _backlight_thread
    try:
        _backlight_stop_event.set()
        _backlight_wake_event.set()  # wake to let it exit quickly
        if _backlight_thread is not None:
            _backlight_thread.join(timeout=5)
    except Exception:
        pass
    _backlight_thread = None
