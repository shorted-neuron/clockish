"""tests/test_display_fonts.py
==============================
Regression test for the named-scale font bootstrap ordering bug.

Bug history
-----------
`_get_font()` bootstraps the built-in named-scale fonts (giant/huge/big/med/
normal/small/tiny/micro) into the module-level `_FONTS` cache on first call,
guarded historically by `if not _FONTS:`. But `_init_layout()` also writes
directly into `_FONTS` (synthetic `_res_*` / `_auto_*` keys for panels using
`font:` or `font_size: auto`), independent of `_get_font()`.

Production's `main()` always calls `_get_font()` for a few scales *before*
`_init_layout()` runs, so the bootstrap fires correctly there. But
`render_preview.py` calls `_init_layout()` directly without priming
`_get_font()` first -- so for any config with a `font:` / `font_size: auto`
panel, `_FONTS` was already non-empty by the time a named scale was first
requested, the `if not _FONTS:` guard was skipped, and every giant/huge/
normal/... request silently fell back to `ImageFont.load_default()` (tiny
fixed-size bitmap font) instead of the configured `default_font`.

Fix: a dedicated `_SCALE_FONTS_LOADED` flag, decoupled from `_FONTS`
contents, tracks whether the bootstrap has run.
"""
from __future__ import annotations

from PIL import ImageFont

import clockish.display as d


def _reset_font_state() -> None:
    d._FONTS.clear()
    d._SCALE_FONTS_LOADED = False
    # main() normally sets this before _get_font()/_init_layout() ever run;
    # tests call those directly without going through main().
    d._FONT_PATH = d._find_font('DejaVuSans.ttf')


class TestNamedScaleFontBootstrapOrdering:
    """_init_layout() populating _FONTS first must not block the named-scale
    bootstrap in _get_font()."""

    def test_named_scale_survives_init_layout_with_font_attr_panel(self) -> None:
        """A panel with an explicit `font:` (resolved during _init_layout())
        must not prevent 'giant'/'normal' etc. from loading the real
        default_font typeface afterwards."""
        _reset_font_state()
        d._config = {
            'orientation': 'landscape',
            'rows': [{
                'name': 'r', 'height': 100,
                'panels': [
                    {'type': 'debug', 'font': 'debug', 'color': 'grey'},
                ],
            }],
        }
        d.width, d.height, d.top, d.bottom = 480, 320, 0, 320
        d._init_layout()

        giant = d._get_font('giant')
        normal = d._get_font('normal')

        assert isinstance(giant, ImageFont.FreeTypeFont)
        assert isinstance(normal, ImageFont.FreeTypeFont)
        # Real named-scale sizes (fraction of height=320), not the fixed
        # ~10px ImageFont.load_default() fallback.
        assert giant.size == int(320 * d.BUILTIN_FONT_SCALE['giant'])
        assert normal.size == int(320 * d.BUILTIN_FONT_SCALE['normal'])

    def test_named_scale_survives_init_layout_with_font_size_auto_panel(self) -> None:
        """A panel with `font_size: auto` and no `font:` (also resolved
        during _init_layout()) must not prevent named scales from loading."""
        _reset_font_state()
        d._config = {
            'orientation': 'landscape',
            'rows': [{
                'name': 'r', 'height': 100,
                'panels': [
                    {'type': 'fact', 'source': 'cpu', 'font_size': 'auto'},
                ],
            }],
        }
        d.width, d.height, d.top, d.bottom = 480, 320, 0, 320
        d._init_layout()

        giant = d._get_font('giant')

        assert isinstance(giant, ImageFont.FreeTypeFont)
        assert giant.size == int(320 * d.BUILTIN_FONT_SCALE['giant'])

    def test_default_font_typeface_used_for_named_scales(self) -> None:
        """When 'default_font' is set, named-scale fonts must load that
        typeface, not DejaVu, even after _init_layout() touches _FONTS
        first via an unrelated font:/auto panel."""
        _reset_font_state()
        d._config = {
            'orientation': 'landscape',
            'default_font': 'DejaVuSans-Bold.ttf',
            'rows': [{
                'name': 'r', 'height': 100,
                'panels': [
                    {'type': 'debug', 'font': 'debug', 'color': 'grey'},
                    {'type': 'text', 'label': 'hi', 'font_size': 'giant'},
                ],
            }],
        }
        d.width, d.height, d.top, d.bottom = 480, 320, 0, 320
        d._init_layout()

        giant = d._get_font('giant')
        assert 'DejaVuSans-Bold' in giant.path

    def test_scale_fonts_loaded_only_once_per_reset(self) -> None:
        """The bootstrap must run exactly once between resets, and _get_font
        for a second named scale must not re-trigger it."""
        _reset_font_state()
        d._config = {'orientation': 'landscape', 'rows': []}
        d.width, d.height, d.top, d.bottom = 480, 320, 0, 320

        assert d._SCALE_FONTS_LOADED is False
        d._get_font('normal')
        assert d._SCALE_FONTS_LOADED is True
        cached = d._FONTS['normal']
        d._get_font('tiny')
        # Bootstrap didn't re-run and clobber the already-loaded 'normal' entry.
        assert d._FONTS['normal'] is cached


class TestBareFontSizeWithoutFontAttr:
    """`font_size: <px|%|number>` must work even when the panel has no
    `font:` attribute -- previously it was silently ignored and the panel
    fell back to the 'normal' named-scale font instead of the requested size."""

    def test_px_string_resolves_to_literal_pixels(self) -> None:
        _reset_font_state()
        d._config = {
            'orientation': 'landscape',
            'rows': [{
                'name': 'r', 'height': 100,
                'panels': [{'type': 'text', 'label': 'x', 'font_size': '16px'}],
            }],
        }
        d.width, d.height, d.top, d.bottom = 480, 480, 0, 480
        d._init_layout()
        panel = d._config['rows'][0]['panels'][0]
        f = d._get_font(panel['font_size'])
        assert f.size == 16

    def test_percent_string_resolves_against_display_height(self) -> None:
        _reset_font_state()
        d._config = {
            'orientation': 'landscape',
            'rows': [{
                'name': 'r', 'height': 100,
                'panels': [{'type': 'text', 'label': 'x', 'font_size': '10%'}],
            }],
        }
        d.width, d.height, d.top, d.bottom = 480, 480, 0, 480
        d._init_layout()
        panel = d._config['rows'][0]['panels'][0]
        f = d._get_font(panel['font_size'])
        assert f.size == 48  # 10% of height=480

    def test_named_scale_still_handled_by_get_font_directly(self) -> None:
        """Bare named-scale strings ('small' etc.) must NOT be rewritten by
        _init_layout() -- they stay as-is and resolve via _get_font()'s own
        bootstrap, same as before this fix."""
        _reset_font_state()
        d._config = {
            'orientation': 'landscape',
            'rows': [{
                'name': 'r', 'height': 100,
                'panels': [{'type': 'text', 'label': 'x', 'font_size': 'small'}],
            }],
        }
        d.width, d.height, d.top, d.bottom = 480, 480, 0, 480
        d._init_layout()
        panel = d._config['rows'][0]['panels'][0]
        assert panel['font_size'] == 'small'
