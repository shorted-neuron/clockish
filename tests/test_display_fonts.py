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
                'name': 'r', 'height': 100, 'font_behavior': 'scale_numeric',
                'panels': [{'type': 'clock'}],
            }],
        }
        d.width, d.height, d.top, d.bottom = 480, 480, 0, 480
        d._init_layout()
        assert d._config['rows'][0]['panels'][0]['font_behavior'] == 'scale_numeric'

    def test_panel_level_overrides_row_level(self) -> None:
        _reset_font_state()
        d._config = {
            'orientation': 'landscape',
            'rows': [{
                'name': 'r', 'height': 100, 'font_behavior': 'scale_numeric',
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
    """scale_numeric: ink metrics from digits only, cached per font object."""

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
        assert (path, "72", 60, 40, 'both', False) in d._FIT_FONT_CACHE
        f2 = d._fit_font(path, "72", 60, 40, axis='both')
        assert f1 is f2

    def test_fit_font_numeric_uses_numeric_ink_metrics(self) -> None:
        """numeric=True must fit against digit-only ink height, not 'Ag|'."""
        _reset_font_state()
        d._FIT_FONT_CACHE.clear()
        path = d._FONT_PATH
        text = "72"
        pw, ph = 200, 60
        f_default = d._fit_font(path, text, pw, ph, axis='both', numeric=False)
        f_numeric = d._fit_font(path, text, pw, ph, axis='both', numeric=True)
        # No descenders to worry about -> numeric fit can pick a larger size
        # for the same height budget.
        assert f_numeric.size >= f_default.size
        assert (path, text, pw, ph, 'both', True) in d._FIT_FONT_CACHE


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

        def _fake_fit(path, text, avail_w, avail_h, axis, numeric=False):
            calls.append((text, avail_w, avail_h, axis, numeric))
            return f
        monkeypatch.setattr(d, '_fit_font', _fake_fit)
        d._draw_text_line(_NullDraw(), 0, 0, 100, 40, "72", f, '#ffffff', behavior='scale')
        assert len(calls) == 1
        assert calls[0][3] == 'both'
        assert calls[0][4] is False

    def test_scale_numeric_behavior_calls_fit_font_with_numeric_flag(self, monkeypatch) -> None:
        _reset_font_state()
        f = d.ImageFont.truetype(d._FONT_PATH, 30)
        calls = []

        def _fake_fit(path, text, avail_w, avail_h, axis, numeric=False):
            calls.append((text, avail_w, avail_h, axis, numeric))
            return f
        monkeypatch.setattr(d, '_fit_font', _fake_fit)
        d._draw_text_line(_NullDraw(), 0, 0, 100, 40, "72", f, '#ffffff',
                           behavior='scale_numeric')
        assert len(calls) == 1
        assert calls[0][3] == 'both'
        assert calls[0][4] is True

    def test_stretch_y_behavior_uses_height_axis(self, monkeypatch) -> None:
        _reset_font_state()
        f = d.ImageFont.truetype(d._FONT_PATH, 30)
        calls = []

        def _fake_fit(path, text, avail_w, avail_h, axis, numeric=False):
            calls.append(axis)
            return f
        monkeypatch.setattr(d, '_fit_font', _fake_fit)
        d._draw_text_line(_NullDraw(), 0, 0, 100, 40, "72", f, '#ffffff', behavior='stretch_y')
        assert calls == ['height']

    def test_stretch_x_without_img_falls_back_to_default(self, monkeypatch) -> None:
        """No Image threaded through -> degrade to 'default' rather than crash."""
        _reset_font_state()
        f = d.ImageFont.truetype(d._FONT_PATH, 30)
        calls = []
        monkeypatch.setattr(d, '_draw_text_stretch_x',
                             lambda *a, **kw: calls.append('stretched'))
        d._draw_text_line(_NullDraw(), 0, 0, 100, 40, "72", f, '#ffffff',
                           behavior='stretch_x', img=None)
        assert calls == []  # never called the stretch path

    def test_stretch_x_with_img_calls_stretch_helper(self, monkeypatch) -> None:
        _reset_font_state()
        f = d.ImageFont.truetype(d._FONT_PATH, 30)
        calls = []
        monkeypatch.setattr(d, '_draw_text_stretch_x',
                             lambda *a, **kw: calls.append(a))
        fake_img = object()
        d._draw_text_line(_NullDraw(), 5, 6, 100, 40, "72", f, '#ffffff',
                           behavior='stretch_x', img=fake_img)
        assert len(calls) == 1
        assert calls[0][0] is fake_img


class TestStretchXRendering:
    """_draw_text_stretch_x() actually stretches ink to fill the panel width."""

    def test_ink_spans_full_available_width(self) -> None:
        _reset_font_state()
        f = d.ImageFont.truetype(d._FONT_PATH, 60)
        img = d.Image.new('RGB', (480, 320), (0, 0, 0))
        draw = d.ImageDraw.Draw(img)
        px, py, pw, ph = 50, 50, 300, 100
        d._draw_text_line(draw, px, py, pw, ph, "72", f, '#ffffff',
                           behavior='stretch_x', img=img)

        # Sample the drawn image: ink should span most of [px, px+pw).
        pixels = img.load()
        xs_with_ink = [
            x for x in range(px, px + pw)
            if any(pixels[x, y][0] > 10 for y in range(py, py + ph))
        ]
        assert xs_with_ink, "expected some ink to be drawn"
        span = max(xs_with_ink) - min(xs_with_ink)
        assert span > pw * 0.7  # stretched to fill most of the panel width

    def test_fallback_to_default_when_img_none(self) -> None:
        """Full _draw_text_line() call with img=None must not raise."""
        _reset_font_state()
        f = d.ImageFont.truetype(d._FONT_PATH, 30)
        d._draw_text_line(_NullDraw(), 0, 0, 100, 40, "72", f, '#ffffff',
                           behavior='stretch_x', img=None)  # should not raise


class TestPanelPadding:
    """_dispatch_panel() insets (px, py, pw, ph) by 'padding:' before handing
    off to the type-specific renderer; background fill uses the unpadded rect."""

    def _spy_rect(self, monkeypatch, panel_type: str):
        _reset_font_state()
        calls = []
        name = {
            'text': '_render_text_panel', 'clock': '_render_clock_panel',
        }[panel_type]
        orig = getattr(d, name)

        def spy(p, px, py, pw, ph, *rest):
            calls.append((px, py, pw, ph))
            return orig(p, px, py, pw, ph, *rest)
        monkeypatch.setattr(d, name, spy)
        return calls

    def test_default_padding_is_1px(self, monkeypatch) -> None:
        calls = self._spy_rect(monkeypatch, 'text')
        panel = {'type': 'text', 'label': 'x'}
        d._dispatch_panel(panel, 0, 0, 100, 50, {}, {}, 0, _NullDraw())
        assert calls == [(1, 1, 98, 48)]

    def test_explicit_padding_insets_all_sides(self, monkeypatch) -> None:
        calls = self._spy_rect(monkeypatch, 'text')
        panel = {'type': 'text', 'label': 'x', 'padding': 10}
        d._dispatch_panel(panel, 0, 0, 100, 50, {}, {}, 0, _NullDraw())
        assert calls == [(10, 10, 80, 30)]

    def test_zero_padding_allowed(self, monkeypatch) -> None:
        calls = self._spy_rect(monkeypatch, 'text')
        panel = {'type': 'text', 'label': 'x', 'padding': 0}
        d._dispatch_panel(panel, 0, 0, 100, 50, {}, {}, 0, _NullDraw())
        assert calls == [(0, 0, 100, 50)]

    def test_invalid_padding_falls_back_to_default_1px(self, monkeypatch) -> None:
        calls = self._spy_rect(monkeypatch, 'text')
        panel = {'type': 'text', 'label': 'x', 'padding': 'bogus'}
        d._dispatch_panel(panel, 0, 0, 100, 50, {}, {}, 0, _NullDraw())
        assert calls == [(1, 1, 98, 48)]

    def test_padding_never_shrinks_below_1px(self, monkeypatch) -> None:
        """A panel too small for the requested padding still gets a >=1px rect."""
        calls = self._spy_rect(monkeypatch, 'text')
        panel = {'type': 'text', 'label': 'x', 'padding': 50}
        d._dispatch_panel(panel, 0, 0, 10, 10, {}, {}, 0, _NullDraw())
        px, py, pw, ph = calls[0]
        assert pw >= 1 and ph >= 1

    def test_padding_passed_through_to_img_for_stretch_x(self, monkeypatch) -> None:
        """target_img threaded through _dispatch_panel reaches the renderer."""
        _reset_font_state()
        calls = self._spy_rect(monkeypatch, 'clock')
        panel = {'type': 'clock', 'padding': 5}
        fake_img = object()
        import datetime as _dt
        d._dispatch_panel(panel, 0, 0, 100, 50,
                           {'local': _dt.datetime(2028, 1, 1)}, {}, 0,
                           _NullDraw(), fake_img)
        assert calls == [(5, 5, 90, 40)]


class _NullDraw:
    """Minimal stand-in for ImageDraw.ImageDraw -- records nothing, just
    accepts .text() calls so _draw_text_line() can run without a real canvas."""

    def text(self, *args, **kwargs) -> None:
        pass
