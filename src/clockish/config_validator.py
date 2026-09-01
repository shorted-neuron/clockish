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
Required:
    PyYAML     --  config loading
    jsonschema  --  schema validation; required dep (pyproject.toml main deps).
    yamllint    --  YAML style lint; required dep (also available as apt: yamllint).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any

import yaml

# When run as a bare script (e.g. the clockish-validate pre-commit hook:
# `python src/clockish/config_validator.py`), Python puts this file's own
# directory (src/clockish/) on sys.path but not its parent (src/), so
# `import clockish.*` fails with ModuleNotFoundError. Insert src/ ahead of the
# sibling-module import below so this works with or without an editable
# package install.
if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clockish.transforms import (  # noqa: E402
    KNOWN_TRANSFORM_NAMES,
    NO_ARG_TRANSFORMS,
    OPTIONAL_NUMERIC_ARG_TRANSFORMS,
    REQUIRED_ARG_TRANSFORMS,
)

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
# Helpers for cached-facts validation
# ---------------------------------------------------------------------------

#: Prefix a fact panel's `source:` uses to reference a top-level cached-facts entry.
CACHED_FACTS_SOURCE_PREFIX: str = 'cached-facts.'

def _is_valid_interval(interval_str: str) -> bool:
    """Check if interval_str is in valid format: <number>[s|m|h].

    Examples: '30s', '5m', '1h', '2.5m'
    """
    # TODO: clarify and comment the following return statement, perhaps split it
    #       into something less efficient but more readable to humans
    return bool(re.match(r'^\d+(?:\.\d+)?[smh]$', interval_str.strip()))


# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

#: Panel types recognised by the display engine.
KNOWN_PANEL_TYPES: frozenset[str] = frozenset({
    'clock', 'date', 'fact', 'text', 'divider', 'wifi_graphic', 'debug', 'blank',
})

#: Valid ``source:`` values for ``type: fact`` panels. A ``fact`` panel may
#: ALSO use ``source: cached-facts.<name>`` to pull from a top-level
#: ``cached-facts:`` entry (background-thread-fetched, non-blocking) -- see
#: ``CACHED_FACTS_SOURCE_PREFIX`` / ``_CACHED_FACT_ATTRS`` below.
KNOWN_FACT_SOURCES: frozenset[str] = frozenset({
    'ip', 'hostname', 'uptime', 'version', 'config_file',
    'cpu', 'cpu_load', 'mem', 'disk', 'temp',
    'ntp_status', 'ntp_upstream', 'ntp_all',
    'wireguard',
    'wifi_status', 'wifi_ssid', 'wifi_signal', 'wifi_quality', 'wifi_all',
    # new built-in facts
    'location', 'daytime', 'nighttime',
})

#: Fetcher ``type:`` values recognised for ``cached-facts:`` entries.
KNOWN_CACHED_FACT_TYPES: frozenset[str] = frozenset({'url-fact'})

#: All valid attribute keys for a ``cached-facts:`` list entry.
_CACHED_FACT_ATTRS: frozenset[str] = frozenset({
    'name', 'type', 'url', 'interval', 'timeout', 'verify_ssl', 'preview_response',
})

#: Built-in font scale names (should be used with ``font_size:``, not ``font:``).
BUILTIN_FONT_NAMES: frozenset[str] = frozenset({
    'giant', 'huge', 'big', 'med', 'normal', 'small', 'tiny', 'micro',
})

