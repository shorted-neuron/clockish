#!/usr/bin/env python3
"""
fontdemo.py - Display all named fonts from clockish on the ILI9486.

Controls:
  Enter    — next background color (font sample mode)
  Spc/l/→  — colors - next page
  h/←      — colors - prev page
  +        — colors - font size +1px
  -        — colors - font size -1px
  f        — colors - cycle font
  ?        — this help screen (shown on startup)
  q/Q      — quit
"""

import sys
import os
import termios
import tty

from PIL import Image, ImageDraw, ImageFont
from spidev import SpiDev
from pyili9486 import ILI9486, Origin, SKU
from pyili9486.gpio.rpilgpio_facade import RPiLGPIOFacade
from clockish.colors import BY_NAME, PALETTE

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
spi = SpiDev(SPI_BUS, SPI_DEVICE)
spi.mode = 0b10
spi.max_speed_hz = 64000000
# Change origin to control physical orientation:
#   Origin.UPPER_LEFT   -> landscape (480x320)
#   Origin.UPPER_RIGHT  -> portrait  (320x480)
#   Origin.LOWER_LEFT   -> portrait  (320x480)
#   Origin.LOWER_RIGHT  -> landscape (480x320)
DISPLAY_ORIGIN = Origin.UPPER_RIGHT
_gpio = RPiLGPIOFacade(dc_pin=DC_PIN, rs_pin=RST_PIN)
lcd = ILI9486(spi=spi, gpio_facade=_gpio, origin=DISPLAY_ORIGIN, sku=SKU.MPI3501).begin()

# Always derive WIDTH/HEIGHT from the LCD so they match regardless of origin.
WIDTH, HEIGHT = lcd.dimensions
rotation = 0   # hardware orientation is handled by DISPLAY_ORIGIN above

image = Image.new("RGB", (WIDTH, HEIGHT))
draw  = ImageDraw.Draw(image)

# ---------------------------------------------------------------------------
# Font definitions — must match clockish exactly
# ---------------------------------------------------------------------------
FONT_PATH = 'DejaVuSans.ttf'

FONTS = [
    ('huge',   72),
    ('big',    48),
    ('med',    36),
    ('normal', 28),
    ('small',  20),
    ('tiny',   14),
]

# Load them all
loaded = {name: ImageFont.truetype(FONT_PATH, size) for name, size in FONTS}
label_font = loaded['tiny']   # font used for the name/size label line

