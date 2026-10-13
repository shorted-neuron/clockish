"""
clockish.duty_cycle
~~~~~~~~~~~~~~~~~~~
Pure schedule logic for ``display.duty_cycle:`` -- blank an OLED except for a short
window each period, to stop burn-in.  Stdlib only, no I/O, so ``config_validator.py``
imports it (one-way, like ``backlight.py``).

.. code-block:: yaml

    display:
      duty_cycle:
        period:   60s    # wall-clock aligned; must divide 86400
        show_at:  55s    # offset into the period where the panel turns on
        show_for: 10s    # visible :55 through :04; the window wraps the period boundary

Visible at tick ``t`` (integer epoch seconds) iff ``((t - show_at) mod period) < show_for``.
A duration is a whole number of seconds (int) or ``<number>[s|m|h]``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from clockish import durations

#: Keys accepted under ``display.duty_cycle:``.
DUTY_CYCLE_KEYS = ('period', 'show_at', 'show_for')

SECONDS_PER_DAY = 86400


def parse_duration(value: Any) -> int | None:
    """Whole seconds via :func:`clockish.durations.seconds`; None if unparseable or fractional."""
    secs = durations.seconds(value)
    if secs is None or secs != int(secs):
        return None
    return int(secs)


@dataclass(frozen=True)
class DutyCycle:
    period: int
    show_at: int
    show_for: int

    @property
    def always_visible(self) -> bool:
        return self.show_for >= self.period


def problems(cfg: Any) -> list[str]:
    """Everything wrong with a ``duty_cycle:`` value, as messages; empty = valid."""
    if not isinstance(cfg, dict):
        return ["'duty_cycle' must be a mapping"]
    out = [f"unknown key '{k}' on duty_cycle (expected {', '.join(DUTY_CYCLE_KEYS)})"
           for k in cfg if k not in DUTY_CYCLE_KEYS]
    vals: dict[str, int] = {}
    for key in DUTY_CYCLE_KEYS:
        if key not in cfg:
            out.append(f"duty_cycle missing required '{key}' key")
            continue
        secs = parse_duration(cfg[key])
        if secs is None:
            out.append(f"'{key}: {cfg[key]!r}' must be whole seconds or <number>[s|m|h]")
        else:
            vals[key] = secs
    period = vals.get('period')
    if period is not None:
        if period <= 0:
            out.append(f"'period: {cfg['period']!r}' must be greater than 0")
        elif SECONDS_PER_DAY % period:
            out.append(f"'period: {cfg['period']!r}' ({period}s) must divide 86400s evenly, "
                       "so the window lands on the same wall-clock seconds every day")
        elif 'show_at' in vals and not 0 <= vals['show_at'] < period:
            out.append(f"'show_at: {cfg['show_at']!r}' must be at least 0 and less than period")
    if 'show_for' in vals and vals['show_for'] <= 0:
        out.append(f"'show_for: {cfg['show_for']!r}' must be greater than 0 (the panel would never show)")
    return out


def parse(cfg: Any) -> DutyCycle | None:
    """Normalise a ``duty_cycle:`` mapping.  None for absent OR invalid (invalid = always visible)."""
    if cfg is None or problems(cfg):
        return None
    return DutyCycle(parse_duration(cfg['period']), parse_duration(cfg['show_at']),  # type: ignore[arg-type]
                     parse_duration(cfg['show_for']))  # type: ignore[arg-type]


def is_visible(duty: DutyCycle | dict | None, tick: float) -> bool:
    """Whether the panel shows at integer epoch second ``tick``.  No/invalid schedule = always."""
    if isinstance(duty, dict):
        duty = parse(duty)
    if duty is None or duty.always_visible:
        return True
    return (int(tick) - duty.show_at) % duty.period < duty.show_for
