"""clockish.transforms
====================
Generic value-transform pipeline for panel text.

Any text-producing panel (``clock``, ``date``, ``fact``, ``text``, ``url-fact``)
may carry a ``transform:`` list in its config.  Each entry is applied in order
to the panel's core string value *before* any label prefix/suffix is added.

YAML entry forms
-----------------
Simple (no argument)::

    transform: [upper]

Parameterised (single-key mapping)::

    transform:
      - {round: 1}
      - {suffix: "F"}

Chained (applied left-to-right)::

    transform: [lower, {suffix: "!"}]

Design notes
------------
- Every transform takes a ``str`` and returns a ``str`` -- panels only ever
  deal in strings (already-formatted display text).
- Numeric transforms (``round``/``ceil``/``floor``/``int``/``abs``/``multiply``/
  ``add``) attempt ``float(value)``; on failure the value passes through
  unchanged (a debug line is printed) rather than crashing the render loop --
  remote/system values are not always numeric (e.g. fallback text like 'n/a').
- Unknown transform names or malformed args are caught by
  ``config_validator.py`` at startup; ``apply_transforms()`` itself is
  defensive and never raises.
"""
from __future__ import annotations

import math
import re
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Individual transform implementations
# ---------------------------------------------------------------------------


def _t_upper(value: str, _arg: Any) -> str:
    return value.upper()


def _t_lower(value: str, _arg: Any) -> str:
    return value.lower()


def _t_title(value: str, _arg: Any) -> str:
    return value.title()


def _t_capitalize(value: str, _arg: Any) -> str:
    return value.capitalize()


def _t_strip(value: str, _arg: Any) -> str:
    return value.strip()


_WORD_SPLIT_RE = re.compile(r'[\s_\-]+')


def _split_words(value: str) -> list[str]:
    return [w for w in _WORD_SPLIT_RE.split(value.strip()) if w]


def _t_titlecase(value: str, _arg: Any) -> str:
    """PascalCase / TitleCase: 'hello world' -> 'HelloWorld'."""
    return ''.join(w.capitalize() for w in _split_words(value))


# Alias: 'pascalcase' is the same transform as 'titlecase'.
_t_pascalcase = _t_titlecase


def _t_camelcase(value: str, _arg: Any) -> str:
    """True camelCase: 'hello world' -> 'helloWorld' (first word lowercase)."""
    words = _split_words(value)
    if not words:
        return value
    return words[0].lower() + ''.join(w.capitalize() for w in words[1:])


def _as_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _decimals_arg(arg: Any) -> int:
    """Coerce a transform arg to a non-negative int decimal-places count."""
    if arg is None:
        return 0
    try:
        return max(0, int(arg))
    except (TypeError, ValueError):
        return 0


def _t_round(value: str, arg: Any) -> str:
    """Round-half-even (Python builtin 'round') to N decimal places (default 0)."""
    x = _as_float(value)
    if x is None:
        return value
    n = _decimals_arg(arg)
    r = round(x, n)
    return str(int(r)) if n == 0 else str(r)


def _t_ceil(value: str, arg: Any) -> str:
    """Round up (toward +infinity) to N decimal places (default 0)."""
    x = _as_float(value)
    if x is None:
        return value
    n = _decimals_arg(arg)
    scale = 10 ** n
    r = math.ceil(x * scale) / scale
    return str(int(r)) if n == 0 else str(r)


def _t_floor(value: str, arg: Any) -> str:
    """Round down (toward -infinity) to N decimal places (default 0)."""
    x = _as_float(value)
    if x is None:
        return value
    n = _decimals_arg(arg)
    scale = 10 ** n
    r = math.floor(x * scale) / scale
    return str(int(r)) if n == 0 else str(r)


def _t_int(value: str, _arg: Any) -> str:
    """Truncate toward zero (string -> float -> int), e.g. '71.8' -> '71'."""
    x = _as_float(value)
    if x is None:
        return value
    return str(int(x))


def _t_abs(value: str, _arg: Any) -> str:
    x = _as_float(value)
    if x is None:
        return value
    r = abs(x)
    return str(int(r)) if r == int(r) else str(r)


