"""
clockish.duty_cycle
~~~~~~~~~~~~~~~~~~~
Pure schedule logic for ``display.duty_cycle:`` -- steps an OLED through brightness levels,
inverted frames and blanking on the wall clock, to slow burn-in.  Stdlib only, no I/O, so
``config_validator.py`` imports it (one-way, like ``backlight.py``).

.. code-block:: yaml

    display:
      duty_cycle:
        steps:                        # period = sum of 'for'; must divide 86400
          - {for: 5s,  level: full}   # :00-:04
          - {for: 25s, level: half}   # :05-:29
          - {for: 30s, level: dim}    # :30-:59
        offset: 0s                    # optional: where step 1 starts within the period
        levels:                       # optional: tune a level for this module
          dim: {contrast: 1, vcomh: low}

    # shorthand: lit at full for show_for from show_at, blank the rest
    display:
      duty_cycle: {period: 60s, show_at: 55s, show_for: 10s}

Step ``mode`` is ``normal`` (default), ``invert`` or ``blank``; ``level`` names an entry of
``levels`` (default ``full``).  The step at tick ``t`` (integer epoch seconds) is the one
covering ``(t - offset) mod period``.  A duration is whole seconds (int) or ``<number>[s|m|h]``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from clockish import durations

#: Shorthand keys: one lit window per period, blank the rest.
SHORTHAND_KEYS = ('period', 'show_at', 'show_for')
#: Keys accepted under ``display.duty_cycle:``.
DUTY_CYCLE_KEYS = SHORTHAND_KEYS + ('steps', 'offset', 'levels')
STEP_KEYS = ('for', 'mode', 'level')
MODES = ('normal', 'invert', 'blank')
LEVEL_KEYS = ('contrast', 'vcomh', 'precharge')
VCOMH_VALUES = ('low', 'mid', 'high')
PRECHARGE_VALUES = ('normal', 'short')

#: Built-in levels.  'full' equals the SSD1306 init values.  Lowest VCOMH alone barely shows next to
#: full on a running clock, so 'half' also drops contrast.  Contrast 1 + lowest VCOMH is the dimmest
#: setting visible on every module tested (a worn module may need more).  Contrast 0 and short
#: precharge go fully dark on some modules, so neither is a default.
DEFAULT_LEVELS: dict[str, dict[str, Any]] = {
    'full': {'contrast': 255, 'vcomh': 'high', 'precharge': 'normal'},
    'half': {'contrast': 32, 'vcomh': 'low', 'precharge': 'normal'},
    'dim': {'contrast': 1, 'vcomh': 'low', 'precharge': 'normal'},
}

SECONDS_PER_DAY = 86400


def parse_duration(value: Any) -> int | None:
    """Whole seconds via :func:`clockish.durations.seconds`; None if unparseable or fractional."""
    secs = durations.seconds(value)
    if secs is None or secs != int(secs):
        return None
    return int(secs)


@dataclass(frozen=True)
class Level:
    contrast: int
    vcomh: str
    precharge: str


@dataclass(frozen=True)
class Step:
    start: int          # seconds into the period
    length: int
    mode: str
    level: str


@dataclass(frozen=True)
class State:
    """What the panel should show at one tick.  ``level`` None = leave the panel's level alone."""
    mode: str
    level: Level | None
    index: int = -1     # step number, -1 without a schedule


#: No schedule: normal frames, levels untouched.
ALWAYS_ON = State('normal', None)


@dataclass(frozen=True)
class DutyCycle:
    period: int
    offset: int
    steps: tuple[Step, ...]
    levels: dict[str, Level] = field(hash=False)

    @property
    def always_visible(self) -> bool:
        return all(s.mode != 'blank' for s in self.steps)


def _level_problems(name: Any, spec: Any) -> list[str]:
    if not isinstance(spec, dict):
        return [f"duty_cycle.levels.{name} must be a mapping of {', '.join(LEVEL_KEYS)}"]
    out = [f"unknown key '{k}' on duty_cycle.levels.{name} (expected {', '.join(LEVEL_KEYS)})"
           for k in spec if k not in LEVEL_KEYS]
    if 'contrast' in spec:
        c = spec['contrast']
        if isinstance(c, bool) or not isinstance(c, int) or not 0 <= c <= 255:
            out.append(f"duty_cycle.levels.{name}.contrast must be an integer 0-255, got {c!r}")
    if 'vcomh' in spec and spec['vcomh'] not in VCOMH_VALUES:
        out.append(f"duty_cycle.levels.{name}.vcomh must be one of {', '.join(VCOMH_VALUES)}, "
                   f"got {spec['vcomh']!r}")
    if 'precharge' in spec and spec['precharge'] not in PRECHARGE_VALUES:
        out.append(f"duty_cycle.levels.{name}.precharge must be one of {', '.join(PRECHARGE_VALUES)}, "
                   f"got {spec['precharge']!r}")
    return out


def _shorthand_problems(cfg: dict) -> list[str]:
    out: list[str] = []
    vals: dict[str, int] = {}
    for key in SHORTHAND_KEYS:
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
    if 'offset' in cfg:
        out.append("'offset' does not combine with period/show_at/show_for (show_at is the offset)")
    return out


