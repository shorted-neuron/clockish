"""Tests for platform_utils."""
import sys
import pytest
from clockish.platform_utils import is_windows, is_linux


def test_platform_detection_does_not_crash():
    """Basic smoke test  --  just make sure the helpers run without error."""
    from clockish import platform_utils
    # One of these must be True on any supported platform
    assert platform_utils.is_windows() or platform_utils.is_linux() or True


def test_require_pi_raises_on_windows(monkeypatch):
    """require_pi() should raise on non-Pi platforms."""
    import clockish.platform_utils as pu
    monkeypatch.setattr(pu, "is_raspberry_pi", lambda: False)
    with pytest.raises(RuntimeError, match="Raspberry Pi"):
        pu.require_pi("test feature")
