"""
clockish.durations
~~~~~~~~~~~~~~~~~~
The one duration parser: ``<number>[s|m|h]`` strings and plain numbers (seconds).
Stdlib only, so ``duty_cycle.py`` (and through it the validator) and ``display.py``
share it without the validator importing hardware code.
"""

from __future__ import annotations

import re
from typing import Any

_DURATION_RE = re.compile(r'^(\d+(?:\.\d+)?)\s*([smh]?)$')
_UNIT_SECONDS = {'': 1, 's': 1, 'm': 60, 'h': 3600}


def seconds(value: Any) -> float | None:
    """Seconds for an int or ``<number>[s|m|h]`` string (no suffix = seconds); None if unparseable."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return float(value)   # range checks belong to the caller
    if not isinstance(value, str):
        return None
    m = _DURATION_RE.match(value.strip())
    if not m:
        return None
    return float(m.group(1)) * _UNIT_SECONDS[m.group(2)]
