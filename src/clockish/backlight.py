"""Backlight brightness control -- optional `display.backlight:` config block.

Runs a background thread that looks up the current local time in a simple
day/night `schedule:` list and writes the resulting brightness level to the
display's backlight control device. See AGENTS.md ("backlight") and
configs/display/framebuffer.yaml for the config shape and worked examples.

Only one control `method` exists today: 'sysfs' (writes an integer 0-255 to
a /sys/class/backlight/<device>/brightness file -- the standard Linux
backlight-class interface most DSI panels expose). The `method` key and the
per-method dispatch table (`_METHODS` below) exist so other methods (e.g. a
GPIO on/off pin, or PWM) can be added later without reshaping this module --
none of that is implemented yet.

TODO: st7789 (and other non-sysfs) backlight control -- different hardware
access, path/method TBD; needs testing against real hardware.

TODO: sun-following brightness curve -- replace the fixed schedule with a
continuous ramp keyed off display.py's `_SUN_TIMES` (sunrise/sunset), so
brightness eases from min at dawn to max through the day and back down at
dusk, still on this same ~10-minute worker cadence.
"""

import datetime
import threading

DEBUG: bool = False

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


def _apply(cfg: dict) -> None:
    """Compute the currently-scheduled value and write it if it changed since the last write."""
    global _last_written_value
    method = cfg.get('method')
    write_fn = _METHODS.get(method)
    if write_fn is None:
        if DEBUG:
            print(f"DEBUG: backlight: unknown or unimplemented method {method!r}, skipping")
        return

    value = resolve_scheduled_value(
        cfg.get('schedule', []),
        min_=cfg.get('min', 0),
        max_=cfg.get('max', 255),
    )
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


def start_backlight(display_cfg: dict) -> None:
    """Start the backlight scheduler thread, if `display_cfg['backlight']` is present.

    Applies the current scheduled value synchronously before returning, so a
    restart never leaves the backlight at a stale level (e.g. full brightness
    at 2am) until the first background tick.
    """
    global _backlight_thread
    cfg = display_cfg.get('backlight')
    if not cfg:
        return

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
