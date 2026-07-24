# AGENTS.md

## Writing Style for This Codebase

Moderate terse. Fluff dies.  Sarcasm valued but not required, keep technical exactness.  Expletives bloody good.

Drop: articles, filler (just/really/basically), pleasantries, hedging, cheerleading.
Fragments OK. Short synonyms. Code unchanged.

Pattern: `[thing] [action] [reason]. [next step].`

**Active every response.** No drift into normal mode mid-conversation.

Git: commit/PR messages normal. Otherwise check before `git add` / `git commit`.

Git pager: repo has `core.pager=cat` set locally (`.git/config`) -- git commands
never invoke `less`. Still use `--no-pager` / pipe to `cat` explicitly in any
new command for safety (e.g. a fresh clone won't have this local config set).

Remember: major pattern change require update AGENTS.md also

### File Modification Tracking (for AI agents & handoff)

**Always explicitly report which files you touched.** Silent modifications cause surprises at commit time.

**Pattern:**
1. **Per-edit**: After `replace_string_in_file`, `insert_edit_into_file`, or `create_file`, state:
   ```
   ✏️  Modified: src/clockish/display.py (line 600: added _render_url_fact_panel)
   ```
   or
   ```
   📄 Created: configs/url-fact-sample.yaml
   ```

2. **On tool failure** (partial state left): Flag it:
   ```
   ⚠️  Partial state: configs/url-fact-sample.yaml may have trailing blanks (fix attempt failed; user must clean)
   ```

3. **Session summary** (end of major feature): Print full list:
   ```
   ## FILES MODIFIED THIS SESSION
   - src/clockish/config_validator.py (added url-fact validation)
   - src/clockish/display.py (added url-fact renderer + cache)
   - tests/test_config_validator.py (added TestUrlFactPanel)
   - configs/url-fact-sample.yaml (created)
   - URL_FACT_GUIDE.md (created)
   ```

**Why**: Developers need to `git diff` / `git status` and catch changes before commit. Don't hide file touches.

---

## Codebase Overview

**clockish** — Raspberry Pi dashboard. YAML-driven layout, PIL rendering, pluggable display drivers (ILI9486, ST7789, framebuffer).

Runs in tight loop: `show_rows()` once/sec, renders rows → panels → PIL Image → `lcd.display()`.

### Entry points
- `clockish` CLI command → `src/clockish/display.py:main()`
- `clockish-preview` → `render_preview.py` (no hardware needed; stubs GPIO/SPI/etc.)
- `clockish-time-samples` → `render_time_samples.py` (one config, many synthetic clock/date moments; see below)
- `clockish-validate` → `config_validator.py` (YAML validation + schema check)

### Core flow

1. **Parse args** (`--debug`, `--debug-layout`, config path)
2. **Load config** → YAML dict
3. **Validate config** at startup (errors printed; non-fatal)
4. **Init hardware** → driver (ili9486/st7789/framebuffer) via `load_driver()`
5. **Load fonts** → PIL TrueType fonts (DejaVu default; custom via `fonts:` section)
6. **Pre-compute layout** → `_init_layout()` resolves row/panel widths, font sizes
7. **Main loop** → `show_rows()` per second until KeyboardInterrupt

### Key modules

| File                  | Role                                                                                                                            |
|-----------------------|---------------------------------------------------------------------------------------------------------------------------------|
| `display.py`          | Renderer. Loads config, parses args, runs display loop. Panel renderers: clock, date, fact, text, wifi_graphic, divider, debug. |
| `render_preview.py`   | PNG export (any platform). Stubs hardware; runs render pipeline offline.                                                        |
| `config_validator.py` | YAML schema + semantic validation. Three entry points: CLI, startup, file-based.                                                |
| `drivers/`            | Abstract `DisplayDriver` + three concrete implementations (ili9486, st7789, framebuffer).                                       |
| `colors.py`           | Named color palette lookup.                                                                                                     |
| `transforms.py`       | Value-transform registry (`upper`/`round`/`camelcase`/etc.) applied to panel text.                                              |
| `platform_utils.py`   | `is_raspberry_pi()`, `is_linux()`, `require_pi()` guards.                                                                       |

### Config structure

```yaml
orientation: portrait | landscape

default_font: DejaVuSans.ttf  # optional; used for named scales

fonts:                         # optional custom fonts
  my_font:
    file: DSEG7.ttf
    size: 20  # or "15%"

rows:
  - name: row-name
    height: 40  # pixels | float 0-1 | "15%"
    background: navy  # optional; default black
    font_behavior: default  # optional row-level default: default|scale|scale_numeric|stretch_y|stretch_x
    panels:
      - type: clock | date | fact | text | divider | wifi_graphic | debug | blank | url-fact
        # common: color, font_size, font, font_behavior, width, background, justify, padding
        # clock/date: timezone, time_format / date_format
        # fact: source (required), label
        # text: label
        # clock/date/fact/text/url-fact: transform (see below)

display:  # optional here; search display.yaml alongside config or ~/.config/clockish/
  driver: ili9486 | st7789 | framebuffer  # default ili9486
  width: 320
  height: 480
  rotation: 0 | 90 | 180 | 270
  # driver-specific keys (SPI pins, SKU, etc.) passed to constructor
```

### Layout pre-computation

`_init_layout()` runs once at startup:
- Resolves row heights (px | fraction | %)
- Resolves panel widths (px | fraction | "auto" → remainder / num_auto)
- Resolves font sizes (named scale `giant`/`huge`/`big`/`med`/`normal`/`small`/`tiny`/`micro`, or `auto` = 75% row height, or explicit `%` / px)
- Stores computed layout in `_LAYOUT` for fast per-frame access

Custom fonts (`font: my_font`) resolved at init; `font_size` determines final size.

### Panel rendering

All renderers: `(panel_dict, px, py, pw, ph, ...)` → draw on `ImageDraw`.

- **clock, date**: render time/date string centered or justified in rect
- **fact**: query system info (ip, hostname, cpu%, mem, disk, temp, ntp, wifi_*), format with label
- **text**: static label
- **divider**: horizontal line
- **wifi_graphic**: arc-based signal-strength display (0–4 bars + dot)
- **debug**: per-frame timings (prep, ntp, tz, draw, display ms)
- **blank**: reserved space, no draw

Text vertical-centering: `_center_y()` aligns ink baseline within row height.

### font_behavior (row-default / panel-override)

Text-drawing panels (`clock`/`date`/`fact`/`text`/`url-fact`) and rows support `font_behavior:`,
resolved once in `_init_layout()` (panel value > row default > `'default'`) and written back onto
the panel dict, so renderers just read `p['font_behavior']`. Values (`KNOWN_FONT_BEHAVIORS` in both
`display.py` and `config_validator.py` -- duplicated, not imported, to keep the validator free of
`display.py`'s hardware-driver imports):

- `default` -- unchanged: fixed `font_size:`, ink metrics from `"Ag|"` reference glyphs (assumes
  worst-case ascender+descender). Numeric-only content (clock, cpu%, temp) can look off-center
  since digits have no descenders.
- `scale` -- ignores `font_size:`'s resolved *size* (keeps its resolved *font file*, via
  `f.path`); every draw, `_fit_font()` binary-searches the largest point size where the text fits
  both the panel's width and height (aspect-preserving, since a single TrueType point size scales
  uniformly). Cached by `(font_path, text, avail_w, avail_h, axis, numeric)` so unchanged content
  across frames is free.
