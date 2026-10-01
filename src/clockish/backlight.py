"""Backlight brightness control -- optional `display.backlight:` config block.

Runs a background thread that computes the current brightness level from the
`schedule:` key -- EITHER a fixed day/night list of entries, OR one of the
scalars in SUN_SCHEDULE_VALUES for a sun-following curve -- and writes it to
the display's backlight control device. See AGENTS.md ("backlight") and
configs/display/framebuffer.yaml for the config shape and worked examples.

Only one control `method` exists today: 'sysfs' (writes an integer 0-255 to
a /sys/class/backlight/<device>/brightness file -- the standard Linux
backlight-class interface most DSI panels expose). The `method` key and the
per-method dispatch table (`_METHODS` below) exist so other methods (e.g. a
GPIO on/off pin, or PWM) can be added later without reshaping this module --
none of that is implemented yet.

A sun schedule needs today's sunrise/sunset, which display.py's background
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

#: `schedule:` scalars that mean "follow the sun" instead of a fixed list of
#: time-of-day entries. Three spellings of one thing, so a config reads however
#: its author thinks of it. config_validator.py imports this -- the single
#: definition (this module is stdlib-only, so the import is cheap and one-way).
SUN_SCHEDULE_VALUES: frozenset[str] = frozenset({'sun', 'follow-sun', 'sun-curve'})

#: Sun-curve shape (see resolve_sun_curve_value()).
#:
#: TWILIGHT_MINUTES -- how long before sunrise the ramp up starts, and how
#: long after sunset the ramp down finishes. 50 minutes is roughly the end of
#: nautical twilight at mid latitudes: it is already meaningfully light before
#: the sun clears the horizon and still light after it drops below, so a
#: backlight that only moves between sunrise and sunset lags the actual room.
#:
#: PEAK_FRACTION -- fraction of the sunrise-to-sunset span spent ramping up to
#: max (mirrored at the other end). 0.25 means full brightness for the middle
#: half of the day, which is the point of the shape.
TWILIGHT_MINUTES = 50
PEAK_FRACTION = 0.25

#: The `backlight:` block currently driving the display, set by
#: start_backlight(). Kept so current_percent() can report against the same
#: min/max/device the scheduler is writing to.
_active_cfg: dict | None = None

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
    twilight_minutes: int = TWILIGHT_MINUTES,
    peak_fraction: float = PEAK_FRACTION,
) -> int:
    r"""Return the brightness value (int) for a sun-following schedule.

    An eased trapezoid, not a bump -- the point is to sit at `max_` for most
    of the day and spend as little time as possible in between:

        min_ ______/^^^^^^^^^^^^^^^^^^^^^\______ min_
                  ^         plateau       ^
        sunrise - twilight             sunset + twilight

      - `min_` at night: before sunrise - twilight_minutes, and at/after
        sunset + twilight_minutes (see AGENTS.md for why night uses min
        rather than off_value or a separate key)
      - ramping up from there to `max_` at `peak_fraction` of the way from
        sunrise to sunset (default 25%)
      - flat `max_` through the middle of the day, until `peak_fraction`
        of the daylight is left
      - mirrored ramp back down, reaching `min_` twilight_minutes past sunset

    Each ramp is a half-period cosine ease over its own span, which has zero
    slope at BOTH ends -- so it leaves the flat night level and meets the flat
    plateau without a visible kink at either junction. (Applying that same
    half-period cosine across the whole sunrise-to-sunset span, rather than
    per ramp, is the wrong shape: it is a monotonic all-day climb that peaks
    at sunset. See AGENTS.md.)
    """
    if now is None:
        now = datetime.datetime.now(tz=sunrise.tzinfo) if sunrise.tzinfo is not None else datetime.datetime.now()

    daylight_secs = (sunset - sunrise).total_seconds()
    if daylight_secs <= 0:
        return min_

    # A peak_fraction of 0.5 or more would leave no plateau at all (the two
    # ramps would cross); clamp so the shape stays well defined.
    peak_fraction = min(max(peak_fraction, 0.0), 0.5)
    shoulder = datetime.timedelta(seconds=daylight_secs * peak_fraction)
    twilight = datetime.timedelta(minutes=max(twilight_minutes, 0))

    ramp_start = sunrise - twilight
    peak_start = sunrise + shoulder
    peak_end = sunset - shoulder
    ramp_end = sunset + twilight

    if now < ramp_start or now >= ramp_end:
        return min_
    if peak_start <= now < peak_end:
        return max_

    if now < peak_start:
        span = (peak_start - ramp_start).total_seconds()
        elapsed = (now - ramp_start).total_seconds()
    else:
        span = (ramp_end - peak_end).total_seconds()
        elapsed = (ramp_end - now).total_seconds()
    if span <= 0:  # zero-length ramp (twilight 0 and peak_fraction 0)
        return max_

    u = elapsed / span  # 0 at the night end of the ramp, 1 at the plateau end
    return round(min_ + (max_ - min_) * (1 - math.cos(math.pi * u)) / 2)


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


def _read_sysfs(device: str) -> int | None:
    """Read /sys/class/backlight/<device>/brightness. None if unreadable."""
    try:
        with open(f"/sys/class/backlight/{device}/brightness") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


_METHODS = {
    'sysfs': _write_sysfs,
}

_READERS = {
    'sysfs': _read_sysfs,
}


def current_value() -> int | None:
    """The brightness the hardware is actually at, or None if unknown.

    Read back from the device rather than trusting `_last_written_value`, so a
    level changed outside clockish (someone echoing into sysfs) is reported
    honestly. Falls back to the last written value when the device can't be
    read -- and to None when no backlight is configured at all.
    """
    cfg = _active_cfg
    if not cfg:
        return None
    read_fn = _READERS.get(cfg.get('method'))
    value = read_fn(cfg.get('device', '')) if read_fn else None
    return value if value is not None else _last_written_value


def current_percent() -> str | None:
    """Current brightness as a whole percentage of the configured min..max span.

    min..max is the useful range the display was configured for, not 0..255, so
    this answers "how far up its own range is the panel" -- min reads 0%, max
    reads 100%. Values outside the span (set externally, or an `off_value`
    below min) clamp rather than reporting a negative or >100%.

    Returns None when there is no backlight config or the level is unknown;
    the fact panel renders that as an empty string.
    """
    cfg = _active_cfg
    value = current_value()
    if not cfg or value is None:
        return None
    min_, max_ = cfg.get('min', 0), cfg.get('max', 255)
    span = max_ - min_
    if span <= 0:
        return None
    pct = round((value - min_) / span * 100)
    return f"{min(max(pct, 0), 100)}%"


def _resolve_value(cfg: dict, now: datetime.datetime | None = None) -> int:
    """Resolve `cfg['schedule']` -- a sun scalar or a list of entries -- to a value.

    `now` defaults to the real current time; pass one to resolve the value
    for an arbitrary moment (scripts/backlight_hardware_test.py simulates a
    whole day this way).
    """
    min_, max_ = cfg.get('min', 0), cfg.get('max', 255)
    schedule = cfg.get('schedule')
    midpoint = round((min_ + max_) / 2)

    if isinstance(schedule, str):
        if schedule not in SUN_SCHEDULE_VALUES:
            if DEBUG:
                print(f"DEBUG: backlight: unknown schedule {schedule!r}, using midpoint")
            return midpoint
        sun_times = _get_sun_times() if _get_sun_times is not None else None
        if sun_times is None:
            if DEBUG:
                print(f"DEBUG: backlight: schedule: {schedule} but sun times not known yet, using midpoint")
            return midpoint
        sunrise, sunset = sun_times
        return resolve_sun_curve_value(sunrise, sunset, min_=min_, max_=max_, now=now)

    return resolve_scheduled_value(schedule or [], min_=min_, max_=max_, now=now)


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

    `get_sun_times` is only needed for sun-schedule configs -- see the
    module docstring for why it's a callable instead of an import.
    """
    global _backlight_thread, _get_sun_times, _active_cfg
    cfg = display_cfg.get('backlight')
    if not cfg:
        return

    _get_sun_times = get_sun_times
    stop_backlight()          # clears _active_cfg; re-set it below
    _active_cfg = cfg
    _backlight_stop_event.clear()
    _backlight_wake_event.clear()

    _apply(cfg)  # synchronous initial write, before the first frame is ever shown

    t = threading.Thread(target=_backlight_worker, args=(cfg,), daemon=True)
    _backlight_thread = t
    t.start()


def stop_backlight() -> None:
    global _backlight_thread, _active_cfg
    _active_cfg = None
    try:
        _backlight_stop_event.set()
        _backlight_wake_event.set()  # wake to let it exit quickly
        if _backlight_thread is not None:
            _backlight_thread.join(timeout=5)
    except Exception:
        pass
    _backlight_thread = None
