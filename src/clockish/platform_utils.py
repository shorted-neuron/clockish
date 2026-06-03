"""
platform.py — runtime helpers for detecting the target platform.

Because we develop on Windows but deploy on Raspberry Pi, code that touches
GPIO / I2C / SPI must always guard itself with these helpers rather than
importing RPi.GPIO or gpiozero unconditionally.

Usage:
    from clockish.platform import is_raspberry_pi, require_pi

    if is_raspberry_pi():
        import RPi.GPIO as GPIO
        ...
    else:
        # stub / mock path for Windows dev
        ...
"""

import platform
import sys


def is_raspberry_pi() -> bool:
    """Return True when running on a Raspberry Pi (any OS)."""
    machine = platform.machine().lower()
    # armv6l  → Pi 1, Pi Zero (1st gen)
    # armv7l  → Pi 2, Pi 3, Pi 4 (32-bit OS)
    # aarch64 → Pi 3, Pi 4, Pi 5 (64-bit OS), Ubuntu on Pi
    return machine in ("armv6l", "armv7l", "aarch64") or _check_cpuinfo()


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def is_windows() -> bool:
    return sys.platform == "win32"


def require_pi(feature: str = "this feature") -> None:
    """Raise a clear error when Pi-only code is called on another platform."""
    if not is_raspberry_pi():
        raise RuntimeError(
            f"{feature} requires a Raspberry Pi. "
            f"Current platform: {platform.machine()} / {sys.platform}"
        )


# ── internal helpers ──────────────────────────────────────────────────────────

def _check_cpuinfo() -> bool:
    """Fallback: sniff /proc/cpuinfo for the Raspberry Pi model string."""
    try:
        with open("/proc/cpuinfo", "r") as f:
            return "raspberry pi" in f.read().lower()
    except OSError:
        return False

