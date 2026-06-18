#!/usr/bin/env python3
"""
colortest.py - Interactive color visibility tester for ILI9486 display.

Shows swatches of colors on the display, then prompts on the terminal
asking how many distinct colors you could see.  Results are saved to
colortest_results.txt so you can use them to build a better color palette.
"""

import sys
import os
import time
import math

from PIL import Image, ImageDraw, ImageFont
from spidev import SpiDev
from pyili9486 import ILI9486, Origin, SKU
from pyili9486.gpio.rpilgpio_facade import RPiLGPIOFacade

# ---------------------------------------------------------------------------
# Hardware configuration  --  adjust to match your wiring
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
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
    smallfont = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
except Exception:
    font = ImageFont.load_default()
    smallfont = font

# ---------------------------------------------------------------------------
# All named colors to test  (name, hex)
# ---------------------------------------------------------------------------
ALL_COLORS = [
    ("BLACK",       "#000000"),
    ("DARKGREY",    "#3F3F3F"),
    ("GREY",        "#7F7F7F"),
    ("WHITE",       "#FFFFFF"),
    ("RED",         "#FF0000"),
    ("DARKRED",     "#7F0000"),
    ("ORANGE",      "#FFA500"),
    ("YELLOW",      "#FFFF00"),
    ("DARKYELLOW",  "#7F7F00"),
    ("GREEN",       "#007F00"),
    ("BRIGHTGREEN", "#00FF00"),
    ("DARKGREEN",   "#003F00"),
    ("CYAN",        "#00FFFF"),
    ("DARKCYAN",    "#007F7F"),
    ("BLUE",        "#0000BF"),
    ("BRIGHTBLUE",  "#0000FF"),
    ("MEDBLUE",     "#00007F"),
    ("DARKBLUE",    "#00003F"),
    ("PURPLE",      "#FF00FF"),
    ("DARKPURPLE",  "#7F007F"),
    ("PINK",        "#FF7FFF"),
    ("BROWN",       "#8B4513"),
]

SWATCHES_PER_PAGE = 6   # number of color swatches shown at once
SWATCH_HEIGHT = 60      # pixels tall per swatch
LABEL_HEIGHT  = 20      # pixels for color name label inside swatch

results = {}  # color name -> user-reported visibility ("yes"/"no"/"partial")


def clear(color="#000000"):
    draw.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), fill=color)


