#!/usr/bin/env python3
"""scripts/ssd1306-brightness-test.py -- step an SSD1306 OLED through brightness levels, by eye.

For tuning ``display.duty_cycle.levels`` on one module: modules differ at the bottom end, and
contrast 0 or ``precharge: short`` turns some completely dark.  Each step shows a solid block
(easiest to judge brightness) and a short label; the console prints the matching ``levels:``
values to copy into the profile.

Run on the Pi, from the clockish checkout, with the clockish service for that panel stopped
(both would drive the same panel):

    sudo systemctl stop clockish
    .venv/bin/python scripts/ssd1306-brightness-test.py --height 32
    .venv/bin/python scripts/ssd1306-brightness-test.py --set floor --loop
    sudo systemctl start clockish

Sets (--set):
    levels    the built-in full / half / dim levels, then dim inverted (default)
    ladder    contrast 255 / 64 / 32 / 8 / 1, each at VCOMH high then low
    floor     the bottom end: contrast 32 / 8 / 2 / 1 / 0 at VCOMH low
    precharge the bottom end with and without precharge: short

--loop repeats the set until Ctrl-C, stepping on the wall clock (epoch // secs), so panels on
synced clocks show the same step.  On exit the panel goes back to full level, normal, cleared.
"""
import argparse
import signal
import sys
import time

from PIL import Image, ImageDraw, ImageFont

from clockish.drivers.ssd1306 import SSD1306Driver
from clockish.duty_cycle import DEFAULT_LEVELS

# (label, contrast, vcomh, precharge, inverted)
Step = tuple[str, int, str, str, bool]


def _named(name: str, inverted: bool = False) -> Step:
    lv = DEFAULT_LEVELS[name]
    return (name.upper() + (" INV" if inverted else ""), lv['contrast'], lv['vcomh'], lv['precharge'], inverted)


SETS: dict[str, list[Step]] = {
    'levels': [_named('full'), _named('half'), _named('dim'), _named('dim', inverted=True)],
    'ladder': [(f"C{c} V{v[0].upper()}", c, v, 'normal', False)
               for c in (255, 64, 32, 8, 1) for v in ('high', 'low')],
    'floor': [(f"C{c} VL", c, 'low', 'normal', False) for c in (32, 8, 2, 1, 0)],
    'precharge': [(f"C{c} VL{' PS' if p == 'short' else ''}", c, 'low', p, False)
                  for c in (32, 8, 1) for p in ('normal', 'short')],
}

_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(_FONT, size)
    except OSError:
        return ImageFont.load_default()


def frame(w: int, h: int, label: str) -> Image.Image:
    """A solid block on the left, the label as large as fits on the right."""
    img = Image.new("1", (w, h), 0)
    d = ImageDraw.Draw(img)
    block = w // 5
    d.rectangle((0, 0, block - 1, h - 1), fill=1)
    avail_w, avail_h = w - block - 4, h - 2
    size = max(avail_h, 6)
    while True:                                         # largest size that fits, down to 6
        font = _font(size)
        x0, y0, x1, y1 = d.textbbox((0, 0), label, font=font)
        if size <= 6 or (x1 - x0 <= avail_w and y1 - y0 <= avail_h):
            break
        size -= 1
    d.text((block + 4 - x0, (h - (y1 - y0)) // 2 - y0), label, font=font, fill=1)
    return img


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    ap.add_argument("--width", type=int, default=128)
    ap.add_argument("--height", type=int, default=64)
    ap.add_argument("--bus", type=int, default=1, help="/dev/i2c-N (default 1)")
    ap.add_argument("--addr", type=lambda x: int(x, 0), default=0x3C, help="I2C address (default 0x3C)")
    ap.add_argument("--secs", type=float, default=3.0, help="seconds per step (default 3)")
    ap.add_argument("--set", choices=sorted(SETS), default="levels")
    ap.add_argument("--loop", action="store_true", help="repeat until Ctrl-C, stepping on the wall clock")
    a = ap.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))      # restore the panel on kill too

    steps = SETS[a.set]
    drv = SSD1306Driver({"width": a.width, "height": a.height, "i2c_bus": a.bus, "i2c_addr": a.addr}).begin()

    def show(step: Step) -> None:
        label, contrast, vcomh, precharge, inverted = step
        drv.display(frame(a.width, a.height, label))
        drv.set_level(contrast, vcomh, precharge)
        drv.invert(inverted)
        print(f"{time.strftime('%H:%M:%S')}  {label:10s} "
              f"{{contrast: {contrast}, vcomh: {vcomh}, precharge: {precharge}}}"
              f"{'  (inverted)' if inverted else ''}", flush=True)

    try:
        if a.loop:
            while True:
                time.sleep(a.secs - time.time() % a.secs)
                show(steps[int(time.time() // a.secs) % len(steps)])
        for step in steps:
            show(step)
            time.sleep(a.secs)
    except KeyboardInterrupt:
        pass
    finally:
        full = DEFAULT_LEVELS['full']
        drv.set_level(full['contrast'], full['vcomh'], full['precharge'])
        drv.clear()                                              # also un-inverts
        drv.close()


if __name__ == "__main__":
    main()