def _steps_problems(cfg: dict, level_names: set[str]) -> list[str]:
    steps = cfg['steps']
    if not isinstance(steps, list) or not steps:
        return ["duty_cycle.steps must be a non-empty list"]
    out: list[str] = []
    total = 0
    for i, st in enumerate(steps, 1):
        where = f"duty_cycle.steps[{i}]"
        if not isinstance(st, dict):
            out.append(f"{where} must be a mapping with 'for' (and optional mode, level)")
            continue
        out += [f"unknown key '{k}' on {where} (expected {', '.join(STEP_KEYS)})" for k in st if k not in STEP_KEYS]
        secs = parse_duration(st.get('for'))
        if 'for' not in st:
            out.append(f"{where} missing required 'for' key")
        elif secs is None or secs <= 0:
            out.append(f"{where}: 'for: {st['for']!r}' must be whole seconds > 0 or <number>[s|m|h]")
        else:
            total += secs
        if st.get('mode', 'normal') not in MODES:
            out.append(f"{where}: mode must be one of {', '.join(MODES)}, got {st['mode']!r}")
        level = st.get('level', 'full')
        if not isinstance(level, str) or level not in level_names:
            out.append(f"{where}: unknown level {level!r} (known: {', '.join(sorted(map(str, level_names)))})")
    if out:
        return out
    if SECONDS_PER_DAY % total:
        out.append(f"duty_cycle steps add up to {total}s, which must divide 86400s evenly, "
                   "so each step lands on the same wall-clock seconds every day")
    if 'offset' in cfg:
        off = parse_duration(cfg['offset'])
        if off is None:
            out.append(f"'offset: {cfg['offset']!r}' must be whole seconds or <number>[s|m|h]")
        elif not 0 <= off < total:
            out.append(f"'offset: {cfg['offset']!r}' must be at least 0 and less than the period ({total}s)")
    return out


def problems(cfg: Any) -> list[str]:
    """Everything wrong with a ``duty_cycle:`` value, as messages; empty = valid."""
    if not isinstance(cfg, dict):
        return ["'duty_cycle' must be a mapping"]
    out = [f"unknown key '{k}' on duty_cycle (expected {', '.join(DUTY_CYCLE_KEYS)})"
           for k in cfg if k not in DUTY_CYCLE_KEYS]
    levels = cfg.get('levels', {})
    if not isinstance(levels, dict):
        out.append("duty_cycle.levels must be a mapping of level name -> {contrast, vcomh, precharge}")
        levels = {}
    for name, spec in levels.items():
        out += _level_problems(name, spec)
    has_short = any(k in cfg for k in SHORTHAND_KEYS)
    if has_short and 'steps' in cfg:
        out.append("use either period/show_at/show_for or steps, not both")
    elif 'steps' in cfg:
        out += _steps_problems(cfg, set(DEFAULT_LEVELS) | set(levels))
    else:
        out += _shorthand_problems(cfg)
    return out


def _levels(cfg: dict) -> dict[str, Level]:
    merged = {name: dict(spec) for name, spec in DEFAULT_LEVELS.items()}
    for name, spec in (cfg.get('levels') or {}).items():
        merged[name] = {**merged.get(name, DEFAULT_LEVELS['full']), **spec}
    return {name: Level(**spec) for name, spec in merged.items()}


def parse(cfg: Any) -> DutyCycle | None:
    """Normalise a ``duty_cycle:`` mapping.  None for absent OR invalid (invalid = always on)."""
    if cfg is None or problems(cfg):
        return None
    levels = _levels(cfg)
    if 'steps' in cfg:
        steps, start = [], 0
        for st in cfg['steps']:
            length = parse_duration(st['for'])
            assert length is not None   # problems() checked it
            steps.append(Step(start, length, st.get('mode', 'normal'), st.get('level', 'full')))
            start += length
        return DutyCycle(start, parse_duration(cfg.get('offset', 0)) or 0, tuple(steps), levels)
    period, show_at, show_for = (parse_duration(cfg[k]) for k in SHORTHAND_KEYS)
    assert period is not None and show_at is not None and show_for is not None
    if show_for >= period:
        return DutyCycle(period, show_at, (Step(0, period, 'normal', 'full'),), levels)
    return DutyCycle(period, show_at, (Step(0, show_for, 'normal', 'full'),
                                       Step(show_for, period - show_for, 'blank', 'full')), levels)


def state_at(duty: DutyCycle | dict | None, tick: float) -> State:
    """What the panel shows at integer epoch second ``tick``.  No/invalid schedule = ALWAYS_ON."""
    if isinstance(duty, dict):
        duty = parse(duty)
    if duty is None:
        return ALWAYS_ON
    pos = (int(tick) - duty.offset) % duty.period
    for i, st in enumerate(duty.steps):
        if pos < st.start + st.length:
            return State(st.mode, duty.levels[st.level], i)
    raise AssertionError("steps cover the whole period")   # pragma: no cover


def is_visible(duty: DutyCycle | dict | None, tick: float) -> bool:
    """Whether the panel shows anything at ``tick``.  No/invalid schedule = always."""
    return state_at(duty, tick).mode != 'blank'
