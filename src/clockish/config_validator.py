"""clockish.config_validator
===========================
Validate clockish YAML layout configuration files.

Three use-cases
---------------
1. Pre-commit hook / GitHub Action (CI)::

       clockish-validate --strict configs/*.yaml
       # Exit 1 on any ERROR; WARNINGs are informational.

2. Ad-hoc, by a user::

       clockish-validate my-config.yaml
       python -m clockish.config_validator my-config.yaml

3. Clockish startup (called from display._init() after config is loaded)::

       from clockish.config_validator import validate_config_dict
       result = validate_config_dict(config, path=config_path)
       result.print_summary(file=sys.stderr)

Severity levels
---------------
ERROR
    Structural problems that will prevent correct rendering or crash clockish
    at runtime.  In strict CI mode these produce a non-zero exit code.
    At startup they are printed prominently but do **not** abort startup  --
    clockish will attempt to continue in a degraded state.

WARNING
    Deprecated keys, unknown attributes, or suspicious values that the
    current runtime silently ignores.  Always printed; never causes failure.

Dependencies
------------
Required (already in clockish main deps):
    PyYAML     --  config loading

Optional but strongly recommended:
    jsonschema  --  schema validation; added to clockish[validate] and dev extras.
                 If not installed, schema checks are skipped with a notice.
    yamllint    --  YAML style lint; dev / CI only.
                 If not installed, lint checks are skipped with a notice.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Optional dependencies  --  handled gracefully if not installed
# ---------------------------------------------------------------------------
try:
    import jsonschema
    import jsonschema.exceptions
    _JSONSCHEMA_AVAILABLE = True
except ImportError:  # pragma: no cover
    _JSONSCHEMA_AVAILABLE = False

try:
    from yamllint import linter as _yl_linter
    from yamllint.config import YamlLintConfig as _YamlLintConfig
    _YAMLLINT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _YAMLLINT_AVAILABLE = False


# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

#: Panel types recognised by the display engine.
KNOWN_PANEL_TYPES: frozenset[str] = frozenset({
    'clock', 'date', 'fact', 'text', 'divider', 'wifi_graphic', 'debug', 'blank',
})

#: Valid ``source:`` values for ``type: fact`` panels.
KNOWN_FACT_SOURCES: frozenset[str] = frozenset({
    'ip', 'hostname', 'uptime', 'version', 'config_file',
    'cpu', 'cpu_load', 'mem', 'disk', 'temp',
    'ntp_status', 'ntp_upstream', 'ntp_all',
    'wireguard',
    'wifi_status', 'wifi_ssid', 'wifi_signal', 'wifi_quality', 'wifi_all',
})

#: Built-in font scale names (should be used with ``font_size:``, not ``font:``).
BUILTIN_FONT_NAMES: frozenset[str] = frozenset({
    'giant', 'huge', 'big', 'med', 'normal', 'small', 'tiny', 'micro',
})

#: Deprecated panel keys  ->  replacement hint.
_DEPRECATED_PANEL_KEYS: dict[str, str] = {
    'time_font':  "use 'font_size:' instead",
    'label_font': "use 'font_size:' instead",
    'date_font':  "use 'font_size:' instead",
    'colors':     "use 'color: <string>' instead of 'colors: {dict}'",
}

#: All valid attribute keys per panel type.
#: Keys that appear here but are present in the config do NOT trigger unknown-key warnings.
_PANEL_TYPE_ATTRS: dict[str, frozenset[str]] = {
    'clock': frozenset({
        'type', 'justify', 'color', 'font', 'font_size', 'width', 'background', 'label',
        'timezone', 'time_format',
    }),
    'date': frozenset({
        'type', 'justify', 'color', 'font', 'font_size', 'width', 'background',
        'timezone', 'date_format',
    }),
    'fact': frozenset({
        'type', 'justify', 'color', 'font', 'font_size', 'width', 'background', 'label',
        'source',
    }),
    'text': frozenset({
        'type', 'justify', 'color', 'font', 'font_size', 'width', 'background', 'label',
    }),
    'divider': frozenset({
        'type', 'color', 'height', 'width', 'background',
    }),
    'wifi_graphic': frozenset({
        'type', 'color', 'width', 'background',
    }),
    'debug': frozenset({
        'type', 'color', 'font', 'font_size', 'width', 'background',
    }),
    'blank': frozenset({
        'type', 'width', 'background',
    }),
}

#: Valid row-level keys.
_KNOWN_ROW_KEYS: frozenset[str] = frozenset({
    'name', 'height', 'panels', 'background',
    '_widths',   # runtime-injected by _init_layout(); harmless if present in config
})

#: Valid top-level config keys.
_KNOWN_TOP_LEVEL_KEYS: frozenset[str] = frozenset({
    'orientation', 'default_font', 'fonts', 'rows', 'display',
})


# ---------------------------------------------------------------------------
# JSON Schema for structural validation (jsonschema / Draft 7)
# ---------------------------------------------------------------------------

_HEIGHT_SCHEMA: dict = {
    "description": "Row height: integer pixels >= 1, float fraction 0 < x < 1, or percentage string like '15%'.",
    "anyOf": [
        {"type": "integer", "minimum": 1},
        {"type": "number", "exclusiveMinimum": 0.0, "exclusiveMaximum": 1.0},
        {"type": "string", "pattern": r"^\d+(\.\d+)?%$"},
    ],
}

_WIDTH_SCHEMA: dict = {
    "description": "Panel width: integer pixels >= 1, float fraction 0 < x < 1, percentage string, 'auto', or 'default'.",
    "anyOf": [
        {"type": "integer", "minimum": 1},
        {"type": "number", "exclusiveMinimum": 0.0, "exclusiveMaximum": 1.0},
        {"type": "string"},
    ],
}

#: JSON Schema (Draft 7) for a clockish layout config.
#: Export this via ``clockish-validate --dump-schema`` for editor integration.
CLOCKISH_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft-07/schema#",
    "title": "Clockish Layout Config",
    "description": (
        "Schema for clockish YAML display layout configuration files. "
        "See https://github.com/shorted-neuron/clockish for documentation."
    ),
    "type": "object",
    "required": ["orientation", "rows"],
    "properties": {
        "orientation": {
            "type": "string",
            "enum": ["landscape", "portrait", "square"],
            "description": (
                "Display orientation hint. Informational -- used when selecting "
                "a config for a specific display. "
                "landscape: aspect >= ~1.5 (e.g. 800x480). "
                "portrait:  aspect <= ~0.7 (e.g. 320x480). "
                "square:    aspect ~ 1.0."
            ),
        },
        "default_font": {
            "type": "string",
            "description": (
                "TTF file name used for all built-in scale font sizes "
                "(giant/huge/big/med/normal/small/tiny/micro). "
                "Searched in third_party/ subdirectories and system font paths."
            ),
        },
        "fonts": {
            "type": "object",
            "description": "Named custom font definitions. Each key is a font reference name used in panels.",
            "additionalProperties": {
                "type": "object",
                "required": ["file"],
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "TTF file name (searched in third_party/ and system font paths).",
                    },
                    "size": {
                        "description": "Optional fixed size: integer pixels or percentage string like '10%'.",
                    },
                },
                "additionalProperties": False,
            },
        },
        "display": {
            "type": "object",
            "description": "Display hardware profile. Usually provided by a separate display.yaml file.",
        },
        "rows": {
            "type": "array",
            "minItems": 1,
            "description": "Ordered list of display rows, rendered top-to-bottom.",
            "items": {
                "type": "object",
                "required": ["name", "height"],
                "additionalProperties": True,
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Unique label for this row (used in debug output).",
                    },
                    "height": _HEIGHT_SCHEMA,
                    "background": {
                        "type": "string",
                        "description": "Row background color (name or hex). Default: black.",
                    },
                    "panels": {
                        "type": "array",
                        "minItems": 1,
                        "description": "Left-to-right ordered list of panels in this row.",
                        "items": {
                            "type": "object",
                            "required": ["type"],
                            "additionalProperties": True,
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "description": (
                                        "Panel renderer. Known types: "
                                        "clock, date, fact, text, divider, wifi_graphic, debug, blank."
                                    ),
                                },
                                "justify": {
                                    "type": "string",
                                    "enum": ["left", "right", "center"],
                                    "description": "Horizontal text alignment within the panel.",
                                },
                                "color": {
                                    "type": "string",
                                    "description": "Foreground/text color (palette name or hex string).",
                                },
                                "background": {
                                    "type": "string",
                                    "description": "Panel background color override.",
                                },
                                "font": {
                                    "type": "string",
                                    "description": (
                                        "Named font entry from the 'fonts:' section, or the special "
                                        "alias 'debug' (always DejaVu Sans). "
                                        "Do NOT use built-in scale names here  --  use 'font_size:' instead."
                                    ),
                                },
                                "font_size": {
                                    "description": (
                                        "Font size: built-in scale name (giant/huge/big/med/normal/small/tiny/micro), "
                                        "percentage string like '10%', integer pixels, or 'auto' (75% of row height)."
                                    ),
                                },
                                "width": _WIDTH_SCHEMA,
                                "label": {
                                    "type": "string",
                                    "description": "Static label text prefix. Use 'default' for the built-in label.",
                                },
                            },
                        },
                    },
                },
            },
        },
    },
    "additionalProperties": True,
}


# ---------------------------------------------------------------------------
# Issue / Result types
# ---------------------------------------------------------------------------

@dataclass
class ValidationIssue:
    """A single validation finding with a severity, location, and message."""

    severity: str   # 'ERROR' or 'WARNING'
    path: str       # human-readable location, e.g. "config.yaml:rows[0].panels[1]"
    message: str

    def __str__(self) -> str:
        icon = 'X' if self.severity == 'ERROR' else '!'
        return f"  {icon} [{self.severity}] {self.path}: {self.message}"


@dataclass
class ValidationResult:
    """Aggregated result of validating one config file."""

    path: str = "<unknown>"
    issues: list[ValidationIssue] = field(default_factory=list)

    # -- Convenience accessors -----------------------------------------------

    @property
    def errors(self) -> list[ValidationIssue]:
        """All ERROR-level issues."""
        return [i for i in self.issues if i.severity == 'ERROR']

    @property
    def warnings(self) -> list[ValidationIssue]:
        """All WARNING-level issues."""
        return [i for i in self.issues if i.severity == 'WARNING']

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    @property
    def ok(self) -> bool:
        """True if there are no ERRORs (warnings are tolerated)."""
        return not self.has_errors

    # -- Mutators ------------------------------------------------------------

    def add_error(self, location: str, message: str) -> None:
        self.issues.append(ValidationIssue('ERROR', location, message))

    def add_warning(self, location: str, message: str) -> None:
        self.issues.append(ValidationIssue('WARNING', location, message))

    # -- Reporting -----------------------------------------------------------

    def print_summary(self, *, file=None) -> None:
        """Print all issues to *file* (default: ``sys.stderr``)."""
        if file is None:
            file = sys.stderr
        if not self.issues:
            print(f"  OK  {self.path}: OK", file=file)
            return
        print(f"\n{self.path}:", file=file)
        for issue in self.issues:
            print(str(issue), file=file)
        n_e = len(self.errors)
        n_w = len(self.warnings)
        summary = f"  -> {n_e} error(s), {n_w} warning(s)"
        print(summary, file=file)


# ---------------------------------------------------------------------------
# Internal: yamllint
# ---------------------------------------------------------------------------

def _find_yamllint_config() -> str | None:
    """Search for a .yamllint.yaml file starting from the CWD up to the root."""
    for name in ('.yamllint.yaml', '.yamllint.yml', '.yamllint'):
        # Check CWD and the directory containing this module
        candidates = [
            os.path.join(os.getcwd(), name),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', name),
        ]
        for path in candidates:
            norm = os.path.normpath(path)
            if os.path.isfile(norm):
                return norm
    return None


def _validate_yamllint(path: str) -> list[ValidationIssue]:
    """Run yamllint on *path* and return issues.  Gracefully skips if yamllint is absent."""
    if not _YAMLLINT_AVAILABLE:
        return [ValidationIssue(
            'WARNING', path,
            "yamllint not installed  --  YAML style checks skipped. "
            "Install with: pip install 'clockish[validate]'  or  pip install yamllint",
        )]

    cfg_path = _find_yamllint_config()
    if cfg_path:
        try:
            yl_cfg = _YamlLintConfig(file=cfg_path)
        except Exception:
            yl_cfg = None
    else:
        yl_cfg = None

    if yl_cfg is None:
        # Sensible fallback when no config file is found
        yl_cfg = _YamlLintConfig(
            'extends: default\n'
            'rules:\n'
            '  line-length:\n'
            '    max: 120\n'
            '    level: warning\n'
            '  document-start: disable\n'
            '  truthy:\n'
            '    allowed-values: ["true", "false", "yes", "no"]\n'
            '    check-keys: false\n'
            '  comments-indentation: disable\n'
        )

    issues: list[ValidationIssue] = []
    try:
        with open(path, encoding='utf-8') as fh:
            content = fh.read()
        for problem in _yl_linter.run(content, yl_cfg):
            sev = 'ERROR' if problem.level == 'error' else 'WARNING'
            issues.append(ValidationIssue(
                sev,
                f"{path}:{problem.line}:{problem.column}",
                f"[yamllint] {problem.message}",
            ))
    except OSError as exc:
        issues.append(ValidationIssue('ERROR', path, f"Cannot read file for yamllint: {exc}"))
    return issues


# ---------------------------------------------------------------------------
# Internal: JSON Schema structural validation
# ---------------------------------------------------------------------------

def _json_path_str(abs_path) -> str:
    """Format a jsonschema absolute_path deque as a human-readable string."""
    result = ''
    for part in abs_path:
        if isinstance(part, int):
            result += f'[{part}]'
        else:
            result += ('.' if result else '') + str(part)
    return result or '(root)'


def _validate_schema(config: dict, file_path: str) -> list[ValidationIssue]:
    """Validate *config* against :data:`CLOCKISH_SCHEMA` using jsonschema.

    Returns ERROR-level issues for schema violations.
    Gracefully skips if jsonschema is not installed.
    """
    if not _JSONSCHEMA_AVAILABLE:
        return [ValidationIssue(
            'WARNING', file_path,
            "jsonschema not installed  --  structural schema checks skipped. "
            "Install with: pip install jsonschema",
        )]

    issues: list[ValidationIssue] = []
    validator = jsonschema.Draft7Validator(CLOCKISH_SCHEMA)
    for error in sorted(validator.iter_errors(config), key=lambda e: list(e.absolute_path)):
        loc = f"{file_path}:{_json_path_str(error.absolute_path)}"
        issues.append(ValidationIssue('ERROR', loc, error.message))
    return issues


# ---------------------------------------------------------------------------
# Internal: semantic / deprecation walker
# ---------------------------------------------------------------------------

def _validate_semantics(config: dict, file_path: str) -> list[ValidationIssue]:
    """Walk the config tree and emit warnings for deprecated / unknown keys.

    This pass emits WARNING for stylistic / deprecation issues and ERROR for
    panel-level mistakes that will crash the renderer at runtime (e.g. missing
    ``source`` on a ``fact`` panel).
    """
    issues: list[ValidationIssue] = []

    def warn(location: str, message: str) -> None:
        issues.append(ValidationIssue('WARNING', f"{file_path}:{location}", message))

    def err(location: str, message: str) -> None:
        issues.append(ValidationIssue('ERROR', f"{file_path}:{location}", message))

    # -- Top-level unknown keys ---------------------------------------------
    for key in config:
        if key not in _KNOWN_TOP_LEVEL_KEYS:
            warn('(root)', f"unknown top-level key '{key}'")

    # -- fonts section ------------------------------------------------------
    fonts_cfg = config.get('fonts', {})
    if isinstance(fonts_cfg, dict):
        for fname, fentry in fonts_cfg.items():
            if not isinstance(fentry, dict):
                warn(f'fonts.{fname}', "font entry must be a mapping with at least a 'file:' key")

    # -- rows ---------------------------------------------------------------
    rows = config.get('rows')
    if not isinstance(rows, list):
        return issues  # structural check already caught this

    for ri, row in enumerate(rows):
        if not isinstance(row, dict):
            continue  # structural check handles this

        row_name = row.get('name', f'row[{ri}]')

        # Unknown row keys
        for key in row:
            if key not in _KNOWN_ROW_KEYS:
                warn(f'rows[{ri}]', f"unknown row-level key '{key}'")

        # Missing / empty panels
        panels = row.get('panels')
        if panels is None:
            warn(
                f'rows[{ri}]',
                f"row '{row_name}' has no 'panels' key -- renders as blank space; "
                "consider 'panels: [{type: blank}]' for clarity",
            )
            continue
        if not isinstance(panels, list):
            continue  # structural check handles
        if len(panels) == 0:
            warn(f'rows[{ri}].panels', f"row '{row_name}' has an empty 'panels' list")
            continue

        # -- panels ---------------------------------------------------------
        for pi, panel in enumerate(panels):
            ploc = f'rows[{ri}].panels[{pi}]'
            if not isinstance(panel, dict):
                continue  # structural check handles

            ptype = panel.get('type', '')

            # 1. Deprecated keys
            for dep_key, hint in _DEPRECATED_PANEL_KEYS.items():
                if dep_key in panel:
                    val = panel[dep_key]
                    if dep_key == 'colors':
                        # Deprecated regardless of whether value is a dict or not
                        warn(ploc, f"deprecated key 'colors' -- {hint}")
                    else:
                        warn(ploc, f"deprecated key '{dep_key}: {val!r}' -- {hint}")

            # 2. 'font:' used as a built-in scale name (should be 'font_size:')
            font_val = panel.get('font')
            if font_val and font_val in BUILTIN_FONT_NAMES:
                warn(
                    ploc,
                    f"'font: {font_val}' looks like a built-in scale name -- "
                    f"use 'font_size: {font_val}' to set size; "
                    "'font:' should reference a named entry from the 'fonts:' section",
                )

            # 3. Invalid 'justify' value (also caught by schema, but give a nicer message)
            justify = panel.get('justify')
            if justify is not None and justify not in {'left', 'right', 'center'}:
                warn(
                    ploc,
                    f"'justify: {justify!r}' is not a valid value "
                    "(expected: left, right, or center)",
                )

            # 4. Unknown panel type
            if ptype and ptype not in KNOWN_PANEL_TYPES:
                warn(
                    ploc,
                    f"unknown panel type '{ptype}' "
                    f"(known types: {', '.join(sorted(KNOWN_PANEL_TYPES))})",
                )

            # 5. Unknown keys for known panel types
            if ptype in _PANEL_TYPE_ATTRS:
                allowed = _PANEL_TYPE_ATTRS[ptype]
                for key in panel:
                    if key not in allowed and key not in _DEPRECATED_PANEL_KEYS:
                        warn(ploc, f"unexpected key '{key}' on '{ptype}' panel")

            # 6. fact panel: source required (runtime crash without it) + must be recognised
            if ptype == 'fact':
                source = panel.get('source')
                if not source:
                    # p['source'] is accessed directly in the renderer -- KeyError at runtime.
                    err(ploc, "fact panel is missing required 'source' key (will crash at runtime)")
                elif source not in KNOWN_FACT_SOURCES:
                    warn(
                        ploc,
                        f"unrecognised fact source '{source}' "
                        f"(known sources: {', '.join(sorted(KNOWN_FACT_SOURCES))})",
                    )

    return issues


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_config_dict(
    config: Any,
    path: str = "<dict>",
    *,
    run_yamllint: bool = False,
) -> ValidationResult:
    """Validate an already-loaded config dict.

    This is the function to call at **clockish startup** (the config has
    already been parsed by PyYAML).

    Args:
        config:       Parsed config object (expected: dict).
        path:         A label for error messages (e.g. the source file path).
        run_yamllint: yamllint cannot re-lint an already-parsed dict, so this
                      is always ``False`` here; the parameter exists for API
                      symmetry with :func:`validate_config_file`.

    Returns:
        :class:`ValidationResult` with all collected issues.
    """
    result = ValidationResult(path=path)

    if not isinstance(config, dict):
        result.add_error(path, "Config must be a YAML mapping (dict) at the top level")
        return result

    result.issues.extend(_validate_schema(config, path))
    result.issues.extend(_validate_semantics(config, path))
    return result


def validate_config_file(
    path: str,
    *,
    run_yamllint: bool = True,
) -> ValidationResult:
    """Validate a clockish YAML config **file**.

    Runs (in order):

    1. yamllint  --  YAML syntax and style (if *run_yamllint* is True and yamllint
       is installed).
    2. PyYAML parse  --  catches any remaining YAML errors before schema checks.
    3. JSON Schema  --  structural requirements (orientation, rows, panels, types).
    4. Semantic walker  --  deprecated keys, unknown attributes, suspicious values.

    Args:
        path:         Path to the YAML config file.
        run_yamllint: Set to ``False`` to skip yamllint (faster; for startup).

    Returns:
        :class:`ValidationResult` with all collected issues.
    """
    result = ValidationResult(path=path)

    # 1. yamllint
    if run_yamllint:
        result.issues.extend(_validate_yamllint(path))

    # 2. PyYAML parse
    try:
        with open(path, encoding='utf-8') as fh:
            config = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        result.add_error(path, f"YAML parse error: {exc}")
        return result
    except OSError as exc:
        result.add_error(path, f"Cannot read file: {exc}")
        return result

    if config is None:
        result.add_error(path, "Config file is empty or contains only comments")
        return result

    if not isinstance(config, dict):
        result.add_error(path, "Config must be a YAML mapping (dict) at the top level")
        return result

    # 3 + 4. Structural + semantic validation
    result.issues.extend(_validate_schema(config, path))
    result.issues.extend(_validate_semantics(config, path))
    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """CLI for ad-hoc validation and CI/pre-commit use.

    Exit codes:
        0  All files validated without errors (warnings don't count).
        1  One or more files have ERRORs (or any issue when ``--strict``).
    """
    parser = argparse.ArgumentParser(
        prog='clockish-validate',
        description='Validate clockish YAML configuration files.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'examples:\n'
            '  clockish-validate configs/nixie.yaml\n'
            '  clockish-validate --strict configs/*.yaml\n'
            '  clockish-validate --no-yamllint my-config.yaml\n'
            '  clockish-validate --dump-schema > clockish-config.schema.yaml\n'
        ),
    )
    parser.add_argument(
        'configs', nargs='*', metavar='CONFIG',
        help='Path(s) to clockish YAML config file(s). Required unless --dump-schema.',
    )
    parser.add_argument(
        '--strict', action='store_true',
        help='Exit 1 if any WARNINGs are found (useful for CI to enforce clean configs).',
    )
    parser.add_argument(
        '--no-yamllint', action='store_true',
        help='Skip yamllint checks (faster; equivalent to startup-time validation).',
    )
    parser.add_argument(
        '--quiet', '-q', action='store_true',
        help='Only print files that have issues.',
    )
    parser.add_argument(
        '--dump-schema', action='store_true',
        help='Print the embedded JSON Schema as YAML and exit (for editor integration).',
    )
    args = parser.parse_args(argv)

    # --dump-schema: print schema and exit
    if args.dump_schema:
        print(yaml.dump(CLOCKISH_SCHEMA, allow_unicode=True, sort_keys=False), end='')
        return 0

    if not args.configs:
        parser.print_help()
        return 1

    overall_ok = True
    for config_path in args.configs:
        result = validate_config_file(config_path, run_yamllint=not args.no_yamllint)

        if args.quiet and not result.issues:
            continue

        result.print_summary(file=sys.stdout)

        if result.has_errors:
            overall_ok = False
        if args.strict and result.warnings:
            overall_ok = False

    return 0 if overall_ok else 1


if __name__ == '__main__':
    sys.exit(main())