- `scale_numeric` -- same fit search as `scale`, but both `_fit_font()`'s ink measurement and the
  final vertical-centring ink metrics come from `_numeric_ink_metrics()` (`"0123456789"`) instead
  of the `"Ag|"` reference -- fixes off-center numeric-only content (clock, cpu%, temp) since
  digits have no descenders. `_numeric_ink_metrics()` caches once per font object in
  `_NUMERIC_INK_CACHE` (keyed by `id(font)`), never per-draw.
- `stretch_y` -- same as `scale` but constrained by height only; width may overflow/clip
  depending on `justify`.
- `stretch_x` -- fixed `font_size:` (height set once at load, like `default`); every draw,
  non-uniformly stretches the rendered glyphs horizontally to exactly fill the panel width.
  Unlike `scale`/`stretch_y` (uniform point-size change), Pillow has no API for anisotropic
  font scaling, so this renders to an offscreen RGBA image (`_draw_text_stretch_x()`) and
  resizes width-only (`Image.Resampling.BILINEAR`), then alpha-composites it onto the row's
  `Image` -- the one behavior that needs the actual `Image` object, not just `ImageDraw`,
  threaded through `_draw_text_line()` → each `_render_*_panel()` → `_dispatch_panel()` →
  `_render_row()` (`img=`/`target_img=` params, default `None`). Falls back to `default` if
  no `Image` is available (e.g. a caller that only has `ImageDraw`). `justify` is moot (always
  fills edge-to-edge); use `padding:` to inset instead.

