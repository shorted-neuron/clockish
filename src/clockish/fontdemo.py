#!/usr/bin/env python3
"""
fontdemo.py - Display all named fonts from clockish on the ILI9486.

Font sizes shown here mirror the built-in scale defined in display.py:
  each name maps to a fixed percentage of the display HEIGHT, so the samples
  look the same relative to the screen regardless of physical resolution.

Controls:
  Enter    - next background color (font sample mode)
  Spc/l/>  - colors - next page
  h/<      - colors - prev page
  +        - colors - font size +1px
  -        - colors - font size -1px
  f        - colors - cycle font
  n        - font mode: cycle default_font (to preview a custom typeface)
  ?        - this help screen (shown on startup)
  q/Q      - quit
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
# Standard font scale  --  mirrors clockish/display.py BUILTIN_FONT_SCALE.
# Each name maps to a fraction of the display HEIGHT.
# This is the single source of truth for the demo; if you change it in
# display.py, update it here too (or import it once display.py has no
# side-effect init at module level).
# ---------------------------------------------------------------------------
BUILTIN_FONT_SCALE: dict[str, float] = {
    'giant':  0.68,
    'huge':   0.45,
    'big':    0.30,
    'med':    0.20,
    'normal': 0.12,
    'small':  0.08,
    'tiny':   0.05,
    'micro':  0.03,
}

# Default font file for the scale demo (overridden by cycling with 'n').
FONT_PATH = 'DejaVuSans.ttf'

# Build FONTS as (name, pct_str, size_px) from the scale.
def _build_fonts(font_path: str) -> tuple[list, dict]:
    """Return (FONTS list, loaded dict) for the given font file."""
    fonts_list = []
    loaded_dict = {}
    for name, frac in BUILTIN_FONT_SCALE.items():
        px = max(1, round(HEIGHT * frac))
        pct_str = f"{frac*100:.0f}%"
        try:
            f = ImageFont.truetype(font_path, px)
        except (IOError, OSError):
            f = ImageFont.load_default()
        fonts_list.append((name, pct_str, px, f))
        loaded_dict[name] = f
    return fonts_list, loaded_dict

FONTS, loaded = _build_fonts(FONT_PATH)
label_font = ImageFont.truetype(FONT_PATH, max(1, round(HEIGHT * BUILTIN_FONT_SCALE['micro'])))

# ---------------------------------------------------------------------------
# Candidate fonts for color-mode cycling (f key) and scale-mode cycling (n key)
# Each entry: (display_name, ttf_filename_or_path)
# Paths are tried in order; first one that loads wins.
# ---------------------------------------------------------------------------
_CANDIDATE_FONTS = [
    ("DejaVu Sans",          "DejaVuSans.ttf"),
    ("DejaVu Sans Bold",     "DejaVuSans-Bold.ttf"),
    ("DejaVu Sans Mono",     "DejaVuSansMono.ttf"),
    ("DejaVu Sans Mono Bold","DejaVuSansMono-Bold.ttf"),
    ("Nixie One",            "NixieOne-Regular.ttf"),
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

# Also search third_party/ subdirectories for custom fonts (e.g. NixieOne).
_tp = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'third_party')
_tp = os.path.normpath(_tp)
if os.path.isdir(_tp):
    for _sub in sorted(os.listdir(_tp)):
        _subpath = os.path.join(_tp, _sub)
        if os.path.isdir(_subpath):
            for _fname in os.listdir(_subpath):
                if _fname.lower().endswith('.ttf'):
                    _full = os.path.join(_subpath, _fname)
                    # Only add if not already in the list by filename
                    if not any(_fname in path for _, path in _CANDIDATE_FONTS):
                        _CANDIDATE_FONTS.append((_fname.replace('.ttf', ''), _full))


def _build_available_fonts() -> list:
    """Return only the candidate fonts that can actually be loaded."""
    available = []
    seen_paths = set()
    for display_name, path in _CANDIDATE_FONTS:
        norm = os.path.normpath(path)
        if norm in seen_paths:
            continue
        try:
            ImageFont.truetype(path, 20)   # test load
            available.append((display_name, path))
            seen_paths.add(norm)
        except (IOError, OSError):
            pass
    if not available:
        available = [("DejaVuSans", "DejaVuSans.ttf")]
    return available

AVAILABLE_FONTS = _build_available_fonts()

# ---------------------------------------------------------------------------
# Background color cycle  --  dark colors from the named palette
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

    # Row 1  --  control hints
    draw.rectangle((0, 0, WIDTH - 1, 16), fill=BY_NAME['DARKGREY'])
    draw.text((2, 0),
              f"size={size}px +/-  Spc=more  h/l=pg  f=font  Enter=smp  Q=quit",
              font=label_font, fill=BY_NAME['SILVER'])

    # Row 2  --  current font name in WHITE (prominent)
    draw.rectangle((0, 18, WIDTH - 1, 35), fill=(30, 30, 60))
    name_f = label_font
    draw.text((2, 18), font_display_name, font=name_f, fill=BY_NAME['WHITE'])

    # Row 3  --  size / metrics info
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


def render_page(bg_name, bg_rgb, fonts_list=None, font_display_name: str = ""):
    """Draw all font scale samples onto the display.

    fonts_list  --  list of (name, pct_str, size_px, font_obj); defaults to FONTS.
    font_display_name  --  typeface label shown in the header bar.
    """
    if fonts_list is None:
        fonts_list = FONTS
    draw.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), fill=bg_rgb)

    # Header bar
    header_f = label_font
    typeface_hint = f"  [{font_display_name}]" if font_display_name else ""
    draw.rectangle((0, 0, WIDTH - 1, 16), fill=BY_NAME['DARKGREY'])
    draw.text((2, 0),
              f"fontdemo{typeface_hint}  bg={bg_name}  Enter=bg  n=font  Space=colors  Q=quit",
              font=header_f, fill=BY_NAME['SILVER'])

    y = 18
    PADDING = 3

    for name, pct_str, size_px, f in fonts_list:
        fh = font_height(f)

        # Label line in a dim bar: font name, scale percentage, pixel size, cell height
        label = f"{name}  ({pct_str} = {size_px}px)  cell={fh}px"
        label_h = font_height(label_font) + 2
        draw.rectangle((0, y, WIDTH - 1, y + label_h - 1), fill=BY_NAME['DIMGREY'])
        draw.text((2, y), label, font=label_font, fill=BY_NAME['GOLD'])
        y += label_h

        # Time-style sample  --  WHITE, most clock-relevant
        draw.text((2, y), SAMPLE_TIME, font=f, fill=BY_NAME['WHITE'])
        y += fh + PADDING

        # Alphabet sample  --  CYAN
        if y + fh + PADDING < HEIGHT - 4:
            draw.text((2, y), SAMPLE_TEXT, font=f, fill=BY_NAME['CYAN'])
            y += fh + PADDING

        # Digit sample  --  ORANGE
        if y + fh + PADDING < HEIGHT - 4:
            draw.text((2, y), SAMPLE_DIGITS, font=f, fill=BY_NAME['ORANGE'])
            y += fh + PADDING

        # Divider  --  DARKGREY
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
    ("Spc/l/->", "colors - next page"),
    ("h/<-",     "colors - prev page"),
    ("+",       "colors - font size +1px"),
    ("-",       "colors - font size -1px"),
    ("f",       "colors - cycle font"),
    ("n",       "font mode: cycle default_font typeface"),
    ("?",       "this help screen"),
    ("q/Q",     "quit"),
]


def render_help():
    """Render the help screen: big rainbow title + control list."""
    draw.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), fill=BY_NAME['BLACK'])

    # --- Big rainbow "fontdemo" title ---
    title      = "fontdemo"
    title_font = loaded.get('big', label_font)
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

    # Scale table: show name -> pct -> px for current display
    scale_font  = loaded.get('micro', label_font)
    scale_fh    = font_height(scale_font) + 2
    y_scale     = sep_y + 4

    draw.text((2, y_scale), f"Display: {WIDTH}x{HEIGHT}  Scale reference:", font=scale_font,
              fill=BY_NAME['GOLD'])
    y_scale += scale_fh
    for name, frac in BUILTIN_FONT_SCALE.items():
        if y_scale + scale_fh > HEIGHT - 2:
            break
        px  = max(1, round(HEIGHT * frac))
        pct = f"{frac*100:.0f}%"
        draw.text((2, y_scale), f"  {name:<7} {pct:>4} = {px}px",
                  font=scale_font, fill=BY_NAME['SILVER'])
        y_scale += scale_fh

    # --- Control list ---
    ctrl_font  = loaded.get('micro', label_font)
    ctrl_fh    = font_height(ctrl_font) + 4
    KEY_W      = 76    # fixed column width for keys

    # If scale table + controls don't fit, just show controls at bottom
    y_ctrl = max(y_scale + 4, HEIGHT - (len(HELP_LINES) * ctrl_fh) - 4)
    draw.rectangle((0, y_ctrl - 2, WIDTH - 1, y_ctrl - 2), fill=BY_NAME['DARKGREY'])

    for key_str, desc_str in HELP_LINES:
        if y_ctrl + ctrl_fh > HEIGHT - 2:
            break
        draw.text((4,         y_ctrl), key_str,  font=ctrl_font, fill=BY_NAME['GOLD'])
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
    print(f"fontdemo  --  built-in font scale for clockish  (display {WIDTH}x{HEIGHT})")
    print("=" * 55)
    print(f"  {'name':<8} {'scale':>5}  {'pixels':>6}  {'cell_h':>6}")
    print(f"  {'-'*8} {'-'*5}  {'-'*6}  {'-'*6}")
    for name, pct_str, size_px, f in FONTS:
        fh = font_height(f)
        print(f"  {name:<8} {pct_str:>5}  {size_px:>6}px  {fh:>6}px")
    print()
    print("Controls:")
    print("  Enter        = next background color  (font sample mode)")
    print("  n            = font mode: cycle typeface (preview default_font options)")
    print("  Space / l /-> = color demo mode  (next page of colors)")
    print("  h / <-        = color mode: prev page of colors")
    print("  + / -        = color mode: increase / decrease font size by 1px")
    print("  f            = color mode: cycle through available fonts")
    print("  ?            = help screen")
    print("  q / Q        = quit")
    print()

    MODE_FONTS  = 'fonts'
    MODE_COLORS = 'colors'
    MODE_HELP   = 'help'

    mode           = MODE_HELP
    bg_index       = 0
    color_size     = _COLOR_MODE_DEFAULT_SIZE   # pixel size for color mode
    color_offset   = 0
    font_index     = 0                          # index into AVAILABLE_FONTS (colors mode)
    scale_fi       = 0                          # index into AVAILABLE_FONTS (scale/fonts mode)
    cur_fonts_list = FONTS                      # active fonts list for font-sample mode
    cur_font_name  = "DejaVu Sans"

    def _redraw_colors():
        fname, fpath = AVAILABLE_FONTS[font_index]
        per = _colors_per_page_px(color_size, fpath)
        page_start = color_offset % len(PALETTE)
        page_end   = (page_start + per - 1) % len(PALETTE)
        print(f"Color mode  font={fname}  size={color_size}px  colors {page_start} - {page_end} of {len(PALETTE)}")
        render_color_page(color_size, color_offset % len(PALETTE), fname, fpath)

    print()
    print("Available fonts for color mode (f) and scale mode (n):")
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

        elif key == 'n':
            # Cycle the typeface used for the font-scale sample page.
            scale_fi = (scale_fi + 1) % len(AVAILABLE_FONTS)
            cur_font_name, cur_font_path = AVAILABLE_FONTS[scale_fi]
            cur_fonts_list, _ = _build_fonts(cur_font_path)
            mode = MODE_FONTS
            bg_name, bg_rgb = BG_COLORS[bg_index % len(BG_COLORS)]
            print(f"Font mode  typeface={cur_font_name}  background={bg_name}")
            render_page(bg_name, bg_rgb, cur_fonts_list, cur_font_name)

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
            print(f"Font mode  typeface={cur_font_name}  background={bg_name}")
            render_page(bg_name, bg_rgb, cur_fonts_list, cur_font_name)

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
        spi.close()
