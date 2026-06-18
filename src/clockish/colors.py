"""
pyili9486.colors - Named color palette for ILI9486 displays (RGB565 / 16-bit color).

This module is intentionally NOT imported by pyili9486.__init__, so existing
users of the library pay zero import cost.  Use it explicitly when you want it:

    from pyili9486.colors import PALETTE, C, RED, GREEN, best_label_color

The ILI9486 display supports 65,536 colors in RGB565 format:
  - Red:   5 bits  -> 32 steps, multiples of 8  (0, 8, 16 ... 248)
  - Green: 6 bits  -> 64 steps, multiples of 4  (0, 4, 8  ... 252)
  - Blue:  5 bits  -> 32 steps, multiples of 8  (0, 8, 16 ... 248)

All RGB values in this palette are snapped to the RGB565 grid so every
color renders exactly as intended with no rounding artefacts.
The palette contains exactly 64 named colors.

Quick-reference
---------------
- PALETTE         list of (name: str, rgb: tuple[int,int,int])  --  all 64 colors
- BY_NAME         dict[str, tuple]  --  look up RGB by name
- C.COLORNAME     attribute-style access returning an RGB tuple
- Module-level    RED, GREEN, ... as '#RRGGBB' hex strings for PIL fill= args
- rgb_to_hex(rgb) -> '#RRGGBB'
- hex_to_rgb(s)   -> (R, G, B)
- luminance(rgb)  -> float (perceived brightness 0-255)
- best_label_color(bg_rgb) -> RGB tuple   (readable text colour for bg)
- best_label_hex(bg_rgb)   -> '#RRGGBB'
"""

# ---------------------------------------------------------------------------
# Palette  --  (name, RGB tuple), ordered by hue family
# All values snapped to RGB565 grid: R & B multiples of 8, G multiples of 4.
# Max values: R=248, G=252, B=248  (5-bit and 6-bit maximums)
# ---------------------------------------------------------------------------
PALETTE: list[tuple[str, tuple[int, int, int]]] = [
    # --- Whites & Greys (6) ---
    ("WHITE",          (248, 252, 248)),
    ("SILVER",         (192, 192, 192)),
    ("GREY",           (128, 128, 128)),
    ("DIMGREY",        ( 96,  96,  96)),
    ("DARKGREY",       ( 64,  64,  64)),
    ("BLACK",          (  0,   0,   0)),

    # --- Reds (5) ---
    ("RED",            (248,   0,   0)),
    ("CRIMSON",        (224,  20,  56)),
    ("FIREBRICK",      (192,  32,  32)),
    ("DARKRED",        (128,   0,   0)),
    ("DIMRED",         ( 64,   0,   0)),

    # --- Oranges (4) ---
    ("ORANGE",         (248, 164,   0)),
    ("DARKORANGE",     (224, 100,   0)),
    ("BURNTSIENNA",    (160,  64,   0)),
    ("RUST",           (128,  32,   0)),

    # --- Browns (3) ---
    ("BROWN",          (136,  68,  24)),
    ("CHOCOLATE",      ( 96,  48,  16)),
    ("TAN",            (208, 160, 112)),

    # --- Yellows (4) ---
    ("YELLOW",         (248, 252,   0)),
    ("GOLD",           (248, 212,   0)),
    ("DARKYELLOW",     (128, 128,   0)),
    ("OLIVE",          ( 96,  96,   0)),

    # --- Yellow-greens (3) ---
    ("LIME",           (128, 252,   0)),
    ("YELLOWGREEN",    (128, 192,   0)),
    ("DARKLIME",       ( 64, 128,   0)),

    # --- Greens (5) ---
    ("BRIGHTGREEN",    (  0, 252,   0)),
    ("LAWNGREEN",      ( 64, 220,   0)),
    ("GREEN",          (  0, 128,   0)),
    ("FORESTGREEN",    ( 16,  96,  16)),
    ("DARKGREEN",      (  0,  48,   0)),

    # --- Cyan-greens (3) ---
    ("MINT",           (  0, 252, 128)),
    ("MEDSPRINGGREEN", (  0, 192,  96)),
    ("SEAGREEN",       ( 32, 128,  64)),

    # --- Cyans (4) ---
    ("CYAN",           (  0, 252, 248)),
    ("AQUA",           (  0, 192, 192)),
    ("DARKCYAN",       (  0, 128, 128)),
    ("TEAL",           (  0,  64,  64)),

    # --- Sky blues (3) ---
    ("SKYBLUE",        (  0, 192, 248)),
    ("CORNFLOWER",     ( 64, 128, 224)),
    ("STEELBLUE",      ( 32,  96, 160)),

    # --- Blues (5) ---
    ("BRIGHTBLUE",     (  0,   0, 248)),
    ("BLUE",           (  0,   0, 192)),
    ("MEDBLUE",        (  0,   0, 128)),
    ("DARKBLUE",       (  0,   0,  64)),
    ("NAVY",           (  0,   0,  48)),

    # --- Blue-purples (3) ---
    ("ROYALBLUE",      ( 48,  64, 224)),
    ("SLATE",          ( 64,  64, 160)),
    ("INDIGO",         ( 48,   0, 128)),

    # --- Purples (4) ---
    ("PURPLE",         (248,   0, 248)),
    ("VIOLET",         (192,   0, 192)),
    ("DARKPURPLE",     (128,   0, 128)),
    ("DIMPURPLE",      ( 64,   0,  64)),

    # --- Pinks & magentas (5) ---
    ("PINK",           (248, 128, 248)),
    ("HOTPINK",        (248,   0, 128)),
    ("DEEPPINK",       (224,   0,  96)),
    ("MAGENTA",        (248,   0, 192)),
    ("ORCHID",         (192,  64, 192)),

    # --- Peach / skin tones (3) ---
    ("SALMON",         (248, 128, 112)),
    ("PEACH",          (248, 192, 160)),
    ("ROSYBROWN",      (184, 112,  96)),

    # --- Mixed / special (4) ---
    ("LAVENDER",       (144, 112, 248)),
    ("PERIWINKLE",     (128, 128, 248)),
    ("CHARTREUSE",     (128, 252,  64)),
    ("SPRINGGREEN",    (  0, 252,  64)),
]