def show_page(color_subset):
    """
    Render a page of color swatches.
    Each swatch fills the full width, shows the color, name, and hex value.
    A black-on-white header shows which page we're on.
    """
    clear("#000000")

    n = len(color_subset)
    swatch_h = (HEIGHT - 40) // n  # distribute evenly, leaving 40px header

    # Header
    draw.rectangle((0, 0, WIDTH - 1, 38), fill="#FFFFFF")
    draw.text((4, 4), "Color visibility test  --  see terminal", font=smallfont, fill="#000000")

    y = 40
    for name, hex_color in color_subset:
        # Fill swatch with the color
        draw.rectangle((0, y, WIDTH - 1, y + swatch_h - 2), fill=hex_color)

        # Pick a contrasting label color (white or black)
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
        luminance = 0.299 * r + 0.587 * g + 0.114 * b
        label_color = "#000000" if luminance > 128 else "#FFFFFF"

        # Also draw a small contrasting border strip so pure-black swatches are visible
        draw.rectangle((0, y, WIDTH - 1, y + 1), fill="#FFFFFF")
        draw.rectangle((0, y + swatch_h - 3, WIDTH - 1, y + swatch_h - 2), fill="#FFFFFF")

        label = f"{name}  {hex_color}"
        draw.text((6, y + (swatch_h - LABEL_HEIGHT) // 2), label, font=font, fill=label_color)
        # Also draw label in opposite color offset by 1px for visibility on mid-tones
        draw.text((7, y + (swatch_h - LABEL_HEIGHT) // 2 + 1), label, font=font, fill=label_color)

        y += swatch_h

    lcd.display(image, rotation)


def ask_visibility(color_names):
    """Ask the user on the terminal which colors they could see."""
    print()
    print("=" * 60)
    print("Colors shown on display:")
    for i, name in enumerate(color_names, 1):
        print(f"  {i}. {name}")
    print()
    print("For each color, enter:")
    print("  y = clearly visible")
    print("  p = partially visible / hard to see")
    print("  n = not visible / indistinguishable from background")
    print("  (or press Enter to skip / mark as 'y')")
    print()

    for name in color_names:
        while True:
            ans = input(f"  {name}: [y/p/n] > ").strip().lower()
            if ans in ("y", "p", "n", ""):
                results[name] = ans if ans else "y"
                break
            print("  Please enter y, p, n, or Enter.")


def show_comparison_page(name_a, hex_a, name_b, hex_b):
    """
    Show two colors side by side for direct comparison.
    Left half = color A, right half = color B.
    """
    clear()
    mid = WIDTH // 2

    draw.rectangle((0, 0, mid - 1, HEIGHT - 1), fill=hex_a)
    draw.rectangle((mid, 0, WIDTH - 1, HEIGHT - 1), fill=hex_b)

    # Labels
    for hex_c, x0, name in [(hex_a, 4, name_a), (hex_b, mid + 4, name_b)]:
        r = int(hex_c[1:3], 16)
        g = int(hex_c[3:5], 16)
        b_ = int(hex_c[5:7], 16)
        lum = 0.299 * r + 0.587 * g + 0.114 * b_
        lc = "#000000" if lum > 128 else "#FFFFFF"
        draw.text((x0, HEIGHT // 2 - 20), name, font=font, fill=lc)
        draw.text((x0, HEIGHT // 2 + 4), hex_c, font=smallfont, fill=lc)

    lcd.display(image, rotation)


def save_results():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "colortest_results.txt")
    with open(path, "w") as f:
        f.write("Color visibility test results\n")
        f.write("=" * 40 + "\n")
        f.write(f"{'Color':<16} {'Hex':<10} {'Visible'}\n")
        f.write("-" * 40 + "\n")
        for name, hex_color in ALL_COLORS:
            vis = results.get(name, "untested")
            f.write(f"{name:<16} {hex_color:<10} {vis}\n")
        f.write("\n")
        f.write("Suggested palette (visible colors only):\n")
        for name, hex_color in ALL_COLORS:
            if results.get(name) == "y":
                f.write(f'{name:<16} = "{hex_color}"\n')
    print(f"\nResults saved to: {path}")


def run_comparison_phase():
    """
    For any colors marked 'p' (partial), show them paired against WHITE and BLACK
    to help the user decide if they're usable.
    """
    partials = [(n, h) for n, h in ALL_COLORS if results.get(n) == "p"]
    if not partials:
        return
    print()
    print("=" * 60)
    print("Comparison phase: partial-visibility colors vs WHITE and BLACK")
    for name, hex_color in partials:
        print(f"\n  Showing {name} ({hex_color}) vs WHITE ...")
        show_comparison_page(name, hex_color, "WHITE", "#FFFFFF")
        ans = input(f"  Is {name} distinguishable from WHITE? [y/n] > ").strip().lower()
        vs_white = ans == "y"

        print(f"  Showing {name} ({hex_color}) vs BLACK ...")
        show_comparison_page(name, hex_color, "BLACK", "#000000")
        ans = input(f"  Is {name} distinguishable from BLACK? [y/n] > ").strip().lower()
        vs_black = ans == "y"

        if vs_white and vs_black:
            results[name] = "y"
            print(f"  -> {name} upgraded to VISIBLE")
        elif vs_white or vs_black:
            results[name] = "p"
            print(f"  -> {name} stays PARTIAL")
        else:
            results[name] = "n"
            print(f"  -> {name} downgraded to NOT VISIBLE")


def main():
    print()
    print("ILI9486 Color Visibility Tester")
    print("================================")
    print(f"Testing {len(ALL_COLORS)} colors in pages of {SWATCHES_PER_PAGE}.")
    print("Watch the display and answer the prompts in the terminal.")
    print()
    input("Press Enter to begin...")

    # Split colors into pages
    pages = [ALL_COLORS[i:i + SWATCHES_PER_PAGE]
             for i in range(0, len(ALL_COLORS), SWATCHES_PER_PAGE)]

    for page_num, page in enumerate(pages, 1):
        print(f"\n--- Page {page_num} of {len(pages)} ---")
        show_page(page)
        time.sleep(0.3)  # let display settle
        ask_visibility([name for name, _ in page])

    run_comparison_phase()
    save_results()

    # Final summary
    print()
    print("=" * 60)
    print("Summary:")
    visible  = [n for n, _ in ALL_COLORS if results.get(n) == "y"]
    partial  = [n for n, _ in ALL_COLORS if results.get(n) == "p"]
    hidden   = [n for n, _ in ALL_COLORS if results.get(n) == "n"]
    print(f"  Clearly visible ({len(visible)}): {', '.join(visible)}")
    print(f"  Partial         ({len(partial)}): {', '.join(partial)}")
    print(f"  Not visible     ({len(hidden)}):  {', '.join(hidden)}")
    print()
    print("Done. Use colortest_results.txt to update your color palette.")

    # Show a final "all clear" screen
    clear("#000000")
    draw.text((10, HEIGHT // 2 - 20), "Test complete!", font=font, fill="#FFFFFF")
    draw.text((10, HEIGHT // 2 + 10), "See terminal for results.", font=smallfont, fill="#AAAAAA")
    lcd.display(image, rotation)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        GPIO.cleanup()
        spi.close()

