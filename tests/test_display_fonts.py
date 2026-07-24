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


class TestFontBehaviorResolution:
    """Row default / panel override resolution, done once in _init_layout()."""

    def test_unspecified_defaults_to_default(self) -> None:
        _reset_font_state()
        d._config = {
            'orientation': 'landscape',
            'rows': [{
                'name': 'r', 'height': 100,
                'panels': [{'type': 'text', 'label': 'x'}],
            }],
        }
        d.width, d.height, d.top, d.bottom = 480, 480, 0, 480
        d._init_layout()
        assert d._config['rows'][0]['panels'][0]['font_behavior'] == 'default'

    def test_row_level_default_applies_to_panel(self) -> None:
        _reset_font_state()
        d._config = {
            'orientation': 'landscape',
            'rows': [{
                'name': 'r', 'height': 100, 'font_behavior': 'clip_numeric',
                'panels': [{'type': 'clock'}],
            }],
        }
        d.width, d.height, d.top, d.bottom = 480, 480, 0, 480
        d._init_layout()
        assert d._config['rows'][0]['panels'][0]['font_behavior'] == 'clip_numeric'

    def test_panel_level_overrides_row_level(self) -> None:
        _reset_font_state()
        d._config = {
            'orientation': 'landscape',
            'rows': [{
                'name': 'r', 'height': 100, 'font_behavior': 'clip_numeric',
                'panels': [{'type': 'clock', 'font_behavior': 'scale'}],
            }],
        }
        d.width, d.height, d.top, d.bottom = 480, 480, 0, 480
        d._init_layout()
        assert d._config['rows'][0]['panels'][0]['font_behavior'] == 'scale'

    def test_unknown_value_falls_back_to_default(self) -> None:
        _reset_font_state()
        d._config = {
            'orientation': 'landscape',
            'rows': [{
                'name': 'r', 'height': 100,
                'panels': [{'type': 'clock', 'font_behavior': 'bogus'}],
            }],
        }
        d.width, d.height, d.top, d.bottom = 480, 480, 0, 480
        d._init_layout()
        assert d._config['rows'][0]['panels'][0]['font_behavior'] == 'default'


class TestNumericInkMetrics:
    """clip_numeric: ink metrics from digits only, cached per font object."""

    def test_numeric_metrics_differ_from_default_for_font_with_descenders(self) -> None:
        _reset_font_state()
        f = d.ImageFont.truetype(d._FONT_PATH, 60)
        default_top = d._font_ink_top(f)
        default_h = d._font_height(f) - default_top
        num_top, num_h = d._numeric_ink_metrics(f)
        # Digits have no descenders, so their ink cell is shorter than the
        # 'Ag|' reference (which includes a descender via 'g'). Cap-height
        # ('A' vs digit tops) can differ by a pixel or two due to hinting,
        # so only assert the height difference, not top ordering.
        assert num_h > 0
        assert num_h < default_h

    def test_numeric_metrics_cached_per_font_object(self) -> None:
        _reset_font_state()
        d._NUMERIC_INK_CACHE.clear()
        f = d.ImageFont.truetype(d._FONT_PATH, 40)
        first = d._numeric_ink_metrics(f)
        assert id(f) in d._NUMERIC_INK_CACHE
        second = d._numeric_ink_metrics(f)
        assert first == second


class TestFitFont:
    """scale/stretch_y: binary-searched point size that fits the panel rect."""

    def test_fit_both_axes_stays_within_bounds(self) -> None:
        _reset_font_state()
        path = d._FONT_PATH
        text = "12:34"
        pw, ph = 120, 50
        f = d._fit_font(path, text, pw, ph, axis='both')
        ink_top = d._font_ink_top(f)
        ink_h = d._font_height(f) - ink_top
        assert ink_h <= ph
        assert f.getbbox(text)[2] <= pw

    def test_fit_height_axis_ignores_width(self) -> None:
        _reset_font_state()
        path = d._FONT_PATH
        # A wide string in a narrow, tall panel: height-only fit should pick
        # a size taller than a both-axes fit would (and may overflow width).
        text = "HELLO WORLD"
        pw, ph = 40, 100
        f_height = d._fit_font(path, text, pw, ph, axis='height')
        f_both = d._fit_font(path, text, pw, ph, axis='both')
        assert f_height.size >= f_both.size

    def test_fit_font_result_cached(self) -> None:
        _reset_font_state()
        d._FIT_FONT_CACHE.clear()
        path = d._FONT_PATH
        f1 = d._fit_font(path, "72", 60, 40, axis='both')
        assert (path, "72", 60, 40, 'both') in d._FIT_FONT_CACHE
        f2 = d._fit_font(path, "72", 60, 40, axis='both')
        assert f1 is f2


class TestDrawTextLineBehavior:
    """_draw_text_line() dispatches on `behavior` without crashing and
    respects each mode's contract at the font-selection level."""

    def test_default_behavior_uses_font_as_given(self, monkeypatch) -> None:
        _reset_font_state()
        f = d.ImageFont.truetype(d._FONT_PATH, 30)
        calls = []
        monkeypatch.setattr(d, '_fit_font', lambda *a, **kw: calls.append(a) or f)
        d._draw_text_line(_NullDraw(), 0, 0, 100, 40,
                           "72", f, '#ffffff', behavior='default')
        assert calls == []  # _fit_font must not be called for 'default'

    def test_scale_behavior_calls_fit_font(self, monkeypatch) -> None:
        _reset_font_state()
        f = d.ImageFont.truetype(d._FONT_PATH, 30)
        calls = []

        def _fake_fit(path, text, avail_w, avail_h, axis):
            calls.append((text, avail_w, avail_h, axis))
            return f
        monkeypatch.setattr(d, '_fit_font', _fake_fit)
        d._draw_text_line(_NullDraw(), 0, 0, 100, 40, "72", f, '#ffffff', behavior='scale')
        assert len(calls) == 1
        assert calls[0][3] == 'both'

    def test_stretch_y_behavior_uses_height_axis(self, monkeypatch) -> None:
        _reset_font_state()
        f = d.ImageFont.truetype(d._FONT_PATH, 30)
        calls = []

        def _fake_fit(path, text, avail_w, avail_h, axis):
            calls.append(axis)
            return f
        monkeypatch.setattr(d, '_fit_font', _fake_fit)
        d._draw_text_line(_NullDraw(), 0, 0, 100, 40, "72", f, '#ffffff', behavior='stretch_y')
        assert calls == ['height']


class _NullDraw:
    """Minimal stand-in for ImageDraw.ImageDraw -- records nothing, just
    accepts .text() calls so _draw_text_line() can run without a real canvas."""

    def text(self, *args, **kwargs) -> None:
        pass