# ---------------------------------------------------------------------------
# Convenience: flat dict  name -> RGB tuple
# ---------------------------------------------------------------------------
BY_NAME: dict[str, tuple[int, int, int]] = {name: rgb for name, rgb in PALETTE}


# ---------------------------------------------------------------------------
# Attribute-style access:  C.RED  ->  (255, 0, 0)
# ---------------------------------------------------------------------------
class _ColorNamespace:
    """Provides attribute-style access to palette colors by name."""
    __slots__ = ()

    def __getattr__(self, name: str) -> tuple[int, int, int]:
        try:
            return BY_NAME[name]
        except KeyError:
            raise AttributeError(f"pyili9486.colors has no color named {name!r}") from None

    def __dir__(self):
        return list(BY_NAME.keys())


C = _ColorNamespace()


# ---------------------------------------------------------------------------
# Module-level hex string constants  RED = "#FF0000"  etc.
# Injected dynamically so the palette is the single source of truth.
# Use:  from pyili9486.colors import RED, GREEN, WHITE
# ---------------------------------------------------------------------------
import sys as _sys

_mod = _sys.modules[__name__]
for _name, _rgb in PALETTE:
    setattr(_mod, _name, "#{:02X}{:02X}{:02X}".format(*_rgb))
del _mod, _name, _rgb, _sys


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    """Convert an (R, G, B) tuple to a '#RRGGBB' string."""
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    """Convert a '#RRGGBB' or 'RRGGBB' string to an (R, G, B) tuple."""
    h = hex_str.lstrip('#')
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def luminance(rgb: tuple[int, int, int]) -> float:
    """Return the perceived luminance of an RGB tuple on a 0 - 255 scale."""
    r, g, b = rgb
    return 0.299 * r + 0.587 * g + 0.114 * b


def best_label_color(bg_rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """
    Return an RGB tuple for readable text drawn on top of bg_rgb.

    - Dark background         -> WHITE  (255, 255, 255)
    - Light background        -> BLACK  (0, 0, 0)
    - Mid-grey background     -> ORANGE (255, 164, 0)   --  cuts through grey
    """
    r, g, b = bg_rgb
    lum = luminance(bg_rgb)
    is_grey = (max(r, g, b) - min(r, g, b)) < 40
    if is_grey and 60 <= lum <= 180:
        return BY_NAME["ORANGE"]
    elif lum < 100:
        return BY_NAME["WHITE"]
    else:
        return BY_NAME["BLACK"]


def best_label_hex(bg_rgb: tuple[int, int, int]) -> str:
    """Same as best_label_color() but returns a '#RRGGBB' hex string."""
    return rgb_to_hex(best_label_color(bg_rgb))


# ---------------------------------------------------------------------------
# Self-test / pretty-print when run directly
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print(f"{len(PALETTE)} colors defined.\n")
    print(f"{'Name':<18} {'Hex':<10} {'RGB':<20} Label color")
    print("-" * 60)
    for name, rgb in PALETTE:
        label = rgb_to_hex(best_label_color(rgb))
        print(f"{name:<18} {rgb_to_hex(rgb):<10} {str(rgb):<20} {label}")
