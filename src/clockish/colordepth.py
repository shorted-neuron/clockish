#!/usr/bin/env python3
"""
colordepth.py - Find the minimum distinguishable color step for each channel
on the ILI9486 display.

For each color channel (R, G, B) and for grey (R=G=B), we start with both
halves of the screen at the same value, then slowly increment the RIGHT side
by increasing step sizes until the user can see a difference.

Left half stays fixed at the reference value.
Right half increments from reference upward.

Results are saved to colordepth_results.txt.
"""

import os
import sys
import termios
import tty

from PIL import Image, ImageDraw, ImageFont
from pyili9486 import ILI9486, SKU, Origin
from pyili9486.gpio.rpilgpio_facade import RPiLGPIOFacade
from spidev import SpiDev

# ---------------------------------------------------------------------------
# Hardware configuration
# ---------------------------------------------------------------------------
SPI_BUS    = 0
SPI_DEVICE = 0
DC_PIN     = 24
RST_PIN    = 25

# ---------------------------------------------------------------------------
# Display setup
# ---------------------------------------------------------------------------
_gpio = RPiLGPIOFacade(dc_pin=DC_PIN, rs_pin=RST_PIN)
spi = SpiDev(SPI_BUS, SPI_DEVICE)
spi.mode = 0b10
spi.max_speed_hz = 64000000
lcd = ILI9486(spi=spi, gpio_facade=_gpio, origin=Origin.UPPER_RIGHT, sku=SKU.MPI3501).begin()

WIDTH  = 320
HEIGHT = 480
rotation = 0

image = Image.new("RGB", (WIDTH, HEIGHT))
draw  = ImageDraw.Draw(image)

try:
    font      = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
    smallfont = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
except Exception:
    font = smallfont = ImageFont.load_default()

# ---------------------------------------------------------------------------
# Test definitions: (label, reference_rgb, channel_index_to_vary)
#   channel_index: 0=R, 1=G, 2=B, -1=all three (grey ramp)
# ---------------------------------------------------------------------------
# We test at a mid-range reference value so we're not at the ends of the range
REF = 128

TESTS = [
    ("RED channel",   (REF, 0,   0  ), 0),
    ("GREEN channel", (0,   REF, 0  ), 1),
    ("BLUE channel",  (0,   0,   REF), 2),
    ("GREY ramp",     (REF, REF, REF), -1),
    ("RED channel (bright)",   (220, 0,   0  ), 0),
    ("GREEN channel (bright)", (0,   220, 0  ), 1),
    ("BLUE channel (bright)",  (0,   0,   220), 2),
    ("GREY ramp (bright)",     (220, 220, 220), -1),
]

results = []   # list of dicts


def clamp(v):
    return max(0, min(255, v))


def make_color(base_rgb, channel, delta):
    """Return base_rgb with the given channel(s) increased by delta."""
    r, g, b = base_rgb
    if channel == 0:
        return (clamp(r + delta), g, b)
    elif channel == 1:
        return (r, clamp(g + delta), b)
    elif channel == 2:
        return (r, g, clamp(b + delta))
    else:  # -1: all channels (grey)
        return (clamp(r + delta), clamp(g + delta), clamp(b + delta))


def rgb_to_hex(rgb):
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def show_split(left_rgb, right_rgb, label, delta, step_num, total_steps):
    """Render left/right halves and an info bar at top."""
    mid = WIDTH // 2

    draw.rectangle((0,   0, mid - 1,   HEIGHT - 1), fill=left_rgb)
    draw.rectangle((mid, 0, WIDTH - 1, HEIGHT - 1), fill=right_rgb)

    # Info bar at top (neutral grey background)
    bar_h = 52
    draw.rectangle((0, 0, WIDTH - 1, bar_h - 1), fill=(40, 40, 40))

    # Channel label
    draw.text((4, 2), label, font=font, fill=(255, 255, 255))

    # Left / right hex values
    draw.text((4,   20), f"L: {rgb_to_hex(left_rgb)}",  font=smallfont, fill=(200, 200, 200))
    draw.text((mid, 20), f"R: {rgb_to_hex(right_rgb)}", font=smallfont, fill=(200, 200, 200))

    # Delta
    draw.text((4, 36), f"delta={delta:+d}  step {step_num}/{total_steps}",
              font=smallfont, fill=(180, 180, 100))

    lcd.display(image, rotation)


