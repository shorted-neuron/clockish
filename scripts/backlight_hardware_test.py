#!/usr/bin/env python3
"""Simulated-day backlight runner -- manual real-hardware check for clockish.

Not part of the pytest suite (no sysfs backlight, no panel on a dev machine).
Run it ON the target device, e.g. over SSH:

    scp scripts/backlight_hardware_test.py user@device:/tmp/
    ssh user@device 'python3 /tmp/backlight_hardware_test.py ~/clockish/configs/my.yaml'

What it does
------------
Replays a whole day in a couple of minutes. One wall-clock tick (default 1s)
advances a simulated clock by 10 simulated minutes; at every tick it

  1. runs the REAL backlight resolution + sysfs write for that simulated
     moment (`backlight._apply(cfg, now=...)`, the same call the background
     worker makes -- only the `now` differs),
  2. renders a REAL clockish frame on the REAL panel (`display.show_rows()`)
     with the simulated time injected, so the clock on screen agrees with the
     brightness you are watching,
  3. prints the value, a bar, and a sysfs readback.

So a 24h day at defaults = 144 ticks ~= 2.5 minutes of watching the panel
walk from midnight to midnight. `curve: sun` uses the device's genuinely
resolved location and today's real sunrise/sunset (or `--sunrise/--sunset`
for a repeatable offline run).

Afterwards it checks the collected samples (night == min, peak at solar noon,
monotonic either side, every sysfs readback matched) and exits non-zero on
failure -- it is still a test, not only a demo.

Safety: reads the current brightness first and always restores it
(try/finally), so it is safe to run against a display in use. It WILL visibly
dim and brighten for the duration.

Modes
-----
    --checks-only   the old unit-level hardware checks (direct _write_sysfs,
                    start/stop lifecycle for schedule and curve), no day sim
    --no-frames     backlight only; no config/display/driver init, no panel
    --dry-run       never touch sysfs; print what would have been written
                    (lets the whole thing be exercised on a dev box)

Time injection is entirely local to this script (monkeypatching display's
`_now_in_tz` / `get_daytime` / `get_nighttime`); the only accommodation in
the shipped source is `backlight._apply(cfg, now=...)`'s optional `now`.
"""

import argparse
import datetime
import math
import os
import sys
import time
import types

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# Importing display touches no hardware -- drivers are loaded lazily by
# load_driver(), not at import time.
import clockish.display as display  # noqa: E402
from clockish import backlight  # noqa: E402

#: The simulated moment the current tick is rendering. Read by the patched
#: display hooks below (they are installed once, and always read this global
#: rather than being re-installed per tick).
_SIM_NOW: datetime.datetime = datetime.datetime.now()

_BAR_WIDTH = 42


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _naive(dt: datetime.datetime | None) -> datetime.datetime | None:
    """Drop tzinfo. Sun times from Open-Meteo (timezone=auto) are already naive
    local; anything aware gets flattened so it can be compared against the
    naive simulated clock."""
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def _hhmm(value: str) -> tuple[int, int]:
    hh, mm = value.split(':')
    return int(hh), int(mm)


def _read_sysfs(device: str) -> int | None:
    try:
        with open(f"/sys/class/backlight/{device}/brightness") as f:
            return int(f.read().strip())
    except OSError:
        return None


def _bar(value: int, min_: int, max_: int) -> str:
    span = max(max_ - min_, 1)
    frac = (value - min_) / span
    frac = 0.0 if frac < 0 else (1.0 if frac > 1 else frac)
    filled = int(round(frac * _BAR_WIDTH))
    return '#' * filled + '.' * (_BAR_WIDTH - filled)


# ---------------------------------------------------------------------------
# dry-run: swap the sysfs writer for a recorder (script-local, no source change)
# ---------------------------------------------------------------------------
class _DryRunWriter:
    def __init__(self) -> None:
        self.last: int | None = None

    def __call__(self, device: str, value: int) -> bool:
        self.last = value
        return True


