"""tests/test_clock_off_color.py
==============================
Clock panel ``off_color``: unlit segments drawn as dim 8s under the time.
"""
from __future__ import annotations

import datetime

from PIL import Image, ImageDraw

import clockish.display as d
from clockish.config_validator import validate_config_dict

ON = (255, 0, 0)
OFF = (0, 0, 255)
NOW = datetime.datetime(2026, 9, 25, 11, 11, 11)


def _render(**panel: object) -> Image.Image:
    d._FONTS.clear()
    d._SCALE_FONTS_LOADED = False
    d._FONT_PATH = d._find_font('DejaVuSans.ttf')
    p = {'type': 'clock', 'color': '#ff0000', 'font_size': 'big', **panel}
    img = Image.new('RGB', (200, 60))
    d._render_clock_panel(p, 0, 0, 200, 60, NOW, ImageDraw.Draw(img), img)
    return img


def _count(img: Image.Image, rgb: tuple[int, int, int]) -> int:
    return dict((c, n) for n, c in img.getcolors(img.width * img.height)).get(rgb, 0)


def test_off_color_draws_dim_eights_under_time() -> None:
    img = _render(off_color='#0000ff')
    assert _count(img, OFF) > 0
    assert _count(img, ON) == _count(_render(), ON)


def test_no_off_color_draws_only_time() -> None:
    assert _count(_render(), OFF) == 0


def test_validator_accepts_off_color_on_clock() -> None:
    cfg = {'orientation': 'landscape', 'rows': [{'name': 'r', 'height': 40, 'panels': [
        {'type': 'clock', 'off_color': 'dimred'}]}]}
    result = validate_config_dict(cfg)
    assert result.ok and not result.warnings, result.warnings
