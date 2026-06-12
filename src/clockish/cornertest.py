#!/usr/bin/env python3
"""
clockish-corners — display offset / framing calibration tool.

Draws single-pixel right-angle corner markers at all four corners of the
display plus a centre crosshair.  If the offsets in your config are correct,
each L-marker's vertex will sit exactly at the physical corner of the panel.

Usage:
    clockish-corners                        # uses default config
    clockish-corners configs/small.yaml     # specify config explicitly
    clockish-corners --arm 30 small.yaml    # longer corner arms

The image stays on screen until you press Ctrl-C.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import yaml
from PIL import Image, ImageDraw, ImageFont

from clockish.drivers import load_driver

# ---------------------------------------------------------------------------
# Config loading (mirrors the logic in display.py)
# ---------------------------------------------------------------------------

def _find_default_config() -> str:
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'clockish.yaml'),
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            '..', '..', 'configs', 'clockish.yaml',
        ),
        os.path.expanduser('~/.config/clockish/clockish.yaml'),
        os.path.expanduser('~/clockish.yaml'),
    ]
    for p in candidates:
        if os.path.isfile(os.path.normpath(p)):
            return os.path.normpath(p)
    return os.path.normpath(candidates[1])


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------

def draw_corner_markers(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    arm: int = 20,
    color: str = '#ffffff',
    gap: int = 0,
) -> None:
    """Draw single-pixel right-angle L-markers at all four corners.

    Each marker consists of two 1-pixel-wide lines of length *arm* that meet
    at the corner pixel.  If the display offsets are correct the vertex of
    each L sits exactly at the physical corner of the panel.

    Parameters
    ----------
    draw:   PIL ImageDraw context
    width, height: image dimensions
    arm:    length of each arm in pixels
    color:  line colour (hex string)
    gap:    inset from the very edge (0 = touch the corner pixel)
    """
    W, H = width - 1, height - 1   # max pixel coordinates
    g = gap
    a = arm - 1                     # arm endpoint offset (0-based)

    corners = [
        # (vertex_x, vertex_y, h_end_x, h_end_y, v_end_x, v_end_y)
        (g,     g,     g + a, g,     g,     g + a),   # top-left
        (W - g, g,     W - g - a, g, W - g, g + a),   # top-right
        (g,     H - g, g + a, H - g, g,     H - g - a),  # bottom-left
        (W - g, H - g, W - g - a, H - g, W - g, H - g - a),  # bottom-right
    ]
    for vx, vy, hx, hy, ex, ey in corners:
        draw.line([(vx, vy), (hx, hy)], fill=color, width=1)  # horizontal arm
        draw.line([(vx, vy), (ex, ey)], fill=color, width=1)  # vertical arm


def draw_centre_cross(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    size: int = 10,
    color: str = '#888888',
) -> None:
    """Draw a small crosshair at the centre of the image."""
    cx, cy = width // 2, height // 2
    draw.line([(cx - size, cy), (cx + size, cy)], fill=color, width=1)
    draw.line([(cx, cy - size), (cx, cy + size)], fill=color, width=1)


def draw_border(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    color: str = '#444444',
) -> None:
    """Draw a single-pixel border around the entire image."""
    draw.rectangle([(0, 0), (width - 1, height - 1)], outline=color, width=1)


def build_test_image(width: int, height: int, arm: int, cfg_path: str) -> Image.Image:
    """Compose the full test image."""
    image = Image.new("RGB", (width, height), "#000000")
    draw  = ImageDraw.Draw(image)

    # Dim full border so it's visible but doesn't distract from the corners
    draw_border(draw, width, height, color='#333333')

    # Bright corner L-markers
    draw_corner_markers(draw, width, height, arm=arm, color='#ffffff')

    # Centre crosshair in mid-grey
    draw_centre_cross(draw, width, height, size=12, color='#666666')

    # Small dimension label near top-centre
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
    label = f"{width}x{height}  arm={arm}"
    if font:
        lw = draw.textlength(label, font=font)
        draw.text(((width - lw) // 2, 4), label, fill='#555555', font=font)

    # Config filename at bottom-centre
    cfg_name = os.path.basename(cfg_path)
    if font:
        cw = draw.textlength(cfg_name, font=font)
        draw.text(((width - cw) // 2, height - 12), cfg_name, fill='#444444', font=font)

    return image


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Corner-marker calibration display for clockish.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        'config', nargs='?', default=None,
        metavar='CONFIG',
        help='Path to YAML config (default: auto-detected clockish.yaml)',
    )
    parser.add_argument(
        '--arm', type=int, default=20, metavar='PX',
        help='Length of each corner arm in pixels (default: 20)',
    )
    parser.add_argument(
        '--once', action='store_true',
        help='Render one frame and exit immediately (no wait loop)',
    )
    args = parser.parse_args()

    cfg_path = args.config or _find_default_config()
    if not os.path.isfile(cfg_path):
        sys.exit(f"ERROR: config file not found: {cfg_path}")

    with open(cfg_path) as f:
        config = yaml.safe_load(f)

    display_cfg = config.get('display', {})
    width  = int(display_cfg.get('width',  320))
    height = int(display_cfg.get('height', 480))

    print(f"Config:  {cfg_path}")
    print(f"Canvas:  {width}x{height}")
    print(f"Driver:  {display_cfg.get('driver', 'ili9486')}")
    print(f"Arm:     {args.arm}px")
    print()

    lcd = load_driver(display_cfg).begin()
    print(f"Display: dimensions={lcd.dimensions}  landscape={lcd.is_landscape}")
    print()

    image = build_test_image(width, height, arm=args.arm, cfg_path=cfg_path)

    print("Sending corner markers to display...")
    lcd.display(image)
    print("Done.  Ctrl-C to exit and release hardware.")

    if args.once:
        lcd.close()
        return

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        lcd.close()
        print("\nHardware released.")


if __name__ == '__main__':
    main()