# ---------------------------------------------------------------------------
# Candidate fonts for color-mode cycling (f key)
# Each entry: (display_name, ttf_filename_or_path)
# Paths are tried in order; first one that loads wins.
# ---------------------------------------------------------------------------
_CANDIDATE_FONTS = [
    ("DejaVu Sans",          "DejaVuSans.ttf"),
    ("DejaVu Sans Bold",     "DejaVuSans-Bold.ttf"),
    ("DejaVu Sans Mono",     "DejaVuSansMono.ttf"),
    ("DejaVu Sans Mono Bold","DejaVuSansMono-Bold.ttf"),
    ("Liberation Sans",      "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    ("Liberation Sans Bold", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ("Liberation Mono",      "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"),
    ("Noto Sans",            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
    ("Ubuntu",               "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf"),
    ("Ubuntu Bold",          "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf"),
    ("Ubuntu Condensed",     "/usr/share/fonts/truetype/ubuntu/UbuntuCondensed-R.ttf"),
    ("FreeSans",             "/usr/share/fonts/truetype/freefont/FreeSans.ttf"),
    ("FreeSans Bold",        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"),
]

def _build_available_fonts() -> list:
    """Return only the candidate fonts that can actually be loaded."""
    available = []
    for display_name, path in _CANDIDATE_FONTS:
        try:
            ImageFont.truetype(path, 20)   # test load
            available.append((display_name, path))
        except (IOError, OSError):
            pass
    if not available:
        available = [("DejaVuSans", "DejaVuSans.ttf")]
    return available

AVAILABLE_FONTS = _build_available_fonts()

# ---------------------------------------------------------------------------
# Background color cycle — dark colors from the named palette
# ---------------------------------------------------------------------------
_BG_NAMES = ['BLACK', 'DARKGREY', 'NAVY', 'DIMRED', 'DARKGREEN',
             'TEAL', 'INDIGO', 'DIMPURPLE', 'DIMGREY', 'RUST']
BG_COLORS = [(name, BY_NAME[name]) for name in _BG_NAMES if name in BY_NAME]

# ---------------------------------------------------------------------------
# Sample strings
# ---------------------------------------------------------------------------
SAMPLE_TEXT   = "AaBbCc Xyz"
SAMPLE_DIGITS = "0123456789"
SAMPLE_TIME   = "12:34 PM"


# ---------------------------------------------------------------------------
# Color demo mode
# ---------------------------------------------------------------------------

# Default pixel size for color mode
_COLOR_MODE_DEFAULT_SIZE = 20
_COLOR_MODE_MIN_SIZE     = 8
_COLOR_MODE_MAX_SIZE     = 72


def _colors_per_page_px(size: int, font_path: str = FONT_PATH) -> int:
    """How many color-name lines fit on screen for a given pixel font size."""
    fh = size + 4
    try:
        _f = ImageFont.truetype(font_path, size)
        fh = font_height(_f)
    except Exception:
        pass
    label_h = font_height(label_font) + 2
    avail = HEIGHT - 36 - label_h   # 36 = 18 header + 18 font-name bar
    return max(1, avail // (fh + 2))


def render_color_page(size: int, color_offset: int,
                      font_display_name: str = "DejaVu Sans",
                      font_path: str = FONT_PATH) -> int:
    """
    Render a page of palette colors.  Each row shows:
      COLORNAME  #RRGGBB  <size>px  colorname
    rendered in that color on a black background.
    Returns the number of colors shown.
    """
    f   = ImageFont.truetype(font_path, size)
    fh  = font_height(f)
    label_h = font_height(label_font) + 2
    content_start = 36 + label_h + 1   # below header (18) + font-name bar (18) + info bar
    per = max(1, (HEIGHT - content_start) // (fh + 2))

    draw.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), fill=BY_NAME['BLACK'])

    # Row 1 — control hints
    draw.rectangle((0, 0, WIDTH - 1, 16), fill=BY_NAME['DARKGREY'])
    draw.text((2, 0),
              f"size={size}px +/-  Spc=more  h/l=pg  f=font  Enter=smp  Q=quit",
              font=label_font, fill=BY_NAME['SILVER'])

    # Row 2 — current font name in WHITE (prominent)
    draw.rectangle((0, 18, WIDTH - 1, 35), fill=(30, 30, 60))
    name_f = label_font
    draw.text((2, 18), font_display_name, font=name_f, fill=BY_NAME['WHITE'])

    # Row 3 — size / metrics info
    label = f"size={size}px  h={fh}px  {len(PALETTE)} colors"
    draw.rectangle((0, 36, WIDTH - 1, 36 + label_h - 1), fill=BY_NAME['DIMGREY'])
    draw.text((2, 36), label, font=label_font, fill=BY_NAME['GOLD'])

    y = content_start
    shown = 0
    for i in range(per):
        idx = (color_offset + i) % len(PALETTE)
        cname, crgb = PALETTE[idx]
        r, g, b = crgb
        hex_str = f"#{r:02X}{g:02X}{b:02X}"
        text = f"{cname}  {hex_str}  {size}px  {cname.lower()}"
        draw.text((2, y), text, font=f, fill=crgb)
        y += fh + 2
        shown += 1

    lcd.display(image, rotation)
    return shown


# ---------------------------------------------------------------------------
# Font height and render_page functions
# ---------------------------------------------------------------------------

def font_height(f):
    """Return the full rendered line height (ascender + descender) for a font."""
    try:
        bbox = f.getbbox("Ag0|", anchor='lt')
        return bbox[3]
    except Exception:
        ascent, descent = f.getmetrics()
        return ascent + descent


def render_page(bg_name, bg_rgb):
    """Draw all font samples onto the display."""
    draw.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), fill=bg_rgb)

    # Header bar
    header_f = loaded['tiny']
    draw.rectangle((0, 0, WIDTH - 1, 16), fill=BY_NAME['DARKGREY'])
    draw.text((2, 0), f"fontdemo  bg={bg_name}  Enter=next bg  Space=colors  Q=quit",
              font=header_f, fill=BY_NAME['SILVER'])

    y = 18
    PADDING = 3

    for name, size in FONTS:
        f = loaded[name]
        fh = font_height(f)

        # Label line in a dim bar: font name, point size, rendered pixel height
        label = f"{name}  ({size}pt)  h={fh}px"
        label_h = font_height(label_font) + 2
        draw.rectangle((0, y, WIDTH - 1, y + label_h - 1), fill=BY_NAME['DIMGREY'])
        draw.text((2, y), label, font=label_font, fill=BY_NAME['GOLD'])
        y += label_h

        # Time-style sample — WHITE, most clock-relevant
        draw.text((2, y), SAMPLE_TIME, font=f, fill=BY_NAME['WHITE'])
        y += fh + PADDING

        # Alphabet sample — CYAN
        if y + fh + PADDING < HEIGHT - 4:
            draw.text((2, y), SAMPLE_TEXT, font=f, fill=BY_NAME['CYAN'])
            y += fh + PADDING

        # Digit sample — ORANGE
        if y + fh + PADDING < HEIGHT - 4:
            draw.text((2, y), SAMPLE_DIGITS, font=f, fill=BY_NAME['ORANGE'])
            y += fh + PADDING

        # Divider — DARKGREY
        if y < HEIGHT - 4:
            draw.rectangle((0, y, WIDTH - 1, y), fill=BY_NAME['DARKGREY'])
            y += 2

        if y >= HEIGHT - 4:
            break

    lcd.display(image, rotation)


# ---------------------------------------------------------------------------
# Help screen
# ---------------------------------------------------------------------------

# Colors used to paint the big "fontdemo" title letters, cycling through palette
_TITLE_COLORS = ['CYAN', 'ORANGE', 'BRIGHTGREEN', 'PINK', 'YELLOW',
                 'SKYBLUE', 'LAVENDER', 'GOLD', 'MINT', 'SALMON']

HELP_LINES = [
    ("Enter",   "next background color"),
    ("Spc/l/→", "colors - next page"),
    ("h/←",     "colors - prev page"),
    ("+",       "colors - font size +1px"),
    ("-",       "colors - font size -1px"),
    ("f",       "colors - cycle font"),
    ("?",       "this help screen"),
    ("q/Q",     "quit"),
]


def render_help():
    """Render the help screen: big rainbow title + control list."""
    draw.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), fill=BY_NAME['BLACK'])

    # --- Big rainbow "fontdemo" title ---
    title      = "fontdemo"
    title_font = loaded['big']       # 36pt — fits the width comfortably
    title_fh   = font_height(title_font)

    # Measure per-character width so we can colour each letter differently
    x_cursor = 2
    y_title  = 4
    for i, ch in enumerate(title):
        color_name = _TITLE_COLORS[i % len(_TITLE_COLORS)]
        draw.text((x_cursor, y_title), ch, font=title_font,
                  fill=BY_NAME[color_name])
        # advance by character width
        bbox = title_font.getbbox(ch)
        x_cursor += bbox[2] - bbox[0] + 1

    # Thin separator
    sep_y = y_title + title_fh + 6
    draw.rectangle((0, sep_y, WIDTH - 1, sep_y), fill=BY_NAME['DARKGREY'])

    # --- Control list in 'small' font ---
    ctrl_font  = loaded['small']
    key_font   = loaded['small']
    ctrl_fh    = font_height(ctrl_font) + 4
    KEY_W      = 76    # fixed column width for keys
    y_ctrl     = sep_y + 6

    for key_str, desc_str in HELP_LINES:
        if y_ctrl + ctrl_fh > HEIGHT - 2:
            break
        draw.text((4,         y_ctrl), key_str,  font=key_font,  fill=BY_NAME['GOLD'])
        draw.text((4 + KEY_W, y_ctrl), desc_str, font=ctrl_font, fill=BY_NAME['SILVER'])
        y_ctrl += ctrl_fh

    lcd.display(image, rotation)


