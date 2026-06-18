"""tests/test_config_validator.py
=================================
Tests for the clockish config validation module.

Coverage:
 - Three canonical "good" configs (fourteen-segment, seven-segment, nixie) must
   produce zero errors AND zero warnings.
 - Deprecated key detection (time_font, label_font, date_font, colors dict,
   font: <scale-name>).
 - Required-field enforcement (orientation, rows, name, height, panel type).
 - Fact panel source validation.
 - Optional attribute validation (justify, height types, width types).
 - Spot-checks on existing configs known to have deprecated keys.
"""
from __future__ import annotations

import pytest
from pathlib import Path

from clockish.config_validator import (
    BUILTIN_FONT_NAMES,
    KNOWN_FACT_SOURCES,
    KNOWN_PANEL_TYPES,
    ValidationResult,
    validate_config_dict,
    validate_config_file,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CONFIGS_DIR = Path(__file__).parent.parent / 'configs'


def _config_path(name: str) -> str:
    return str(CONFIGS_DIR / name)


def _minimal_config(**extra) -> dict:
    """Return the simplest valid config dict, with optional overrides."""
    base = {
        'orientation': 'landscape',
        'rows': [
            {
                'name': 'r',
                'height': 40,
                'panels': [{'type': 'text', 'label': 'hi'}],
            }
        ],
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# Canonical "good" configs  --  must have zero errors AND zero warnings
# ---------------------------------------------------------------------------

GOOD_CONFIGS = [
    'fourteen-segment.yaml',
    'seven-segment.yaml',
    'nixie.yaml',
]


class TestGoodConfigs:
    """The three recently-refactored configs that are the reference baseline."""

    @pytest.mark.parametrize('config_name', GOOD_CONFIGS)
    def test_no_errors(self, config_name: str) -> None:
        result = validate_config_file(_config_path(config_name), run_yamllint=False)
        assert not result.errors, (
            f"{config_name} should have no errors, but got:\n"
            + "\n".join(str(e) for e in result.errors)
        )

    @pytest.mark.parametrize('config_name', GOOD_CONFIGS)
    def test_no_warnings(self, config_name: str) -> None:
        result = validate_config_file(_config_path(config_name), run_yamllint=False)
        assert not result.warnings, (
            f"{config_name} should have no warnings, but got:\n"
            + "\n".join(str(w) for w in result.warnings)
        )

    @pytest.mark.parametrize('config_name', GOOD_CONFIGS)
    def test_result_ok(self, config_name: str) -> None:
        result = validate_config_file(_config_path(config_name), run_yamllint=False)
        assert result.ok, f"{config_name} result.ok should be True"


# ---------------------------------------------------------------------------
# Deprecated key detection
# ---------------------------------------------------------------------------

class TestDeprecatedKeys:
    """Each deprecated key should produce at least one WARNING."""

    def test_time_font_warns(self) -> None:
        cfg = _minimal_config(rows=[{
            'name': 'r', 'height': 40,
            'panels': [{'type': 'clock', 'time_font': 'big', 'timezone': 'UTC'}],
        }])
        result = validate_config_dict(cfg)
        msgs = [i.message for i in result.warnings]
        assert any('time_font' in m for m in msgs), \
            f"Expected 'time_font' warning but got: {msgs}"

    def test_date_font_warns(self) -> None:
        cfg = _minimal_config(rows=[{
            'name': 'r', 'height': 40,
            'panels': [{'type': 'date', 'date_font': 'normal', 'timezone': 'UTC'}],
        }])
        result = validate_config_dict(cfg)
        msgs = [i.message for i in result.warnings]
        assert any('date_font' in m for m in msgs), \
            f"Expected 'date_font' warning but got: {msgs}"

    def test_label_font_warns(self) -> None:
        cfg = _minimal_config(rows=[{
            'name': 'r', 'height': 40,
            'panels': [{'type': 'clock', 'label_font': 'small', 'timezone': 'UTC'}],
        }])
        result = validate_config_dict(cfg)
        msgs = [i.message for i in result.warnings]
        assert any('label_font' in m for m in msgs), \
            f"Expected 'label_font' warning but got: {msgs}"

    def test_colors_dict_warns(self) -> None:
        cfg = _minimal_config(rows=[{
            'name': 'r', 'height': 40,
            'panels': [{'type': 'clock', 'colors': {'time': 'red', 'label': 'grey'}}],
        }])
        result = validate_config_dict(cfg)
        msgs = [i.message for i in result.warnings]
        assert any('colors' in m for m in msgs), \
            f"Expected 'colors' deprecation warning but got: {msgs}"

    def test_colors_non_dict_no_colors_warning(self) -> None:
        """colors: <any value> (not just dict) should trigger the deprecation warning."""
        cfg = _minimal_config(rows=[{
            'name': 'r', 'height': 40,
            'panels': [{'type': 'clock', 'colors': 'red'}],
        }])
        result = validate_config_dict(cfg)
        msgs = [i.message for i in result.warnings]
        # Should still warn (deprecated key, regardless of value type)
        assert any('colors' in m for m in msgs)

    # -- font: <scale-name> anti-pattern ------------------------------------

    @pytest.mark.parametrize('scale_name', sorted(BUILTIN_FONT_NAMES))
    def test_font_scale_name_warns(self, scale_name: str) -> None:
        """font: <builtin-scale-name> should warn; use font_size: instead."""
        cfg = _minimal_config(rows=[{
            'name': 'r', 'height': 40,
            'panels': [{'type': 'text', 'label': 'hi', 'font': scale_name}],
        }])
        result = validate_config_dict(cfg)
        msgs = [i.message for i in result.warnings]
        assert any('font_size' in m for m in msgs), (
            f"Expected 'font_size' hint warning for font: {scale_name!r} but got: {msgs}"
        )

    def test_font_custom_ref_no_scale_warning(self) -> None:
        """font: dseg7 (custom font reference, not a scale name) must NOT warn."""
        cfg = {
            'orientation': 'landscape',
            'fonts': {'dseg7': {'file': 'DSEG7Classic-Italic.ttf'}},
            'rows': [{
                'name': 'r', 'height': 40,
                'panels': [{'type': 'clock', 'font': 'dseg7', 'timezone': 'UTC'}],
            }],
        }
        result = validate_config_dict(cfg)
        # No warning about scale-name misuse
        scale_warn = [i for i in result.warnings if 'font_size' in i.message]
        assert not scale_warn, f"Unexpected font_size hint warnings: {scale_warn}"

    def test_font_debug_alias_no_warning(self) -> None:
        """font: debug is the special DejaVu alias; must NOT warn."""
        cfg = _minimal_config(rows=[{
            'name': 'r', 'height': 40,
            'panels': [{'type': 'debug', 'font': 'debug', 'color': 'grey'}],
        }])
        result = validate_config_dict(cfg)
        scale_warn = [i for i in result.warnings if 'font_size' in i.message]
        assert not scale_warn, f"Unexpected font_size hint warnings: {scale_warn}"


# ---------------------------------------------------------------------------
# Required-field errors (structural)
# ---------------------------------------------------------------------------

class TestRequiredFields:
    """Missing required fields must produce at least one ERROR."""

    def test_missing_orientation_errors(self) -> None:
        cfg = {'rows': [{'name': 'r', 'height': 40, 'panels': [{'type': 'text'}]}]}
        result = validate_config_dict(cfg)
        assert result.has_errors, "Missing 'orientation' should be an ERROR"

    def test_missing_rows_errors(self) -> None:
        cfg = {'orientation': 'landscape'}
        result = validate_config_dict(cfg)
        assert result.has_errors, "Missing 'rows' should be an ERROR"

    def test_empty_rows_list_errors(self) -> None:
        cfg = {'orientation': 'landscape', 'rows': []}
        result = validate_config_dict(cfg)
        assert result.has_errors, "Empty 'rows' list should be an ERROR"

    def test_rows_not_list_errors(self) -> None:
        cfg = {'orientation': 'landscape', 'rows': 'not-a-list'}
        result = validate_config_dict(cfg)
        assert result.has_errors

    def test_row_missing_name_errors(self) -> None:
        cfg = {'orientation': 'landscape',
               'rows': [{'height': 40, 'panels': [{'type': 'text'}]}]}
        result = validate_config_dict(cfg)
        assert result.has_errors, "Row missing 'name' should be an ERROR"

    def test_row_missing_height_errors(self) -> None:
        cfg = {'orientation': 'landscape',
               'rows': [{'name': 'r', 'panels': [{'type': 'text'}]}]}
        result = validate_config_dict(cfg)
        assert result.has_errors, "Row missing 'height' should be an ERROR"

    def test_panel_missing_type_errors(self) -> None:
        cfg = _minimal_config(rows=[{
            'name': 'r', 'height': 40,
            'panels': [{'color': 'red'}],  # no 'type'
        }])
        result = validate_config_dict(cfg)
        assert result.has_errors, "Panel missing 'type' should be an ERROR"

    def test_fact_panel_missing_source_errors(self) -> None:
        cfg = _minimal_config(rows=[{
            'name': 'r', 'height': 40,
            'panels': [{'type': 'fact', 'color': 'white'}],  # no 'source'
        }])
        result = validate_config_dict(cfg)
        assert result.has_errors, "fact panel missing 'source' should be an ERROR"

    def test_not_a_dict_errors(self) -> None:
        result = validate_config_dict("this is a string, not a dict")
        assert result.has_errors

    def test_orientation_invalid_value_errors(self) -> None:
        cfg = _minimal_config(orientation='diagonal')  # not a valid enum value
        result = validate_config_dict(cfg)
        assert result.has_errors, "Invalid orientation value should be an ERROR"


# ---------------------------------------------------------------------------
# Optional attribute validation
# ---------------------------------------------------------------------------

class TestOptionalAttributes:

    # -- justify ------------------------------------------------------------

    @pytest.mark.parametrize('justify', ['left', 'right', 'center'])
    def test_valid_justify_no_error(self, justify: str) -> None:
        cfg = _minimal_config(rows=[{
            'name': 'r', 'height': 40,
            'panels': [{'type': 'text', 'label': 'x', 'justify': justify}],
        }])
        result = validate_config_dict(cfg)
        assert not result.has_errors, \
            f"justify: {justify!r} should be valid but got errors: {result.errors}"

    def test_invalid_justify_flagged(self) -> None:
        cfg = _minimal_config(rows=[{
            'name': 'r', 'height': 40,
            'panels': [{'type': 'text', 'label': 'x', 'justify': 'middle'}],
        }])
        result = validate_config_dict(cfg)
        # Both jsonschema (ERROR) and semantic walker (WARNING) may flag this
        assert result.has_errors or result.warnings, \
            "justify: 'middle' should produce an error or warning"

    # -- height types -------------------------------------------------------

    def test_height_integer_valid(self) -> None:
        for h in [1, 5, 40, 320]:
            cfg = _minimal_config(rows=[{
                'name': 'r', 'height': h,
                'panels': [{'type': 'text'}],
            }])
            assert not validate_config_dict(cfg).has_errors, \
                f"height: {h} (integer) should be valid"

    def test_height_percentage_string_valid(self) -> None:
        for h in ['1%', '15%', '55%', '100%', '3.5%']:
            cfg = _minimal_config(rows=[{
                'name': 'r', 'height': h,
                'panels': [{'type': 'text'}],
            }])
            assert not validate_config_dict(cfg).has_errors, \
                f"height: '{h}' should be valid"

    def test_height_float_fraction_valid(self) -> None:
        for h in [0.1, 0.25, 0.5, 0.69]:
            cfg = _minimal_config(rows=[{
                'name': 'r', 'height': h,
                'panels': [{'type': 'text'}],
            }])
            assert not validate_config_dict(cfg).has_errors, \
                f"height: {h} (float fraction) should be valid"

    def test_height_zero_errors(self) -> None:
        cfg = _minimal_config(rows=[{
            'name': 'r', 'height': 0,
            'panels': [{'type': 'text'}],
        }])
        result = validate_config_dict(cfg)
        assert result.has_errors, "height: 0 should be an error"

    def test_height_bad_string_errors(self) -> None:
        for h in ['big', 'auto', '55px', '-5%']:
            cfg = _minimal_config(rows=[{
                'name': 'r', 'height': h,
                'panels': [{'type': 'text'}],
            }])
            result = validate_config_dict(cfg)
            assert result.has_errors, f"height: '{h}' should be an error"

    # -- width types --------------------------------------------------------

    def test_width_integer_valid(self) -> None:
        cfg = _minimal_config(rows=[{
            'name': 'r', 'height': 40,
            'panels': [{'type': 'text', 'width': 120}],
        }])
        assert not validate_config_dict(cfg).has_errors

    def test_width_percentage_valid(self) -> None:
        cfg = _minimal_config(rows=[{
            'name': 'r', 'height': 40,
            'panels': [{'type': 'text', 'width': '44%'}],
        }])
        assert not validate_config_dict(cfg).has_errors

    def test_width_float_valid(self) -> None:
        cfg = _minimal_config(rows=[{
            'name': 'r', 'height': 40,
            'panels': [{'type': 'text', 'width': 0.33}],
        }])
        assert not validate_config_dict(cfg).has_errors


# ---------------------------------------------------------------------------
# Unknown key detection
# ---------------------------------------------------------------------------

class TestUnknownKeys:

    def test_unknown_top_level_key_warns(self) -> None:
        cfg = _minimal_config(theme='dark')  # unknown top-level key
        result = validate_config_dict(cfg)
        msgs = [i.message for i in result.warnings]
        assert any('theme' in m for m in msgs)

    def test_unknown_row_key_warns(self) -> None:
        cfg = _minimal_config(rows=[{
            'name': 'r', 'height': 40, 'columns': 3,  # 'columns' is unknown
            'panels': [{'type': 'text'}],
        }])
        result = validate_config_dict(cfg)
        msgs = [i.message for i in result.warnings]
        assert any('columns' in m for m in msgs)

    def test_unknown_panel_key_warns(self) -> None:
        cfg = _minimal_config(rows=[{
            'name': 'r', 'height': 40,
            'panels': [{'type': 'text', 'label': 'hi', 'blink': True}],
        }])
        result = validate_config_dict(cfg)
        msgs = [i.message for i in result.warnings]
        assert any('blink' in m for m in msgs)

    def test_unknown_panel_type_warns(self) -> None:
        cfg = _minimal_config(rows=[{
            'name': 'r', 'height': 40,
            'panels': [{'type': 'sparkline', 'source': 'cpu'}],
        }])
        result = validate_config_dict(cfg)
        msgs = [i.message for i in result.warnings]
        assert any('sparkline' in m for m in msgs)


# ---------------------------------------------------------------------------
# Fact source validation
# ---------------------------------------------------------------------------

class TestFactSource:

    @pytest.mark.parametrize('source', sorted(KNOWN_FACT_SOURCES))
    def test_known_source_no_warning(self, source: str) -> None:
        cfg = _minimal_config(rows=[{
            'name': 'r', 'height': 40,
            'panels': [{'type': 'fact', 'source': source}],
        }])
        result = validate_config_dict(cfg)
        assert not result.has_errors, \
            f"fact source '{source}' should not produce errors but got: {result.errors}"
        # No unknown-source warning
        src_warns = [i for i in result.warnings if 'unrecognised fact source' in i.message]
        assert not src_warns, f"Known source '{source}' triggered unrecognised warning: {src_warns}"

    def test_unknown_source_warns(self) -> None:
        cfg = _minimal_config(rows=[{
            'name': 'r', 'height': 40,
            'panels': [{'type': 'fact', 'source': 'battery_level'}],
        }])
        result = validate_config_dict(cfg)
        msgs = [i.message for i in result.warnings]
        assert any('battery_level' in m for m in msgs)

    def test_missing_source_errors(self) -> None:
        cfg = _minimal_config(rows=[{
            'name': 'r', 'height': 40,
            'panels': [{'type': 'fact'}],
        }])
        result = validate_config_dict(cfg)
        assert result.has_errors


# ---------------------------------------------------------------------------
# Row with no panels (blank spacer rows)
# ---------------------------------------------------------------------------

class TestBlankRows:

    def test_row_without_panels_warns(self) -> None:
        """A row with no 'panels' key is valid (renders blank) but should warn."""
        cfg = {
            'orientation': 'landscape',
            'rows': [
                {'name': 'blank-1', 'height': 6, 'background': 'black'},
                {'name': 'clock', 'height': 200, 'panels': [{'type': 'clock'}]},
            ],
        }
        result = validate_config_dict(cfg)
        assert not result.has_errors, f"No-panels row should not ERROR: {result.errors}"
        msgs = [i.message for i in result.warnings]
        assert any('blank-1' in m for m in msgs), \
            "Expected warning about row with no panels"


# ---------------------------------------------------------------------------
# fonts: section validation
# ---------------------------------------------------------------------------

class TestFontsSection:

    def test_valid_font_entry(self) -> None:
        cfg = {
            'orientation': 'landscape',
            'fonts': {'dseg7': {'file': 'DSEG7Classic-Italic.ttf'}},
            'rows': [{'name': 'r', 'height': 40,
                      'panels': [{'type': 'clock', 'font': 'dseg7'}]}],
        }
        result = validate_config_dict(cfg)
        assert not result.has_errors

    def test_font_entry_with_size(self) -> None:
        cfg = {
            'orientation': 'landscape',
            'fonts': {'my_font': {'file': 'Custom.ttf', 'size': '10%'}},
            'rows': [{'name': 'r', 'height': 40,
                      'panels': [{'type': 'text', 'font': 'my_font'}]}],
        }
        result = validate_config_dict(cfg)
        assert not result.has_errors

    def test_font_entry_missing_file_errors(self) -> None:
        cfg = {
            'orientation': 'landscape',
            'fonts': {'bad_font': {'size': 20}},  # no 'file' key
            'rows': [{'name': 'r', 'height': 40,
                      'panels': [{'type': 'text'}]}],
        }
        result = validate_config_dict(cfg)
        assert result.has_errors, "Font entry without 'file' should be an ERROR"


# ---------------------------------------------------------------------------
# Spot-checks on deprecated patterns
# ---------------------------------------------------------------------------
# inline dicts test validator *behaviour* regardless of real config files
class TestExistingConfigsSpotCheck:
    """Verify that known deprecated patterns produce the expected findings."""

    def test_time_font_and_colors_dict_detected(self) -> None:
        """Simulates the old clockish.yaml / debug.yaml pattern."""
        cfg = {
            'orientation': 'landscape',
            'rows': [{
                'name': 'clock', 'height': 56,
                'panels': [{
                    'type': 'clock',
                    'timezone': 'local',
                    'time_format': '24hs',
                    'colors': {'time': 'white', 'label': 'grey'},
                    'time_font': 'big',
                }],
            }],
        }
        result = validate_config_dict(cfg)
        msgs = [i.message for i in result.warnings]
        assert any('time_font' in m for m in msgs)
        assert any('colors' in m for m in msgs)

    def test_label_font_detected(self) -> None:
        """Simulates the old debug.yaml / landscape-demo.yaml pattern."""
        cfg = {
            'orientation': 'landscape',
            'rows': [{
                'name': 'clock', 'height': 64,
                'panels': [{
                    'type': 'clock',
                    'timezone': 'America/Denver',
                    'label': 'DEN',
                    'time_format': '12h',
                    'colors': {'time': 'ff0000', 'label': 'darkred'},
                    'time_font': 'big',
                    'label_font': 'normal',
                }],
            }],
        }
        result = validate_config_dict(cfg)
        msgs = [i.message for i in result.warnings]
        assert any('label_font' in m for m in msgs)

    def test_missing_orientation_errors(self) -> None:
        """Simulates the old config pattern (no orientation key)."""
        cfg = {
            'rows': [{
                'name': 'clock', 'height': 170,
                'panels': [{'type': 'clock', 'timezone': 'US/Mountain'}],
            }],
        }
        result = validate_config_dict(cfg)
        assert result.has_errors, "Missing orientation should be an ERROR"

    def test_font_scale_name_as_font_ref_detected(self) -> None:
        """Simulates the old pattern of font: small (should be font_size: small)."""
        cfg = {
            'orientation': 'landscape',
            'rows': [{
                'name': 'info', 'height': 34,
                'panels': [{'type': 'fact', 'source': 'hostname', 'font': 'small'}],
            }],
        }
        result = validate_config_dict(cfg)
        msgs = [i.message for i in result.warnings]
        assert any('font_size' in m for m in msgs)


# ---------------------------------------------------------------------------
# ValidationResult API
# ---------------------------------------------------------------------------

class TestValidationResultAPI:

    def test_ok_when_no_issues(self) -> None:
        result = ValidationResult(path='test')
        assert result.ok
        assert not result.has_errors
        assert result.errors == []
        assert result.warnings == []

    def test_has_errors_when_error_added(self) -> None:
        result = ValidationResult(path='test')
        result.add_error('root', 'something is wrong')
        assert result.has_errors
        assert not result.ok

    def test_warning_does_not_set_has_errors(self) -> None:
        result = ValidationResult(path='test')
        result.add_warning('root', 'something is suspicious')
        assert not result.has_errors
        assert result.ok
        assert len(result.warnings) == 1

    def test_print_summary_ok(self, capsys) -> None:
        result = ValidationResult(path='my.yaml')
        result.print_summary(file=__import__('sys').stdout)
        out = capsys.readouterr().out
        assert 'OK' in out

    def test_print_summary_with_issues(self, capsys) -> None:
        result = ValidationResult(path='my.yaml')
        result.add_error('root', 'missing orientation')
        result.add_warning('rows[0]', 'unknown key')
        result.print_summary(file=__import__('sys').stdout)
        out = capsys.readouterr().out
        assert 'ERROR' in out
        assert 'WARNING' in out
        assert '1 error' in out
        assert '1 warning' in out