### padding (universal panel attribute)

`padding:` (integer px, all 4 sides, default `1`) insets a panel's `(px, py, pw, ph)` rect
before dispatch to its type-specific renderer -- applied once in `_dispatch_panel()`, so it
works uniformly on every panel type (text-drawing or not; background fill still covers the
full, unpadded cell). Invalid values (negative, non-numeric) fall back to the default `1px`
(warned by `config_validator.py`, never fatal).

### Value transforms

`clock`, `date`, `fact`, `text`, `url-fact` panels support `transform:` -- an ordered list of
named operations applied to the panel's core string before any `label` prefix. Registry lives
in `transforms.py` (`TRANSFORM_REGISTRY`), shared by `display.py` (application) and
`config_validator.py` (name/arg validation). Built-ins: case (`upper`/`lower`/`title`/
`capitalize`/`titlecase`/`pascalcase`/`camelcase`/`strip`), rounding (`round`/`ceil`/`floor`/
`int`, string→float→int), arithmetic (`multiply`/`add`/`abs`), string ops (`replace`/`prefix`/
`suffix`), and a `format` escape hatch (raw Python format-spec). See `URL_FACT_GUIDE.md` for
full examples.

### System info sources

`_get_fact(source)` maps strings to lambdas:

| source         | value                                                            |
|----------------|------------------------------------------------------------------|
| `ip`           | first non-loopback IPv4                                          |
| `hostname`     | system hostname                                                  |
| `uptime`       | human-readable uptime                                            |
| `cpu`          | CPU usage % (delta /proc/stat)                                   |
| `cpu_load`     | 1-min load average                                               |
| `mem`          | memory % used                                                    |
| `disk`         | disk % used (root fs)                                            |
| `temp`         | CPU temperature (zone0)                                          |
| `ntp_status`   | synchronized/unsync (chronyc/timedatectl)                        |
| `ntp_upstream` | number of upstream sources                                       |
| `wireguard`    | wg status (stubbed if no wg)                                     |
| `wifi_*`       | from `get_wifi_info()` tuple (status, ssid, signal_dbm, quality) |

### Display drivers

Abstract base: `DisplayDriver.begin()`, `.display(PIL_Image)`, `.close()`, `.idle(bool)`, `.dimensions` property.

**ili9486Driver** (Raspberry Pi SPI):
- Opens pyili9486 + spidev + rpi-lgpio
- Reads config: rotation, SKU (MPI3501/MHS3528), SPI bus/device/speed, GPIO pins (DC, RST)
- Fails fast if hardware missing

**ST7789Driver** (Pimoroni; Adafruit 240×135/240×240):
- Similar; uses st7789 lib + gpiod