def get_keypress():
    """Read a single keypress from stdin without requiring Enter."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return ch


def run_test(label, base_rgb, channel):
    print()
    print("=" * 60)
    print(f"TEST: {label}")
    print(f"  Left (fixed):  {rgb_to_hex(base_rgb)}  {base_rgb}")
    print()
    print("  The LEFT half is fixed.  The RIGHT half will slowly change.")
    print("  Press  Y  when you can see a difference.")
    print("  Press  Enter / Space  if you cannot see any difference yet.")
    print("  Press  Q  to skip this test.")
    print()
    input("  Press Enter to start this test...")

    # We step through deltas: 1,2,3,...,8, then 10,12,...,32, then 40,64,128
    fine_steps   = list(range(1, 9))          # 1..8
    medium_steps = list(range(10, 33, 2))     # 10,12,...32
    coarse_steps = [40, 48, 64, 80, 96, 128]
    all_deltas   = fine_steps + medium_steps + coarse_steps

    threshold_delta = None
    last_delta = 0

    for i, delta in enumerate(all_deltas, 1):
        right_rgb = make_color(base_rgb, channel, delta)

        # Skip if right_rgb == base_rgb (channel already maxed out)
        if right_rgb == base_rgb:
            print(f"  [skipping delta={delta}: channel saturated]")
            continue

        show_split(base_rgb, right_rgb, label, delta, i, len(all_deltas))

        print(f"  delta={delta:3d}   L={rgb_to_hex(base_rgb)}  R={rgb_to_hex(right_rgb)}",
              end="  ", flush=True)

        key = get_keypress()
        print()  # newline after keypress

        if key.lower() == 'q':
            print("  Skipped.")
            break
        elif key.lower() == 'y':
            threshold_delta = delta
            print(f"  [ok] Difference visible at delta={delta}")
            break
        else:
            print(f"  No difference seen at delta={delta}")
            last_delta = delta

    if threshold_delta is None and last_delta > 0:
        print(f"  No difference seen up to delta={last_delta}.")

    result = {
        "label":           label,
        "base_hex":        rgb_to_hex(base_rgb),
        "base_rgb":        base_rgb,
        "channel":         channel,
        "threshold_delta": threshold_delta,
        "last_tested":     (
            last_delta if threshold_delta is None
            else all_deltas[all_deltas.index(threshold_delta) - 1]
            if threshold_delta != all_deltas[0] else 0
        ),
    }
    results.append(result)
    return result


def save_results():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "colordepth_results.txt")
    with open(path, "w") as f:
        f.write("ILI9486 Color Depth / Distinguishability Test Results\n")
        f.write("=" * 56 + "\n\n")
        f.write(f"{'Test':<30} {'Base':<10} {'Min visible delta'}\n")
        f.write("-" * 56 + "\n")
        for r in results:
            if r["threshold_delta"] is not None:
                delta_str = str(r["threshold_delta"])
            else:
                delta_str = f">={r['last_tested']} (not found)"
            f.write(f"{r['label']:<30} {r['base_hex']:<10} {delta_str}\n")

        f.write("\n\nInterpretation\n")
        f.write("-" * 56 + "\n")
        f.write("A minimum visible delta of N on a 0-255 scale means\n")
        f.write("roughly 256/N distinguishable steps per channel.\n\n")
        for r in results:
            if r["threshold_delta"] and r["threshold_delta"] > 0:
                steps = 256 // r["threshold_delta"]
                bits  = steps.bit_length() - 1
                f.write(f"  {r['label']:<30} ~{steps:3d} steps  (~{bits} bits)\n")

    print(f"\nResults saved to: {path}")
    return path


def main():
    print()
    print("ILI9486 Color Depth Tester")
    print("==========================")
    print("This test shows two color patches side by side.")
    print("The left patch is fixed; the right patch slowly changes.")
    print("You indicate when you can first see a difference.")
    print()
    print("Tests cover R, G, B channels and grey ramp at two brightness levels.")
    print()
    input("Press Enter to begin...")

    for label, base_rgb, channel in TESTS:
        run_test(label, base_rgb, channel)

    # Summary on terminal
    print()
    print("=" * 60)
    print("RESULTS SUMMARY")
    print(f"{'Test':<30} {'Base':<10} {'Min delta'}")
    print("-" * 60)
    for r in results:
        delta_str = str(r["threshold_delta"]) if r["threshold_delta"] is not None else "not found"
        print(f"  {r['label']:<28} {r['base_hex']:<10} {delta_str}")

    print()
    print("Estimated bits per channel:")
    for r in results:
        if r["threshold_delta"] and r["threshold_delta"] > 0:
            steps = 256 // r["threshold_delta"]
            bits  = steps.bit_length() - 1
            print(f"  {r['label']:<30} ~{steps} steps  (~{bits} bits/channel)")

    path = save_results()

    # Clear display
    draw.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), fill=(0, 0, 0))
    draw.text((10, HEIGHT // 2 - 20), "Test complete!", font=font, fill=(255, 255, 255))
    draw.text((10, HEIGHT // 2 + 8),  "See terminal for results.",
              font=smallfont, fill=(160, 160, 160))
    lcd.display(image, rotation)
    print(f"\nDone. Results in: {path}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        # GPIO cleanup is handled by the rpi-lgpio facade (see
        # drivers/ili9486.py) -- only the SPI bus needs an explicit close.
        spi.close()