def get_keypress():
    """Read one keypress.  Arrow keys are returned as 'UP', 'DOWN', 'LEFT', 'RIGHT'."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == '\x1b':
            # Try to read the rest of an escape sequence (non-blocking feel via
            # a short read; if nothing follows it's a bare Escape key).
            try:
                ch2 = sys.stdin.read(1)
                if ch2 == '[':
                    ch3 = sys.stdin.read(1)
                    return {'A': 'UP', 'B': 'DOWN', 'C': 'RIGHT', 'D': 'LEFT'}.get(ch3, '\x1b')
            except Exception:
                pass
            return '\x1b'
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return ch


def main():
    print()
    print("fontdemo — font size reference for clockish")
    print("=" * 45)
    for name, size in FONTS:
        fh = font_height(loaded[name])
        print(f"  {name:<8} {size:>3}pt   rendered height: {fh}px")
    print()
    print("Controls:")
    print("  Enter        = next background color  (font sample mode)")
    print("  Space / l /→ = color demo mode  (next page of colors)")
    print("  h / ←        = color mode: prev page of colors")
    print("  + / -        = color mode: increase / decrease font size by 1px")
    print("  f            = color mode: cycle through available fonts")
    print("  ?            = help screen")
    print("  q / Q        = quit")
    print()

    MODE_FONTS  = 'fonts'
    MODE_COLORS = 'colors'
    MODE_HELP   = 'help'

    mode         = MODE_HELP
    bg_index     = 0
    color_size   = _COLOR_MODE_DEFAULT_SIZE   # pixel size for color mode
    color_offset = 0
    font_index   = 0                          # index into AVAILABLE_FONTS

    def _redraw_colors():
        fname, fpath = AVAILABLE_FONTS[font_index]
        per = _colors_per_page_px(color_size, fpath)
        page_start = color_offset % len(PALETTE)
        page_end   = (page_start + per - 1) % len(PALETTE)
        print(f"Color mode  font={fname}  size={color_size}px  colors {page_start}–{page_end} of {len(PALETTE)}")
        render_color_page(color_size, color_offset % len(PALETTE), fname, fpath)

    print()
    print("Available fonts for color mode (f to cycle):")
    for i, (fname, fpath) in enumerate(AVAILABLE_FONTS):
        print(f"  [{i}] {fname}  ({fpath})")
    print()
    # Show help on startup
    render_help()
    print("Help screen shown. Press ? at any time to return to it.")

    while True:
        key = get_keypress()

        if key.lower() == 'q':
            break

        elif key == '?':
            mode = MODE_HELP
            print("Help screen")
            render_help()

        elif key == ' ' or (key in ('l', 'RIGHT') and mode == MODE_COLORS):
            if mode != MODE_COLORS:
                mode         = MODE_COLORS
                color_offset = 0
            else:
                per = _colors_per_page_px(color_size)
                color_offset = (color_offset + per) % len(PALETTE)
            _redraw_colors()

        elif key in ('h', 'LEFT') and mode == MODE_COLORS:
            per = _colors_per_page_px(color_size)
            color_offset = (color_offset - per) % len(PALETTE)
            _redraw_colors()

        elif key == '+' and mode == MODE_COLORS:
            color_size = min(_COLOR_MODE_MAX_SIZE, color_size + 1)
            color_offset = 0
            _redraw_colors()

        elif key == '-' and mode == MODE_COLORS:
            color_size = max(_COLOR_MODE_MIN_SIZE, color_size - 1)
            color_offset = 0
            _redraw_colors()

        elif key == 'f' and mode == MODE_COLORS:
            font_index = (font_index + 1) % len(AVAILABLE_FONTS)
            color_offset = 0
            _redraw_colors()

        elif key == '\r' or key == '\n':
            mode     = MODE_FONTS
            bg_index += 1
            bg_name, bg_rgb = BG_COLORS[bg_index % len(BG_COLORS)]
            print(f"Font mode  background={bg_name}")
            render_page(bg_name, bg_rgb)

    # Clear to black on exit
    draw.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), fill=BY_NAME['BLACK'])
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