def _t_multiply(value: str, arg: Any) -> str:
    x = _as_float(value)
    factor = _as_float(str(arg)) if arg is not None else None
    if x is None or factor is None:
        return value
    r = x * factor
    return str(int(r)) if r == int(r) else str(r)


def _t_add(value: str, arg: Any) -> str:
    x = _as_float(value)
    amount = _as_float(str(arg)) if arg is not None else None
    if x is None or amount is None:
        return value
    r = x + amount
    return str(int(r)) if r == int(r) else str(r)


def _t_replace(value: str, arg: Any) -> str:
    if not isinstance(arg, dict):
        return value
    frm = arg.get('from')
    to = arg.get('to', '')
    if frm is None:
        return value
    return value.replace(str(frm), str(to))


def _t_prefix(value: str, arg: Any) -> str:
    if arg is None:
        return value
    return str(arg) + value


def _t_suffix(value: str, arg: Any) -> str:
    if arg is None:
        return value
    return value + str(arg)


def _t_format(value: str, arg: Any) -> str:
    """Apply a raw Python format-spec, e.g. {format: "{:.1f}F"}.

    Tries numeric formatting first (float(value)); falls back to formatting
    the raw string if the spec has no numeric placeholder or value isn't
    numeric.
    """
    if not isinstance(arg, str):
        return value
    x = _as_float(value)
    if x is not None:
        try:
            return arg.format(x)
        except (ValueError, IndexError, KeyError):
            pass
    try:
        return arg.format(value)
    except Exception:
        return value


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

#: name -> callable(value: str, arg: Any) -> str
TRANSFORM_REGISTRY: dict[str, Callable[[str, Any], str]] = {
    'upper': _t_upper,
    'lower': _t_lower,
    'title': _t_title,
    'capitalize': _t_capitalize,
    'titlecase': _t_titlecase,
    'pascalcase': _t_pascalcase,
    'camelcase': _t_camelcase,
    'strip': _t_strip,
    'round': _t_round,
    'ceil': _t_ceil,
    'floor': _t_floor,
    'int': _t_int,
    'abs': _t_abs,
    'multiply': _t_multiply,
    'add': _t_add,
    'replace': _t_replace,
    'prefix': _t_prefix,
    'suffix': _t_suffix,
    'format': _t_format,
}

#: Transform names that take no argument (simple string form only).
NO_ARG_TRANSFORMS: frozenset[str] = frozenset({
    'upper', 'lower', 'title', 'capitalize', 'titlecase', 'pascalcase',
    'camelcase', 'strip', 'abs',
})

#: Transform names that require an argument (must be given as a mapping).
REQUIRED_ARG_TRANSFORMS: frozenset[str] = frozenset({
    'multiply', 'add', 'replace', 'prefix', 'suffix', 'format',
})

#: Transform names that accept an optional numeric argument (decimal places).
OPTIONAL_NUMERIC_ARG_TRANSFORMS: frozenset[str] = frozenset({
    'round', 'ceil', 'floor',
})

#: All known transform names -- used by config_validator.py.
KNOWN_TRANSFORM_NAMES: frozenset[str] = frozenset(TRANSFORM_REGISTRY.keys())


def apply_transforms(value: str, transform_list: list | None, *, debug: bool = False) -> str:
    """Apply an ordered list of transforms to *value*.

    Each entry is either:
      - a str: transform name, no argument (e.g. 'upper')
      - a dict with exactly one key: {name: arg} (e.g. {'round': 1})

    Unknown names or bad entries are skipped silently (validated separately
    by config_validator.py at startup) -- never raises.
    """
    if not transform_list:
        return value

    result = value
    for entry in transform_list:
        if isinstance(entry, str):
            name, arg = entry, None
        elif isinstance(entry, dict) and len(entry) == 1:
            name, arg = next(iter(entry.items()))
        else:
            continue

        fn = TRANSFORM_REGISTRY.get(name)
        if fn is None:
            continue

        try:
            result = fn(result, arg)
        except Exception as e:
            if debug:
                print(f"DEBUG: transform '{name}' failed on {result!r}: {e}")
            # leave result unchanged for this step
    return result
