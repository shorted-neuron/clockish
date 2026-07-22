"""tests/test_json_path_extraction.py

Unit tests for clockish.display._extract_value_by_json_path().

Covers three lookup modes:
1. Root-level flat key (e.g. api.ipify.org: {"ip": "1.2.3.4"})
2. Nested-wrapper key, used when the root key is absent (e.g. a sensor API
   that wraps its payload under an opaque/variable top-level id)
3. Dot-notation path for explicitly nested structures
"""
import json

from clockish.display import _extract_value_by_json_path


class TestRootLevelKey:
    """The bug this module guards against: flat top-level JSON, e.g. ipify."""

    def test_flat_root_key_extracted_directly(self):
        response = json.dumps({"ip": "75.71.133.104"})
        value, missing = _extract_value_by_json_path(response, "ip")
        assert value == "75.71.133.104"
        assert missing is False

    def test_flat_root_key_numeric_value(self):
        response = json.dumps({"public_repos": 42})
        value, missing = _extract_value_by_json_path(response, "public_repos")
        assert value == "42"
        assert missing is False

    def test_root_key_present_but_null_is_missing(self):
        response = json.dumps({"ip": None})
        value, missing = _extract_value_by_json_path(response, "ip")
        assert value is None
        assert missing is True


class TestNestedWrapperKey:
    """Fallback mode: root key absent, so scan one level deep (sensor-master.lan style)."""

    def test_nested_wrapper_key_found_when_root_key_absent(self):
        response = json.dumps({"286114a10300004b": {"tempF": 71.8, "humidity": 45}})
        value, missing = _extract_value_by_json_path(response, "tempF")
        assert value == "71.8"
        assert missing is False

    def test_nested_wrapper_key_missing_in_nested_object(self):
        response = json.dumps({"286114a10300004b": {"humidity": 45}})
        value, missing = _extract_value_by_json_path(response, "tempF")
        assert value is None
        assert missing is True

    def test_no_dict_values_at_all_is_missing(self):
        response = json.dumps({"a": 1, "b": 2})
        value, missing = _extract_value_by_json_path(response, "tempF")
        assert value is None
        assert missing is True

    def test_root_key_takes_priority_over_nested_scan(self):
        """If the root itself has the key, use it -- don't dig into nested dicts."""
        response = json.dumps({"tempF": 60.0, "device": {"tempF": 99.9}})
        value, missing = _extract_value_by_json_path(response, "tempF")
        assert value == "60.0"
        assert missing is False


class TestDotNotation:
    def test_dot_path_navigates_nested_dicts(self):
        response = json.dumps({"data": {"temp": 21.5}})
        value, missing = _extract_value_by_json_path(response, "data.temp")
        assert value == "21.5"
        assert missing is False

    def test_dot_path_missing_intermediate_key(self):
        response = json.dumps({"data": {}})
        value, missing = _extract_value_by_json_path(response, "data.temp")
        assert value is None
        assert missing is True

    def test_dot_path_through_non_dict_is_missing(self):
        response = json.dumps({"data": "not-a-dict"})
        value, missing = _extract_value_by_json_path(response, "data.temp")
        assert value is None
        assert missing is True


class TestErrorHandling:
    def test_non_dict_root_returns_generic_error_not_missing(self):
        response = json.dumps([1, 2, 3])
        value, missing = _extract_value_by_json_path(response, "ip")
        assert value is None
        assert missing is False  # not a "missing key" -- wrong shape entirely

    def test_invalid_json_returns_generic_error(self):
        value, missing = _extract_value_by_json_path("not json at all {{{", "ip")
        assert value is None
        assert missing is False

    def test_json_path_is_stripped(self):
        response = json.dumps({"ip": "1.2.3.4"})
        value, missing = _extract_value_by_json_path(response, "  ip  ")
        assert value == "1.2.3.4"
        assert missing is False