# ---------------------------------------------------------------------------
# time injection into display.py (script-local monkeypatching)
# ---------------------------------------------------------------------------
def _install_time_patches() -> None:
    """Point display's clock/date/day-night reads at the simulated clock.

    - `_now_in_tz` feeds every clock/date panel (show_rows builds its tz_cache
      from it). 'local' gets the simulated time verbatim; a named timezone
      gets the simulated time shifted by that zone's real current offset from
      local, so multi-timezone configs stay coherent during the replay.
    - `get_daytime`/`get_nighttime` are re-implemented against the simulated
      clock; `_get_fact()` rebuilds its source registry on every call, so
      patching the module globals is enough for fact panels to pick them up.
    """
    real_now_in_tz = display._now_in_tz

    def sim_now_in_tz(tz_name: str) -> datetime.datetime:
        if tz_name.lower() == 'local':
            return _SIM_NOW
        offset_min = round(
            (real_now_in_tz(tz_name).replace(tzinfo=None) - datetime.datetime.now()).total_seconds() / 60
        )
        return _SIM_NOW + datetime.timedelta(minutes=offset_min)

    def sim_daytime() -> str:
        entry = display._SUN_TIMES.get(_SIM_NOW.date().isoformat()) or {}
        sr, ss = _naive(entry.get('sunrise')), _naive(entry.get('sunset'))
        if sr is not None and ss is not None:
            return 'true' if sr <= _SIM_NOW < ss else 'false'
        return 'true' if display._is_daytime_static(_SIM_NOW) else 'false'

    display._now_in_tz = sim_now_in_tz
    display.get_daytime = sim_daytime
    display.get_nighttime = lambda: 'false' if sim_daytime() == 'true' else 'true'


# ---------------------------------------------------------------------------
# setup: config + backlight block + sun times
# ---------------------------------------------------------------------------
def _backlight_cfg_from_config(display_cfg: dict, args) -> dict:
    """The config's `display.backlight:` block, or a synthetic curve: sun one."""
    cfg = dict(display_cfg.get('backlight') or {})
    if not cfg:
        print("No display.backlight: block in config -- synthesizing a curve: sun one "
              f"(device={args.device}, min={args.min}, max={args.max}).")
        cfg = {'method': 'sysfs', 'device': args.device, 'curve': 'sun',
               'min': args.min, 'max': args.max, 'off_value': 0}
    if args.device_override:
        cfg['device'] = args.device
    cfg['logging'] = False  # this script prints its own per-tick line
    return cfg


def _inject_sun_times(day: datetime.date, sunrise: str, sunset: str) -> None:
    srh, srm = _hhmm(sunrise)
    ssh, ssm = _hhmm(sunset)
    display._SUN_TIMES[day.isoformat()] = {
        'sunrise': datetime.datetime.combine(day, datetime.time(srh, srm)),
        'sunset': datetime.datetime.combine(day, datetime.time(ssh, ssm)),
        'fetched_at': datetime.datetime.now(),
    }


