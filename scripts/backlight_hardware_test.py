#!/usr/bin/env python3
"""Manual real-hardware sanity check for clockish.backlight.

Not part of the pytest suite (no sysfs backlight on a dev machine) -- run this
ON the target device instead, e.g. over SSH:

    scp scripts/backlight_hardware_test.py user@device:/tmp/
    ssh user@device 'python3 /tmp/backlight_hardware_test.py [device]'

`[device]` is the folder name under /sys/class/backlight/ (default: 10-0045,
the Raspberry Pi 7" touch display panel this was written against). Reads the
current brightness first and always restores it afterwards (try/finally), so
it's safe to run against a display actively in use -- it will visibly dim
for a couple of seconds partway through, then return to normal.

Exercises, against the real sysfs file:
  - _write_sysfs() directly
  - resolve_scheduled_value() -> the full start_backlight()/stop_backlight()
    lifecycle in fixed-schedule mode
  - resolve_sun_curve_value() -> the same lifecycle in curve: sun mode, using
    a synthetic sunrise/sunset (this script has no network access requirement;
    real sunrise/sunset fetching is display.py's job, already covered by its
    own tests -- this only exercises backlight.py's side of the `curve: sun`
    wiring: the get_sun_times callable and the real sysfs write it produces)
"""
import datetime
import os
import sys
import time

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from clockish import backlight  # noqa: E402


def main() -> None:
    device = sys.argv[1] if len(sys.argv) > 1 else '10-0045'
    path = f"/sys/class/backlight/{device}/brightness"
    backlight.DEBUG = True

    try:
        original = int(open(path).read().strip())
    except OSError as e:
        sys.exit(f"ERROR: can't read {path}: {e}\nPass the correct device dir as argv[1].")
    print(f"device={device}  original brightness={original}")

    try:
        print("\n--- _write_sysfs() direct write ---")
        ok = backlight._write_sysfs(device, 60)
        readback = int(open(path).read().strip())
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
        readback = int(open(path).read().strip())
        print(f"after start_backlight (schedule) readback={readback} (expected 77)")
        assert readback == 77
        backlight.stop_backlight()
        time.sleep(1)

        print("\n--- curve: sun: full lifecycle with a synthetic sunrise/sunset ---")
        # Widened well past "right now" so this passes regardless of what
        # time of day the script actually runs at.
        sunrise = now - datetime.timedelta(hours=6)
        sunset = now + datetime.timedelta(hours=6)

        def fake_get_sun_times():
            return (sunrise, sunset)

        curve_cfg = {
            'method': 'sysfs', 'device': device, 'logging': True,
            'off_value': 0, 'min': 40, 'max': 255,
            'curve': 'sun',
        }
        expected = backlight.resolve_sun_curve_value(sunrise, sunset, min_=40, max_=255)
        backlight.start_backlight({'backlight': curve_cfg}, get_sun_times=fake_get_sun_times)
        readback = int(open(path).read().strip())
        print(f"after start_backlight (curve) readback={readback} (expected {expected})")
        assert readback == expected
        backlight.stop_backlight()

        print("\nAll checks passed.")
    finally:
        print(f"\nrestoring original brightness={original}")
        backlight._write_sysfs(device, original)
        readback = int(open(path).read().strip())
        print(f"final readback={readback}")


if __name__ == '__main__':
    main()
