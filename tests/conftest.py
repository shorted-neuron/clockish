"""
conftest.py  --  pytest fixtures available to all tests.

Add shared fixtures here (e.g. mock GPIO, temporary config files, etc.)
"""
import pytest


# Example: a fixture that stubs out Raspberry Pi hardware so tests run on Windows
@pytest.fixture(autouse=False)
def mock_raspberry_pi(monkeypatch):
    """
    Make platform_utils believe it's running on a Pi.
    Use in tests that exercise Pi-specific code paths:

        def test_something(mock_raspberry_pi):
            ...
    """
    import clockish.platform_utils as pu
    monkeypatch.setattr(pu, "is_raspberry_pi", lambda: True)