**FramebufferDriver** (Linux /dev/fb0):
- Reads /dev/fb0 geometry via ioctl
- Supports 16-bpp (RGB565) and 32-bpp (XRGB/ARGB)
- Suppresses console cursor via KD_GRAPHICS ioctl
- Cross-platform (HDMI, DSI ribbons, any /dev/fb*)

### Testing & validation

**pytest**: `tests/test_config_validator.py` (623 lines), `test_platform_utils.py`, `test_all_encoding.py`.

Run:
```bash
pytest --cov=src/clockish
```

**Config validation** (3 entry points):
1. CLI: `clockish-validate --strict my-config.yaml`
2. Startup: `_init()` calls `validate_config_dict()` on loaded config (errors printed, non-fatal)
3. File-based: `validate_config_file(path, run_yamllint=True)`

Validation layers:
1. yamllint (YAML syntax/style)
2. PyYAML parse
3. jsonschema (structural: orientation, rows, panel types required)
4. Semantic walker (deprecated keys, unknown attrs, fact source checks, font misuse)

### Platform quirks

**Windows dev**: `render_preview.py` stubs hardware modules before import. `platform_utils.is_raspberry_pi()` returns False; GPIO code guarded.

**Non-Linux strftime**: `render_preview.py` replaces `%-d` (no-pad) with `%d` (zero-padded) on Windows/macOS.

**Font fallback**: `render_preview.py` tries C:\Windows\Fonts\*.ttf, /System/Library/Fonts, /usr/share/fonts before PIL default.

### Preview rendering: live vs mock

`clockish-preview` renders every config **twice**:

| output | mode | time/date | cpu% | uptime | hostname / IP / wifi SSID |
|--------|------|-----------|------|--------|---------------------------|
| `docs/previews/{name}.png` | live | real, per-panel timezone (`_ppd._now_in_tz`) | fixed `_LIVE_CPU_PERCENT` (42.7) | real (`get_uptime_str()`, unstubbed `/proc/uptime`) | always mocked |
| `docs/previews/mock/{name}.png` | mock | fixed `_PREVIEW_NOW` (2028-12-20 22:08:08 -- see comment at definition, worst-case digit-width for both 24h and no-pad-12h formats, plus longest weekday/month) | fixed `_MOCK_CPU_PERCENT` (100.0, worst-case width) | fixed `_MOCK_UPTIME_STR` | always mocked |

`docs/previews/*.png` is tracked in git (so a tagged release ships previews
that look close to "now"); `docs/previews/mock/` is gitignored (local dev/
review artifact for spotting layout regressions via a deterministic,
comparable render -- not meant to be committed).

cpu% is fixed in **both** modes (just a different constant) rather than read
live -- real usage changes every render and is noisy/non-reproducible for
git-diffing the tracked `docs/previews/*.png` set. Only time/date (mock only)
and uptime (live only) read anything dynamic.

`--skip-live` / `--skip-mock` CLI flags render only one set. A manual-stage
pre-commit hook (`pre-commit run --hook-stage manual clockish-preview`)
regenerates both -- not run automatically on every commit (slow, and the
live set changes every time regardless of code changes).

### Time-sample rendering (exploratory layout checks)

`clockish-time-samples <config.yaml>` (`render_time_samples.py`) renders ONE
config across a curated set of synthetic clock/date moments -- for eyeballing
how a layout handles the full range of digit widths, 12h/24h hour formats,
and weekday/month name lengths, not just the single worst-case moment
`clockish-preview`'s mock mode uses. No default config -- pass one explicitly
(e.g. run it once against a 12h config like `nixie.yaml` and once against a
24h config like `nixie24.yaml` to compare both side by side).

Reuses `render_preview.render_config()` unchanged (same hardware stubs, same
`mock=True` code path) -- this script only overrides `render_preview`'s
module-level `_PREVIEW_NOW` before each frame instead of leaving it fixed.