#: Valid 'font_behavior' values (row-level default, panel-level override).
#: Mirrors clockish.display.KNOWN_FONT_BEHAVIORS -- duplicated (not imported)
#: to keep this module free of display.py's hardware-driver imports.
KNOWN_FONT_BEHAVIORS: frozenset[str] = frozenset({
    'default', 'scale', 'scale_numeric', 'stretch_y', 'stretch_x',
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
#: 'padding' is universal (applied in _dispatch_panel() before type-specific
#: rendering) so it's included on every panel type below.
_PANEL_TYPE_ATTRS: dict[str, frozenset[str]] = {
    'clock': frozenset({
        'type', 'justify', 'color', 'font', 'font_size', 'font_behavior', 'width',
        'background', 'label', 'timezone', 'time_format', 'transform', 'padding',
    }),
    'date': frozenset({
        'type', 'justify', 'color', 'font', 'font_size', 'font_behavior', 'width',
        'background', 'timezone', 'date_format', 'transform', 'padding',
    }),
    'fact': frozenset({
        'type', 'justify', 'color', 'font', 'font_size', 'font_behavior', 'width',
        'background', 'label', 'source', 'transform', 'padding', 'mem_format',
        'json_path', 'pattern',
    }),
    'text': frozenset({
        'type', 'justify', 'color', 'font', 'font_size', 'font_behavior', 'width',
        'background', 'label', 'transform', 'padding',
    }),
    'divider': frozenset({
        'type', 'color', 'height', 'width', 'background', 'padding',
    }),
    'wifi_graphic': frozenset({
        'type', 'color', 'width', 'background', 'padding',
    }),
    'debug': frozenset({
        'type', 'color', 'font', 'font_size', 'width', 'background', 'padding',
    }),
    'blank': frozenset({
        'type', 'width', 'background', 'padding',
    }),
}

#: Valid row-level keys.
_KNOWN_ROW_KEYS: frozenset[str] = frozenset({
    'name', 'height', 'panels', 'background', 'font_behavior',
    '_widths',   # runtime-injected by _init_layout(); harmless if present in config
})

#: Valid top-level config keys.
_KNOWN_TOP_LEVEL_KEYS: frozenset[str] = frozenset({
    'orientation', 'default_font', 'fonts', 'rows', 'display', 'preview_size', 'cached-facts', 'reload', 'location',
})

#: Format for preview_size: "WxH", e.g. "240x135". Preview-tool only; ignored by production.
_PREVIEW_SIZE_RE = re.compile(r'^\d+x\d+$')


# ---------------------------------------------------------------------------
# JSON Schema for structural validation (jsonschema / Draft 7)
# ---------------------------------------------------------------------------

_HEIGHT_SCHEMA: dict = {
    "description": (
        "Row height: integer pixels >= 1, 'Npx' string, float fraction 0 < x < 1, "
        "or percentage string like '15%'."
    ),
    "anyOf": [
        {"type": "integer", "minimum": 1},
        {"type": "number", "exclusiveMinimum": 0.0, "exclusiveMaximum": 1.0},
        {"type": "string", "pattern": r"^\d+(\.\d+)?%$"},
        {"type": "string", "pattern": r"^\d+(\.\d+)?px$"},
    ],
}

_WIDTH_SCHEMA: dict = {
    "description": (
        "Panel width: integer pixels >= 1, 'Npx' string, float fraction 0 < x < 1, "
        "percentage string, 'auto', or 'default'."
    ),
    "anyOf": [
        {"type": "integer", "minimum": 1},
        {"type": "number", "exclusiveMinimum": 0.0, "exclusiveMaximum": 1.0},
        {"type": "string"},
    ],
}

#: JSON Schema (Draft 7) for a clockish layout config.
#: Defined in configs/schema/clockish-config.schema.yaml; loaded at module init.
def _load_schema() -> dict:
    """Load JSON Schema from configs/schema/clockish-config.schema.yaml."""
    schema_path = os.path.join(
        os.path.dirname(__file__), '..', '..', 'configs', 'schema', 'clockish-config.schema.yaml'
    )
    with open(schema_path, encoding='utf-8') as f:
        return yaml.safe_load(f)

CLOCKISH_SCHEMA: dict = _load_schema()


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
            "Install with: pip install yamllint  (or re-run install.sh)",
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

    # -- preview_size ---------------------------------------------------------
    preview_size = config.get('preview_size')
    if preview_size is not None:
        if not isinstance(preview_size, str) or not _PREVIEW_SIZE_RE.match(preview_size):
            err('(root)', f"preview_size '{preview_size}' must be a string 'WxH' (e.g. '240x135')")

    # -- fonts section ------------------------------------------------------
    fonts_cfg = config.get('fonts', {})
    if isinstance(fonts_cfg, dict):
        for fname, fentry in fonts_cfg.items():
            if not isinstance(fentry, dict):
                warn(f'fonts.{fname}', "font entry must be a mapping with at least a 'file:' key")

    # -- cached-facts section -------------------------------------------------
    # Background-thread-fetched data sources, referenced by `fact` panels via
    # `source: cached-facts.<name>`. Collect valid names here (used below when
    # walking panels) and validate each entry's own shape.
    cached_fact_names: set[str] = set()
    cached_facts_cfg = config.get('cached-facts')
    if cached_facts_cfg is not None:
        if not isinstance(cached_facts_cfg, list):
            err('cached-facts', "'cached-facts' must be a list of entries")
        else:
            seen_names: set[str] = set()
            for ci, entry in enumerate(cached_facts_cfg):
                cloc = f'cached-facts[{ci}]'
                if not isinstance(entry, dict):
                    err(cloc, "cached-facts entry must be a mapping")
                    continue

                for key in entry:
                    if key not in _CACHED_FACT_ATTRS:
                        warn(cloc, f"unexpected key '{key}' on cached-facts entry")

                name = entry.get('name')
                if not name or not isinstance(name, str):
                    err(cloc, "cached-facts entry missing required 'name' key (string)")
                elif name in seen_names:
                    err(cloc, f"duplicate cached-facts name '{name}'")
                else:
                    seen_names.add(name)
                    cached_fact_names.add(name)

                cf_type = entry.get('type')
                if not cf_type:
                    err(cloc, "cached-facts entry missing required 'type' key")
                elif cf_type not in KNOWN_CACHED_FACT_TYPES:
                    err(
                        cloc,
                        f"unknown cached-facts type '{cf_type}' "
                        f"(known types: {', '.join(sorted(KNOWN_CACHED_FACT_TYPES))})",
                    )

                url = entry.get('url')
                if not url:
                    err(cloc, "cached-facts entry missing required 'url' key (will crash at runtime)")

                interval = entry.get('interval')
                if interval is not None:
                    if not isinstance(interval, str) or not _is_valid_interval(interval):
                        warn(
                            cloc,
                            f"cached-facts entry invalid 'interval: {interval}' "
                            "(use format: 30s, 5m, 1h, etc.)",
                        )

                verify_ssl = entry.get('verify_ssl')
                if verify_ssl is not None and isinstance(url, str) and url.lower().startswith('http://'):
                    warn(
                        cloc,
                        "cached-facts entry has 'verify_ssl' but URL is http:// "
                        "(verify_ssl ignored for http)",
                    )

    # -- reload section ---------------------------------------------------
    reload_cfg = config.get('reload')
    if reload_cfg is not None:
        if not isinstance(reload_cfg, dict):
            err('reload', "'reload' must be a mapping")
        else:
            for key in reload_cfg:
                if key not in ('poll_interval',):
                    warn('reload', f"unexpected key '{key}' in reload section")

            poll_interval = reload_cfg.get('poll_interval')
            if poll_interval is not None:
                if not isinstance(poll_interval, str) or not _is_valid_interval(poll_interval):
                    err('reload', f"poll_interval '{poll_interval}' must be a string in format <number>[s|m|h]")

    # -- rows ---------------------------------------------------------------
    rows = config.get('rows')
    if not isinstance(rows, list):
        return issues  # structural check already caught this

    # Track which location subkeys panels reference (e.g. location.city)
    ref_key_to_paths: dict[str, list[str]] = {}
    composite_panel_paths: list[str] = []  # panels that use source: location (whole dict)

    for ri, row in enumerate(rows):
        if not isinstance(row, dict):
            continue  # structural check handles this

        row_name = row.get('name', f'row[{ri}]')

        # Unknown row keys
        for key in row:
            if key not in _KNOWN_ROW_KEYS:
                warn(f'rows[{ri}]', f"unknown row-level key '{key}'")

        # Invalid row-level 'font_behavior' (panel-level checked below)
        row_behavior = row.get('font_behavior')
        if row_behavior is not None and row_behavior not in KNOWN_FONT_BEHAVIORS:
            warn(
                f'rows[{ri}]',
                f"'font_behavior: {row_behavior!r}' is not a valid value "
                f"(expected one of: {', '.join(sorted(KNOWN_FONT_BEHAVIORS))})",
            )

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

            # 3b. Invalid panel-level 'font_behavior' value
            panel_behavior = panel.get('font_behavior')
            if panel_behavior is not None and panel_behavior not in KNOWN_FONT_BEHAVIORS:
                warn(
                    ploc,
                    f"'font_behavior: {panel_behavior!r}' is not a valid value "
                    f"(expected one of: {', '.join(sorted(KNOWN_FONT_BEHAVIORS))})",
                )

            # 3c. Invalid 'padding' value (integer pixels, all 4 sides, >= 0; default 1)
            padding = panel.get('padding')
            if padding is not None and (
                not isinstance(padding, (int, float)) or isinstance(padding, bool) or padding < 0
            ):
                warn(
                    ploc,
                    f"'padding: {padding!r}' must be a non-negative integer (pixels)",
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
                pattern = panel.get('pattern')
                json_path = panel.get('json_path')
                has_pattern = pattern is not None
                has_json_path = json_path is not None

                if not source:
                    # p['source'] is accessed directly in the renderer -- KeyError at runtime.
                    err(ploc, "fact panel is missing required 'source' key (will crash at runtime)")
                elif isinstance(source, str) and source.startswith(CACHED_FACTS_SOURCE_PREFIX):
                    cf_name = source[len(CACHED_FACTS_SOURCE_PREFIX):]
                    if cf_name not in cached_fact_names:
                        err(
                            ploc,
                            f"fact panel references unknown cached-facts entry '{cf_name}' "
                            f"(known: {', '.join(sorted(cached_fact_names)) or '(none defined)'})",
                        )
                    # Exactly one of pattern or json_path required to extract from the
                    # cached-facts entry's raw fetched value.
                    if has_pattern and has_json_path:
                        err(ploc, "fact panel has both 'pattern' and 'json_path' (use one)")
                    elif not has_pattern and not has_json_path:
                        err(
                            ploc,
                            "fact panel with 'source: cached-facts.*' must have 'pattern' "
                            "or 'json_path' (use exactly one)",
                        )
                else:
                    # Check if source is a valid built-in or a dotted location.* variant
                    is_valid_source = source in KNOWN_FACT_SOURCES
                    if not is_valid_source and isinstance(source, str):
                        # Check for dotted location.* sources (e.g. 'location.city')
                        if source.startswith('location.'):
                            is_valid_source = True

                    if not is_valid_source:
                        warn(
                            ploc,
                            f"unrecognised fact source '{source}' "
                            f"(known sources: {', '.join(sorted(KNOWN_FACT_SOURCES))}, "
                            f"or 'cached-facts.<name>', or 'location.<field>')",
                        )

                    # json_path is only invalid on non-location built-in sources
                    # (location source supports json_path for field extraction)
                    if (has_pattern or has_json_path) and source != 'location':
                        warn(
                            ploc,
                            "'pattern'/'json_path' only apply to "
                            "'source: cached-facts.<name>' or 'source: location' -- ignored otherwise",
                        )

                    # Track references to top-level 'location' fields so we can
                    # warn at config-time if they are missing. Examples:
                    #   source: location.city
                    #   source: location (with json_path: city)
                    if isinstance(source, str) and source.startswith('location'):
                        if '.' in source:
                            _, suffix = source.split('.', 1)
                            ref_key_to_paths.setdefault(suffix, []).append(ploc)
                        elif has_json_path:
                            # source: location + json_path: city
                            ref_key_to_paths.setdefault(json_path, []).append(ploc)
                        else:
                            # panel expects the whole location dict (composite usage)
                            composite_panel_paths.append(ploc)

                # Validate mem_format if present and source is 'mem'
                if source == 'mem':
                    mem_format = panel.get('mem_format')
                    if mem_format is not None:
                        valid_mem_formats = {'both', 'mb', 'percent'}
                        if mem_format not in valid_mem_formats:
                            err(
                                ploc,
                                f"mem_format '{mem_format}' invalid "
                                f"(use: {', '.join(sorted(valid_mem_formats))})",
                            )


            # 8. transform: list validation (any panel type that supports it)
            if 'transform' in panel:
                transform_list = panel['transform']
                if not isinstance(transform_list, list):
                    err(ploc, "'transform' must be a list, e.g. transform: [upper]")
                else:
                    for ti, entry in enumerate(transform_list):
                        tloc = f'{ploc}.transform[{ti}]'
                        if isinstance(entry, str):
                            name, arg, has_arg = entry, None, False
                        elif isinstance(entry, dict) and len(entry) == 1:
                            name, arg = next(iter(entry.items()))
                            has_arg = True
                        else:
                            warn(
                                tloc,
                                f"transform entry {entry!r} must be a string (e.g. 'upper') "
                                "or a single-key mapping (e.g. {round: 1})",
                            )
                            continue

                        # Transform names are case-insensitive at runtime (see
                        # transforms.apply_transforms); normalise before lookup
                        # so e.g. 'UPPER' / 'PascalCase' validate cleanly.
                        if isinstance(name, str):
                            name = name.lower()

                        if name not in KNOWN_TRANSFORM_NAMES:
                            warn(
                                tloc,
                                f"unrecognised transform '{name}' "
                                f"(known: {', '.join(sorted(KNOWN_TRANSFORM_NAMES))})",
                            )
                            continue

                        if name in REQUIRED_ARG_TRANSFORMS and not has_arg:
                            warn(
                                tloc,
                                f"transform '{name}' requires an argument, "
                                f"e.g. {{{name}: ...}}",
                            )
                        elif name in NO_ARG_TRANSFORMS and has_arg:
                            warn(
                                tloc,
                                f"transform '{name}' takes no argument -- use plain '{name}' "
                                "instead of a mapping",
                            )
                        elif name in OPTIONAL_NUMERIC_ARG_TRANSFORMS and has_arg:
                            if not isinstance(arg, (int, float)):
                                warn(
                                    tloc,
                                    f"transform '{name}' argument must be a number "
                                    f"(decimal places), got {arg!r}",
                                )
                        elif name in ('multiply', 'add', 'subtract', 'divide') and has_arg:
                            if not isinstance(arg, (int, float)):
                                warn(
                                    tloc,
                                    f"transform '{name}' argument must be a number, got {arg!r}",
                                )
                        elif name == 'replace' and has_arg:
                            if not isinstance(arg, dict) or 'from' not in arg:
                                warn(
                                    tloc,
                                    "transform 'replace' requires "
                                    "{replace: {from: ..., to: ...}}",
                                )
                        elif name in ('prefix', 'suffix', 'format') and has_arg:
                            if not isinstance(arg, str):
                                warn(
                                    tloc,
                                    f"transform '{name}' argument must be a string, got {arg!r}",
                                )

    # After walking rows/panels, statically validate any panels that referenced
    # top-level 'location' fields.
    if ref_key_to_paths or composite_panel_paths:
        loc_cfg = config.get('location')

        # Canonical set of main fields the runtime warns about
        MAIN_LOC_KEYS = ['city', 'region', 'region_code', 'country', 'country_code', 'postal', 'lat', 'lon']

        def _key_missing_in_loc(k: str) -> bool:
            if not isinstance(loc_cfg, dict):
                return True
            v = loc_cfg.get(k)
            return v is None or (isinstance(v, str) and v.strip() == '')

        # Warn for each referenced key that is missing in the config's top-level
        # location mapping. If location is not a mapping at all, warn once for
        # all referenced keys.
        if not isinstance(loc_cfg, dict):
            for k, paths in ref_key_to_paths.items():
                warn(
                    '(root)',
                    (
                        f"fact panels {', '.join(paths)} reference 'location.{k}' "
                        "but top-level 'location' is not a structured mapping; this may be "
                        "missing at runtime. Provide explicit '{k}: ...' under 'location' "
                        "or use lat/lon or an airport code."
                    ),
                )
            if composite_panel_paths:
                warn(
                    '(root)',
                    (
                        f"fact panels {', '.join(composite_panel_paths)} expect the whole 'location' dict "
                        "but top-level 'location' is not a mapping; provide structured fields or "
                        "lat/lon."
                    ),
                )
        else:
            # For structured location, warn per-key if missing
            for k, paths in ref_key_to_paths.items():
                if _key_missing_in_loc(k):
                    warn(
                        '(root)',
                        (
                            f"fact panels {', '.join(paths)} reference 'location.{k}' "
                            "but top-level 'location' mapping is missing this key or it is empty"
                        ),
                    )

            # For panels using the whole location dict, ensure at least city or lat/lon
            if composite_panel_paths:
                # composite usage expects the renderer to read either city or lat/lon
                missing_city = _key_missing_in_loc('city')
                missing_latlon = _key_missing_in_loc('lat') or _key_missing_in_loc('lon')
                if missing_city and missing_latlon:
                    warn(
                        '(root)',
                        (
                            f"fact panels {', '.join(composite_panel_paths)} expect 'location' to contain "
                            "at least 'city' or both 'lat' and 'lon', but none are present"
                        ),
                    )

            # Additionally, warn if the top-level location mapping is missing many
            # of the MAIN_LOC_KEYS (helpful for users using static examples)
            missing_main = [k for k in MAIN_LOC_KEYS if _key_missing_in_loc(k)]
            if len(missing_main) >= len(MAIN_LOC_KEYS) // 2 and len(missing_main) > 0:
                warn(
                    '(root)',
                    (
                        "top-level 'location' mapping is missing many expected keys: "
                        f"{', '.join(missing_main)}; runtime may show blanks in previews/labels"
                    ),
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
        ),
    )
    parser.add_argument(
        'configs', nargs='*', metavar='CONFIG',
        help='Path(s) to clockish YAML config file(s). Required.',
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
    args = parser.parse_args(argv)


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