def _wait_for_sun_times(day: datetime.date, timeout_s: float) -> bool:
    """Poll display's sun-times global until the worker has filled today in."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if display._SUN_TIMES.get(day.isoformat()):
            return True
        time.sleep(0.5)
    return bool(display._SUN_TIMES.get(day.isoformat()))


# ---------------------------------------------------------------------------
# the day simulation
# ---------------------------------------------------------------------------
def _simulate_day(args, cfg: dict, dry: _DryRunWriter | None) -> int:
    """Run the replay. Returns a process exit code."""
    global _SIM_NOW

    device = cfg.get('device', '')
    min_, max_ = cfg.get('min', 0), cfg.get('max', 255)
    day = datetime.date.today()
    start_h, start_m = _hhmm(args.start)
    sim_start = datetime.datetime.combine(day, datetime.time(start_h, start_m))
    step = datetime.timedelta(minutes=args.step_mins)
    ticks = max(1, int(round(args.hours * 60 / args.step_mins)))

    sun = display._SUN_TIMES.get(day.isoformat())
    sunrise = _naive(sun.get('sunrise')) if sun else None
    sunset = _naive(sun.get('sunset')) if sun else None
    if sunrise is None and args.sunrise and args.sunset:
        srh, srm = _hhmm(args.sunrise)
        ssh, ssm = _hhmm(args.sunset)
        sunrise = datetime.datetime.combine(day, datetime.time(srh, srm))
        sunset = datetime.datetime.combine(day, datetime.time(ssh, ssm))
    solar_noon = sunrise + (sunset - sunrise) / 2 if sunrise and sunset else None

    print()
    print(f"device={device}  min={min_}  max={max_}  "
          f"mode={'curve:' + str(cfg.get('curve')) if cfg.get('curve') else 'schedule'}")
    if sunrise and sunset:
        print(f"sunrise={sunrise:%H:%M}  solar noon={solar_noon:%H:%M}  sunset={sunset:%H:%M}")
    elif cfg.get('curve') == 'sun':
        print("WARNING: no sun times available -- curve: sun will flatten to the min/max "
              "midpoint.\n         Pass --sunrise HH:MM --sunset HH:MM for a repeatable run.")
    print(f"{ticks} ticks x {args.step_mins} simulated min, {args.tick_secs}s apart "
          f"(~{ticks * args.tick_secs:.0f}s wall){'  [DRY RUN, no sysfs writes]' if dry else ''}")
    print(f"frames: {'no' if args.no_frames else 'yes'}")
    print()

    samples: list[tuple[datetime.datetime, int]] = []
    readback_mismatches: list[str] = []
    writes = 0
    prev_written: int | None = None

    backlight._last_written_value = None  # force a write on the first tick
    next_deadline = time.monotonic()

    for i in range(ticks):
        _SIM_NOW = sim_start + step * i

        backlight._apply(cfg, now=_SIM_NOW)
        written = backlight._last_written_value
        if written is None:
            print(f"ERROR: backlight._apply wrote nothing at {_SIM_NOW:%H:%M} "
                  f"(method={cfg.get('method')!r} unsupported?)")
            return 1
        if written != prev_written:
            writes += 1
            prev_written = written

        if not args.no_frames:
            display.show_rows()

        actual = dry.last if dry else _read_sysfs(device)
        if actual is not None and actual != written:
            readback_mismatches.append(f"{_SIM_NOW:%H:%M}: wrote {written}, read back {actual}")

        samples.append((_SIM_NOW, written))

        mark = ''
        if sunrise and sunset:
            if abs((_SIM_NOW - sunrise).total_seconds()) < step.total_seconds() / 2:
                mark = f"  <- sunrise {sunrise:%H:%M}"
            elif abs((_SIM_NOW - solar_noon).total_seconds()) < step.total_seconds() / 2:
                mark = f"  <- solar noon {solar_noon:%H:%M}"
            elif abs((_SIM_NOW - sunset).total_seconds()) < step.total_seconds() / 2:
                mark = f"  <- sunset {sunset:%H:%M}"
        print(f"{_SIM_NOW:%H:%M}  {written:3d}  |{_bar(written, min_, max_)}|{mark}")

        next_deadline += args.tick_secs
        remaining = next_deadline - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)

    return _report(samples, cfg, min_, max_, sunrise, sunset, solar_noon,
                   writes, readback_mismatches, step, dry is not None)


def _report(samples, cfg, min_, max_, sunrise, sunset, solar_noon,
            writes, readback_mismatches, step, dry) -> int:
    """Print the summary and the pass/fail checks. Returns an exit code."""
    peak_t, peak_v = max(samples, key=lambda s: s[1])
    low_v = min(v for _, v in samples)

    print()
    print(f"Summary: {len(samples)} ticks, {writes} distinct brightness writes "
          f"(the rest deduped by _apply)")
    print(f"  lowest written={low_v}   highest written={peak_v} at {peak_t:%H:%M}")

    checks: list[tuple[bool, str]] = []

    if readback_mismatches:
        checks.append((False, "sysfs readback matched every write -- MISMATCHES: "
                              + "; ".join(readback_mismatches[:5])))
    elif dry:
        checks.append((True, "recorded writes matched every computed value (dry run)"))
    else:
        checks.append((True, "sysfs readback matched every write"))

    if cfg.get('curve') == 'sun' and sunrise and sunset:
        night = [(t, v) for t, v in samples if t < sunrise or t >= sunset]
        day_s = [(t, v) for t, v in samples if sunrise <= t < sunset]

        bad_night = [f"{t:%H:%M}={v}" for t, v in night if v != min_]
        checks.append((not bad_night,
                       f"every night sample == min ({min_})"
                       + (f" -- got {', '.join(bad_night[:5])}" if bad_night else "")))

        # With discrete steps the peak sample can sit up to half a step off
        # solar noon, so compare against the curve's value there, not max_.
        daylight = (sunset - sunrise).total_seconds()
        delta = (step.total_seconds() / 2) / daylight
        floor_v = round(min_ + (max_ - min_) * (1 - math.cos(2 * math.pi * (0.5 + delta))) / 2)
        checks.append((peak_v >= floor_v,
                       f"peak {peak_v} reaches the curve maximum (>= {floor_v}, max={max_})"))

        off_noon_min = abs((peak_t - solar_noon).total_seconds()) / 60
        checks.append((off_noon_min <= step.total_seconds() / 60,
                       f"peak at {peak_t:%H:%M} is within one step of solar noon "
                       f"({solar_noon:%H:%M}, off by {off_noon_min:.0f}m) -- a monotonic "
                       f"sunrise-to-sunset ramp would peak at sunset instead"))

        rising = [v for t, v in day_s if t <= peak_t]
        falling = [v for t, v in day_s if t >= peak_t]
        checks.append((all(b >= a for a, b in zip(rising, rising[1:])),
                       "brightness rises monotonically from sunrise to solar noon"))
        checks.append((all(b <= a for a, b in zip(falling, falling[1:])),
                       "brightness falls monotonically from solar noon to sunset"))
    elif cfg.get('schedule'):
        values = {int(e['value']) for e in cfg['schedule']}
        values.add(round((min_ + max_) / 2))  # uncovered-time fallback
        stray = sorted({v for _, v in samples} - values)
        checks.append((not stray,
                       "every written value came from the schedule (or its midpoint fallback)"
                       + (f" -- stray: {stray}" if stray else "")))

    print()
    print("CHECKS")
    failed = 0
    for ok, text in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {text}")
        failed += 0 if ok else 1
    print()
    print("All checks passed." if not failed else f"{failed} check(s) FAILED.")
    return 0 if not failed else 1


# ---------------------------------------------------------------------------
# --checks-only: the original unit-level hardware checks
# ---------------------------------------------------------------------------
def _run_checks_only(device: str) -> int:
    print("\n--- _write_sysfs() direct write ---")
    ok = backlight._write_sysfs(device, 60)
    readback = _read_sysfs(device)
    print(f"write ok={ok}  readback={readback}")
    assert readback == 60, "sysfs readback did not match what was written"
    time.sleep(1)

    print("\n--- fixed schedule: full start_backlight()/stop_backlight() lifecycle ---")
    now = datetime.datetime.now()
    # A schedule entry covering the entire day, so the synchronous initial
    # apply in start_backlight() is guaranteed to hit it regardless of now.
    schedule_cfg = {
        'method': 'sysfs', 'device': device, 'logging': True,
        'off_value': 0, 'min': 40, 'max': 255,
        'schedule': [{'name': 'now', 'start': '00:00', 'end': '23:59', 'value': 77}],
    }
    backlight.start_backlight({'backlight': schedule_cfg})
    readback = _read_sysfs(device)
    print(f"after start_backlight (schedule) readback={readback} (expected 77)")
    assert readback == 77
    backlight.stop_backlight()
    time.sleep(1)

    print("\n--- curve: sun: full lifecycle with a synthetic sunrise/sunset ---")
    sunrise = now - datetime.timedelta(hours=6)
    sunset = now + datetime.timedelta(hours=6)
    curve_cfg = {
        'method': 'sysfs', 'device': device, 'logging': True,
        'off_value': 0, 'min': 40, 'max': 255, 'curve': 'sun',
    }
    expected = backlight.resolve_sun_curve_value(sunrise, sunset, min_=40, max_=255)
    backlight.start_backlight({'backlight': curve_cfg}, get_sun_times=lambda: (sunrise, sunset))
    readback = _read_sysfs(device)
    print(f"after start_backlight (curve) readback={readback} (expected {expected})")
    assert readback == expected
    backlight.stop_backlight()

    print("\nAll checks passed.")
    return 0


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Replay a whole simulated day of clockish frames + backlight on real hardware.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument('config', nargs='?', help="clockish config YAML (omit with --no-frames/--checks-only)")
    p.add_argument('--device', default='10-0045',
                   help="folder under /sys/class/backlight/ (default: 10-0045, the Pi 7\" panel)")
    p.add_argument('--step-mins', type=int, default=10,
                   help="simulated minutes per tick (default: 10, matching the worker's cadence)")
    p.add_argument('--tick-secs', type=float, default=1.0, help="wall seconds per tick (default: 1.0)")
    p.add_argument('--start', default='00:00', help="simulated start time HH:MM (default: 00:00)")
    p.add_argument('--hours', type=float, default=24.0, help="simulated hours to replay (default: 24)")
    p.add_argument('--sunrise', help="force sunrise HH:MM instead of the resolved location's")
    p.add_argument('--sunset', help="force sunset HH:MM instead of the resolved location's")
    p.add_argument('--sun-wait', type=float, default=15.0,
                   help="seconds to wait for the sun-times worker (default: 15)")
    p.add_argument('--min', type=int, default=40, help="min brightness if the config has no backlight block")
    p.add_argument('--max', type=int, default=255, help="max brightness if the config has no backlight block")
    p.add_argument('--no-frames', action='store_true', help="backlight only: no driver, no panel output")
    p.add_argument('--dry-run', action='store_true', help="never write sysfs; record the values instead")
    p.add_argument('--checks-only', action='store_true', help="run the old unit-level hardware checks and exit")
    p.add_argument('--loop', action='store_true', help="repeat the day until Ctrl-C")
    args = p.parse_args(argv)
    args.device_override = any(a == '--device' or a.startswith('--device=') for a in argv)
    if not args.config and not (args.no_frames or args.checks_only):
        p.error("a config path is required unless --no-frames or --checks-only is given")
    if args.step_mins <= 0:
        p.error("--step-mins must be positive")
    if bool(args.sunrise) != bool(args.sunset):
        p.error("--sunrise and --sunset must be given together")
    if args.checks_only and args.dry_run:
        p.error("--checks-only writes real sysfs by design; it has nothing to dry-run")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    backlight.DEBUG = True

    dry: _DryRunWriter | None = None
    if args.dry_run:
        dry = _DryRunWriter()
        backlight._METHODS['sysfs'] = dry

    original = None if args.dry_run else _read_sysfs(args.device)
    if original is None and not args.dry_run:
        print(f"ERROR: can't read /sys/class/backlight/{args.device}/brightness\n"
              f"       Pass the right folder with --device, or use --dry-run.", file=sys.stderr)
        return 1
    print(f"device={args.device}  original brightness={original}"
          if not args.dry_run else f"device={args.device}  [DRY RUN: sysfs untouched]")

    if args.checks_only:
        try:
            return _run_checks_only(args.device)
        finally:
            _restore(args.device, original, dry)

    day = datetime.date.today()
    inited = False
    try:
        if args.no_frames:
            # No hardware, no layout: load the config only far enough to find
            # a display.backlight: block (display-profile resolution included).
            # Either half may legitimately be absent -- fall back to the
            # synthetic block built from --device/--min/--max.
            display_cfg = {}
            try:
                cfg_dict = display._load_config(args.config) if args.config else {}
                _, display_cfg = display._resolve_config(
                    cfg_dict, types.SimpleNamespace(config=args.config))
            except Exception as e:
                print(f"note: no display config resolved ({e}); using CLI defaults")
        else:
            # display._init() reads sys.argv itself -- hand it the config path
            # (positional, as the clockish CLI takes it) and let it do the
            # whole real startup: driver, fonts, layout, location + sun-times
            # worker, backlight thread.
            sys.argv = ['clockish', args.config]
            display._init()
            inited = True
            display_cfg = display._resolved_display_config

        # The real 10-minute worker would fight the simulation.
        backlight.stop_backlight()

        cfg = _backlight_cfg_from_config(display_cfg, args)

        if args.sunrise:
            _inject_sun_times(day, args.sunrise, args.sunset)
        elif cfg.get('curve') == 'sun' and not args.no_frames:
            if not _wait_for_sun_times(day, args.sun_wait):
                print(f"WARNING: sun times not resolved within {args.sun_wait:.0f}s "
                      "(location off, offline, or slow API).")
        # curve: sun resolves sun times through this callable; display._init()
        # already wired it up, but --no-frames never called start_backlight().
        if backlight._get_sun_times is None:
            backlight._get_sun_times = display._get_today_sun_times

        if not args.no_frames:
            _install_time_patches()

        rc = 0
        while True:
            rc = _simulate_day(args, cfg, dry)
            if not args.loop or rc:
                break
        return rc
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130
    finally:
        try:
            if inited:
                display._cleanup()
        except Exception:
            pass
        _restore(args.device, original, dry)


def _restore(device: str, original: int | None, dry: _DryRunWriter | None) -> None:
    if dry is not None or original is None:
        return
    print(f"\nrestoring original brightness={original}")
    backlight._write_sysfs(device, original)
    print(f"final readback={_read_sysfs(device)}")


if __name__ == '__main__':
    sys.exit(main())
