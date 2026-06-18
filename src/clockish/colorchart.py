#!/usr/bin/env python3
"""
colorchart.py - Display all named colors as a chart on the ILI9486.

Shows every color from the tested palette as a full-width swatch with its
name and hex value printed in a contrasting color.  For mid-greys where
neither black nor white is ideal, a colored label (cyan or orange) is used.

Navigate pages with Enter (next) or Q (quit).
"""

import sys
import os

import termios
import tty
import time

from PIL import Image, ImageDraw, ImageFont
from spidev import SpiDev
from pyili9486 import ILI9486, Origin, SKU
from pyili9486.gpio.rpilgpio_facade import RPiLGPIOFacade
from clockish.colors import PALETTE, rgb_to_hex, best_label_color, luminance

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
    font      = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
    smallfont = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
    tinyfont  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
except Exception:
    font = smallfont = tinyfont = ImageFont.load_default()

SWATCHES_PER_PAGE = 8



def show_page(swatches, page_num, total_pages):
    """Render a page of color swatches."""
    n = len(swatches)
    header_h = 36
    avail_h = HEIGHT - header_h
    swatch_h = avail_h // n

    # Header
    draw.rectangle((0, 0, WIDTH - 1, header_h - 1), fill=(20, 20, 20))
    draw.text((4, 2),  "Color Chart", font=font, fill=(255, 255, 255))
    draw.text((4, 20), f"Page {page_num}/{total_pages}   --   Enter=next  Q=quit",
              font=tinyfont, fill=(160, 160, 160))

    y = header_h
    for name, rgb in swatches:
        # Swatch background
        draw.rectangle((0, y, WIDTH - 1, y + swatch_h - 1), fill=rgb)

        # Thin separator line in a neutral mid-tone so black swatches are bounded
        draw.rectangle((0, y, WIDTH - 1, y), fill=(80, 80, 80))

        label_rgb = best_label_color(rgb)
        hex_str   = rgb_to_hex(rgb)

        # Name (larger) + hex (smaller) stacked, vertically centered in swatch
        name_y = y + max(2, (swatch_h - 34) // 2)
        hex_y  = name_y + 18

        if swatch_h >= 34:
            draw.text((6, name_y), name,    font=font,      fill=label_rgb)
            draw.text((6, hex_y),  hex_str, font=smallfont, fill=label_rgb)
        else:
            # Tight fit: single line "NAME  #RRGGBB"
            draw.text((6, y + max(1, (swatch_h - 16) // 2)),
                      f"{name}  {hex_str}", font=smallfont, fill=label_rgb)

        y += swatch_h

    lcd.display(image, rotation)


def get_keypress():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return ch


def show_all_at_once():
    """
    Show every color as a tiny strip  --  a visual density overview.
    One pixel row per color (or a few rows if height allows).
    """
    draw.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), fill=(0, 0, 0))
    n = len(PALETTE)
    row_h = max(1, HEIGHT // n)
    y = 0
    for name, rgb in PALETTE:
        draw.rectangle((0, y, WIDTH - 1, min(y + row_h - 1, HEIGHT - 1)), fill=rgb)
        y += row_h
        if y >= HEIGHT:
            break
    # Label
    draw.rectangle((0, 0, WIDTH - 1, 18), fill=(0, 0, 0))
    draw.text((4, 1), f"All {n} colors  --  Enter to continue", font=tinyfont, fill=(255, 255, 255))
    lcd.display(image, rotation)


def main():
    print()
    print("ILI9486 Color Chart")
    print("===================")
    print(f"Displaying {len(PALETTE)} named colors.")
    print("Controls: Enter = next page,  Q = quit")
    print()

    # First: show all colors as a density strip
    show_all_at_once()
    print("All-colors overview shown on display.")
    key = get_keypress()
    if key.lower() == 'q':
        return

    # Then page through with full swatches
    pages = [PALETTE[i:i + SWATCHES_PER_PAGE]
             for i in range(0, len(PALETTE), SWATCHES_PER_PAGE)]

    for page_num, page in enumerate(pages, 1):
        show_page(page, page_num, len(pages))
        print(f"Page {page_num}/{len(pages)}: {', '.join(n for n,_ in page)}")
        key = get_keypress()
        print()
        if key.lower() == 'q':
            break

    # Final black screen
    draw.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), fill=(0, 0, 0))
    draw.text((10, HEIGHT // 2 - 10), "Done.", font=font, fill=(255, 255, 255))
    lcd.display(image, rotation)
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        GPIO.cleanup()
        spi.close()
