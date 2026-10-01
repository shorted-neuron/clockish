"""tests/test_bit_clock.py
=========================
``bit_clock`` panel: renderer (bit value, order, wrap, epoch, grid) and
validator checks.
"""
from __future__ import annotations

import datetime

import pytest
from PIL import Image, ImageDraw

import clockish.display as d
from clockish.config_validator import validate_config_dict

ON = (255, 0, 0)
OFF = (0, 0, 255)
UTC = datetime.timezone.utc


def _render(panel: dict, now: datetime.datetime, w: int = 320, h: int = 20) -> list[int]:
    """Render ``panel`` and return the lit state of each cell, reading order."""
    p = {'on_color': '#ff0000', 'off_color': '#0000ff', **panel}
    img = Image.new('RGB', (w, h))
    d._render_bit_clock_panel(p, 0, 0, w, h, now, ImageDraw.Draw(img))
    bits = d._positive_int(p.get('bits'), 32)
    rows = min(d._positive_int(p.get('bit_rows'), 1), bits)
    cols = -(-bits // rows)
    out = []
    for i in range(bits):
        r, c = divmod(i, cols)
        px = img.getpixel((int((c + 0.5) * w / cols), int((r + 0.5) * h / rows)))
        assert px in (ON, OFF), f"cell {i} centre is {px}, not an on/off color"
        out.append(1 if px == ON else 0)
    return out


def _bits_msb(value: int, n: int) -> list[int]:
    return [(value >> b) & 1 for b in range(n - 1, -1, -1)]


class TestBitClockRender:

    def test_default_is_32_bit_unix_time_msb_first(self) -> None:
        now = datetime.datetime(2026, 9, 23, 12, 34, 56, tzinfo=UTC)
        assert _render({}, now) == _bits_msb(int(now.timestamp()), 32)

    def test_naive_now_is_local_time(self) -> None:
        now = datetime.datetime(2026, 9, 23, 12, 34, 56)
        assert _render({}, now) == _bits_msb(int(now.timestamp()), 32)

    def test_lsb_reverses_order(self) -> None:
        now = datetime.datetime(2026, 9, 23, 12, 34, 56, tzinfo=UTC)
        assert _render({'bit_order': 'lsb'}, now) == _bits_msb(int(now.timestamp()), 32)[::-1]

    def test_fewer_bits_wraps(self) -> None:
        now = datetime.datetime(2026, 9, 23, 12, 34, 56, tzinfo=UTC)
        value = int(now.timestamp()) % (1 << 12)
        assert _render({'bits': 12}, now) == _bits_msb(value, 12)

    def test_custom_epoch(self) -> None:
        now = datetime.datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC)
        for epoch in ('2026-01-01T00:00:00Z', datetime.date(2026, 1, 1),
                      datetime.datetime(2026, 1, 1, tzinfo=UTC)):
            assert _render({'bits': 8, 'epoch': epoch}, now) == _bits_msb(5, 8)

    def test_before_epoch_wraps_mod_2_pow_bits(self) -> None:
        now = datetime.datetime(1969, 12, 31, 23, 59, 59, tzinfo=UTC)
        assert _render({'bits': 8}, now) == [1] * 8

    def test_bad_epoch_falls_back_to_unix(self) -> None:
        now = datetime.datetime(1970, 1, 1, 0, 0, 3, tzinfo=UTC)
        assert _render({'bits': 4, 'epoch': 'yesterday-ish'}, now) == _bits_msb(3, 4)

    @pytest.mark.parametrize('shape', ['circle', 'square', 'rect'])
    def test_grid(self, shape: str) -> None:
        now = datetime.datetime(2026, 9, 23, 12, 34, 56, tzinfo=UTC)
        got = _render({'bit_rows': 4, 'shape': shape}, now, w=160, h=80)
        assert got == _bits_msb(int(now.timestamp()), 32)

    def test_invalid_bits_fall_back_to_default(self) -> None:
        now = datetime.datetime(2026, 9, 23, 12, 34, 56, tzinfo=UTC)
        assert _render({'bits': 0}, now) == _bits_msb(int(now.timestamp()), 32)

    def test_default_colors_crimson_dimred(self) -> None:
        img = Image.new('RGB', (40, 20))
        now = datetime.datetime(1970, 1, 1, 0, 0, 1, tzinfo=UTC)
        d._render_bit_clock_panel({'bits': 2}, 0, 0, 40, 20, now, ImageDraw.Draw(img))
        assert '#%02x%02x%02x' % img.getpixel((10, 10)) == d._color('DIMRED').lower()
        assert '#%02x%02x%02x' % img.getpixel((30, 10)) == d._color('CRIMSON').lower()


def _cfg(panel: dict) -> dict:
    return {
        'orientation': 'landscape',
        'rows': [{'name': 'r', 'height': 40, 'panels': [{'type': 'bit_clock', **panel}]}],
    }


class TestBitClockValidation:

    def test_all_keys_accepted(self) -> None:
        result = validate_config_dict(_cfg({
            'bits': 16, 'bit_order': 'lsb', 'bit_rows': 2, 'shape': 'rect',
            'on_color': 'crimson', 'off_color': 'dimred', 'epoch': '2026-01-01',
        }))
        assert result.ok and not result.warnings, result.warnings

    def test_yaml_date_epoch_accepted(self) -> None:
        result = validate_config_dict(_cfg({'epoch': datetime.date(2026, 1, 1)}))
        assert result.ok and not result.warnings, result.warnings

    @pytest.mark.parametrize('panel', [
        {'bits': 0}, {'bits': 'lots'}, {'bit_rows': -1}, {'epoch': 'nope'},
    ])
    def test_bad_values_warn(self, panel: dict) -> None:
        key = next(iter(panel))
        msgs = [i.message for i in validate_config_dict(_cfg(panel)).issues]
        assert any(key in m for m in msgs), msgs

    @pytest.mark.parametrize('panel', [{'bit_order': 'middle'}, {'shape': 'hexagon'}])
    def test_bad_enum_flagged(self, panel: dict) -> None:
        key = next(iter(panel))
        msgs = [i.message for i in validate_config_dict(_cfg(panel)).issues]
        assert any(key in m or repr(panel[key]) in m for m in msgs), msgs

    def test_unknown_key_warns(self) -> None:
        msgs = [i.message for i in validate_config_dict(_cfg({'color': 'red'})).warnings]
        assert any("'color'" in m for m in msgs), msgs
