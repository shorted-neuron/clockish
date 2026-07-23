"""Unit tests for clockish.transforms."""
import pytest

from clockish.transforms import (
    KNOWN_TRANSFORM_NAMES,
    apply_transforms,
)


class TestCaseTransforms:
    def test_upper(self):
        assert apply_transforms("hello", ["upper"]) == "HELLO"

    def test_lower(self):
        assert apply_transforms("HELLO", ["lower"]) == "hello"

    def test_capitalize(self):
        assert apply_transforms("hello world", ["capitalize"]) == "Hello world"

    def test_strip(self):
        assert apply_transforms("  hello  ", ["strip"]) == "hello"

    def test_titlecase(self):
        assert apply_transforms("hello world", ["titlecase"]) == "HelloWorld"

    def test_pascalcase_is_alias_of_titlecase(self):
        assert apply_transforms("hello world", ["pascalcase"]) == "HelloWorld"

    def test_camelcase(self):
        assert apply_transforms("hello world", ["camelcase"]) == "helloWorld"

    def test_camelcase_vs_titlecase_differ_on_first_word(self):
        title = apply_transforms("hello world", ["titlecase"])
        camel = apply_transforms("hello world", ["camelcase"])
        assert title == "HelloWorld"
        assert camel == "helloWorld"
        assert title != camel

    def test_camelcase_handles_underscores_and_dashes(self):
        assert apply_transforms("hello_world-again", ["camelcase"]) == "helloWorldAgain"


class TestRoundingTransforms:
    """string -> float -> int rounding, three distinct modes."""

    def test_round_default_rounds_to_nearest_int(self):
        # 71.8 -> 72 (the motivating example)
        assert apply_transforms("71.8", ["round"]) == "72"

    def test_int_truncates_no_rounding(self):
        # 71.8 -> 71 (truncation, contrast with 'round')
        assert apply_transforms("71.8", ["int"]) == "71"

    def test_ceil_rounds_up(self):
        assert apply_transforms("71.1", ["ceil"]) == "72"

    def test_floor_rounds_down(self):
        assert apply_transforms("71.9", ["floor"]) == "71"

    def test_round_ceil_floor_agree_on_exact_int(self):
        assert apply_transforms("71.0", ["round"]) == "71"
        assert apply_transforms("71.0", ["ceil"]) == "71"
        assert apply_transforms("71.0", ["floor"]) == "71"

    def test_round_with_decimal_places_arg(self):
        assert apply_transforms("71.849", [{"round": 1}]) == "71.8"

    def test_ceil_with_decimal_places_arg(self):
        assert apply_transforms("71.81", [{"ceil": 1}]) == "71.9"

    def test_floor_with_decimal_places_arg(self):
        assert apply_transforms("71.89", [{"floor": 1}]) == "71.8"

    def test_non_numeric_passes_through_unchanged(self):
        assert apply_transforms("n/a", ["round"]) == "n/a"
        assert apply_transforms("?", ["ceil"]) == "?"


class TestArithmeticTransforms:
    def test_multiply(self):
        assert apply_transforms("10", [{"multiply": 1.8}]) == "18"

    def test_multiply_fractional_result(self):
        assert apply_transforms("5", [{"multiply": 1.5}]) == "7.5"

    def test_add(self):
        assert apply_transforms("32", [{"add": 10}]) == "42"

    def test_abs(self):
        assert apply_transforms("-5", ["abs"]) == "5"

    def test_multiply_non_numeric_passes_through(self):
        assert apply_transforms("n/a", [{"multiply": 2}]) == "n/a"


class TestStringTransforms:
    def test_replace(self):
        assert apply_transforms("hello world", [{"replace": {"from": "world", "to": "there"}}]) == "hello there"

    def test_prefix(self):
        assert apply_transforms("72", [{"prefix": "IP: "}]) == "IP: 72"

    def test_suffix(self):
        assert apply_transforms("72", [{"suffix": "F"}]) == "72F"

    def test_replace_missing_from_key_passes_through(self):
        assert apply_transforms("hello", [{"replace": {"to": "x"}}]) == "hello"


class TestFormatTransform:
    def test_format_numeric(self):
        assert apply_transforms("71.8", [{"format": "{:.1f}F"}]) == "71.8F"

    def test_format_rounds_via_spec(self):
        assert apply_transforms("71.849", [{"format": "{:.0f}"}]) == "72"

    def test_format_non_numeric_string_spec(self):
        assert apply_transforms("hello", [{"format": "[{}]"}]) == "[hello]"

    def test_format_non_string_arg_passes_through(self):
        assert apply_transforms("71.8", [{"format": 5}]) == "71.8"


class TestChaining:
    def test_lower_then_suffix(self):
        assert apply_transforms("HELLO", ["lower", {"suffix": "!"}]) == "hello!"

    def test_round_then_suffix_string_to_float_to_int_display(self):
        # "71.8" -> round -> "72" -> suffix -> "72F"
        assert apply_transforms("71.8", ["round", {"suffix": "F"}]) == "72F"

    def test_empty_list_returns_value_unchanged(self):
        assert apply_transforms("hello", []) == "hello"

    def test_none_returns_value_unchanged(self):
        assert apply_transforms("hello", None) == "hello"


class TestSafety:
    def test_unknown_transform_name_skipped(self):
        assert apply_transforms("hello", ["not_a_real_transform"]) == "hello"

    def test_malformed_entry_skipped(self):
        assert apply_transforms("hello", [{"round": 1, "extra": 2}]) == "hello"
        assert apply_transforms("hello", [123]) == "hello"

    def test_never_raises_on_garbage_input(self):
        # Should not raise regardless of weird combinations.
        apply_transforms("", ["upper", "lower", {"round": "not-a-number"}])
        apply_transforms("abc", [{"multiply": "nope"}])


class TestRegistryCompleteness:
    @pytest.mark.parametrize("name", sorted(KNOWN_TRANSFORM_NAMES))
    def test_known_transform_names_are_applyable(self, name):
        # Just verify each registered name can be invoked without raising.
        apply_transforms("42", [name])