- `_SAMPLE_TIMES`: 12 curated `(hour24, minute)` pairs spanning narrow 12h
  hours (`1:17`), wide 24h/12h hours (`20:00`, `23:59`), midnight/noon edge
  cases, and ordinary middle-of-the-day times. `_assert_digit_coverage()`
  (run every call) guarantees every digit 0-9 appears in at least one
  sample's 24h hour, no-pad 12h hour, or zero-padded minute -- raises if the
  list is ever edited down to a set that loses coverage.
- `_SAMPLE_DATES`: 8 `(year, month, day)` tuples cycled round-robin across
  the time samples (not a single fixed date) so date-format widths
  (short/long weekday & month names) get exercised too.

Output: `docs/previews/time-samples/{config-name}/{HH}-{MM}.png` --
gitignored (ad-hoc exploratory artifact, like `docs/previews/mock/`).

### Workflows

**Local dev** (Windows):
```bash
pip install -e ".[dev]"
clockish-validate configs/clockish.yaml
clockish-preview configs/clockish.yaml  # outputs docs/previews/*.png + docs/previews/mock/*.png
clockish-time-samples configs/nixie.yaml  # outputs docs/previews/time-samples/nixie/*.png
pytest
ruff check .
mypy src/clockish
```

**Before raising a PR**: run everything CI runs (pre-commit + pytest + ruff + mypy,
in that order) with one script:
```bash
bash scripts/pre-pr-check.sh
```
Hard-fails on pre-commit/pytest/mypy issues; reports (but doesn't fail on) ruff
findings, matching CI's `ruff check . || true`.

**On Pi** (systemd service):
```bash
bash install.sh  # venv, system deps, run-clockish.sh, edit-clockish-config.sh
./run-clockish.sh --debug-layout  # single frame, layout debug output
./run-clockish.sh --install-service configs/my.yaml  # systemd unit + start
```

**Pre-commit** (GitHub Actions):
- yamllint + ruff + mypy + pytest + coverage
- CI runs the full suite across a matrix: **Python 3.11, 3.12, 3.13** (see
  "Supported Python versions" below).

### Supported Python versions

`requires-python = ">=3.11"` (open floor, no ceiling). Two real deployment
targets drive this:

- **3.11** -- Raspberry Pi OS "bookworm" (Debian 12), the floor.
- **3.13** -- Raspberry Pi OS "trixie" (Debian 13), current as of this writing.

**3.12** is also matrixed in CI for broader contributor-environment coverage
(e.g. it's Ubuntu 24.04's default `python3`) even though it isn't itself a
deployment target.

Local dev does **not** need to match 3.11 exactly -- develop against whatever
interpreter is convenient (e.g. a stock 3.12 venv); the CI matrix above is the
actual gate for 3.11/3.13 compatibility, so a single local version doesn't
need to carry that burden. This only works cleanly because of two things
that keep the versions from silently diverging:

- `[tool.mypy] python_version = "3.11"` in `pyproject.toml` pins mypy's
  *type-checking target* to the floor regardless of which interpreter
  actually runs mypy -- so a 3.12 (or any) local venv still gets the same
  3.11-accurate type check as CI's 3.11 job.
- `numpy` is capped (`numpy<2.5`, dev extras only) because numpy 2.5.0 ships
  typing stubs using PEP 695 `type X = ...` syntax unconditionally, which
  mypy refuses to parse under `--python-version 3.11` on ANY interpreter --
  a mypy/numpy-stub compatibility gap, not a runtime issue. Without this cap,
  a local venv on a newer interpreter can silently resolve a newer numpy than
  CI does and hit a spurious mypy failure that has nothing to do with real
  code changes. End users installing plain `clockish` (no `[dev]` extra) are
  not constrained by this cap.

If `mypy src/clockish` (or `scripts/pre-pr-check.sh`) ever fails locally in a
way that doesn't reproduce in CI, suspect a dependency-resolution difference
between the local venv's interpreter and CI's, not a real 3.11 incompatibility
-- check `pip list` for anything with a version-gated stub/syntax requirement
newer than 3.11, the same way the numpy issue above was diagnosed.


### Deprecations & patterns

- **Old**: `time_font: big` → **New**: `font_size: big`
- **Old**: `colors: {time: red, label: grey}` → **New**: `color: red` (per panel)
- **Old**: `font: small` (scale name) → **New**: `font_size: small` (warn on mismatch)
- **New pattern**: `fonts: {dseg7: {file: DSEG7.ttf}}` + `font: dseg7` (custom TTF) + `font_size: auto`

### Extending

**Add display driver**:
1. Create `src/clockish/drivers/mydriver.py`, subclass `DisplayDriver`
2. Add entry to `_DRIVER_REGISTRY` in `drivers/__init__.py`
3. Users select via `driver: mydriver` in YAML `display:` section

**Add panel type**:
1. Add type to `KNOWN_PANEL_TYPES` in `config_validator.py`
2. Add attrs to `_PANEL_TYPE_ATTRS[new_type]`
3. Create `_render_new_panel()` in `display.py`
4. Add dispatch in `_dispatch_panel()`
5. Test in `test_config_validator.py`

**Add fact source**:
1. Add to `KNOWN_FACT_SOURCES` in `config_validator.py`
2. Add lambda to `_get_fact()` dict in `display.py`
3. Optional: add default label to `_FACT_DEFAULT_LABELS`
4. Test in validator tests

**Add a value transform**:
1. Write `_t_<name>(value: str, arg) -> str` in `transforms.py`, add to `TRANSFORM_REGISTRY`
2. Classify it in `NO_ARG_TRANSFORMS` / `REQUIRED_ARG_TRANSFORMS` /
   `OPTIONAL_NUMERIC_ARG_TRANSFORMS` (used by validator's arg-shape checks)
3. Add arg-shape validation case in `config_validator.py` if it needs custom checks (e.g. `replace`)
4. Document in `URL_FACT_GUIDE.md` transforms table
5. Test in `test_transforms.py` + `test_config_validator.py::TestTransform`

**Add a font_behavior**:
1. Add name to `KNOWN_FONT_BEHAVIORS` in **both** `display.py` and `config_validator.py`
   (duplicated on purpose -- see font_behavior section above)
2. Implement the drawing logic in `_draw_text_line()` in `display.py`
3. Test in `test_display_fonts.py` + `test_config_validator.py::TestFontBehavior`

---

## Code Conventions & Linter Notes

### Import ordering (Ruff)
Ruff enforces PEP 8 import grouping:
1. Standard library (alphabetical)
2. Blank line
3. Third-party (alphabetical)
4. Blank line
5. Local application (alphabetical)

Example:
```python
import argparse
import datetime
import os

from PIL import Image
import yaml

from clockish.colors import BY_NAME
```

Ruff auto-flags unsorted imports. Reorganize them to fix `unsorted-imports` warnings.

---

## Common edits

| Goal         | File                | Pattern                                              |
|--------------|---------------------|------------------------------------------------------|
| Add color    | `colors.py`         | `BY_NAME['mycolor'] = '#rrggbb'`                     |
| Tweak layout | `configs/*.yaml`    | height/width, row bg, panel fonts                    |
| Debug render | `--debug` flag      | prints per-frame ms; `--debug-layout` one-frame exit |
| Fix config   | `clockish-validate` | run before deploy; start supports non-fatal errors   |
| Test preview | `clockish-preview`  | outputs PNG offline; cross-platform                  |
| Time-sample layout check | `clockish-time-samples` | outputs PNG offline; one config, many clock/date moments |
| Add transform| `transforms.py`     | `TRANSFORM_REGISTRY['myop'] = _t_myop`               |
