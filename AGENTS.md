# AGENTS.md

## Writing Style for This Codebase

Moderate terse. Fluff dies.  Sarcasm valued but not required, keep technical exactness.  Expletives bloody good.

Drop: articles, filler (just/really/basically), pleasantries, hedging, cheerleading.
Fragments OK. Short synonyms. Code unchanged.

Pattern: `[thing] [action] [reason]. [next step].`

**Active every response.** No drift into normal mode mid-conversation.

Git: commit/PR messages normal. Otherwise check before `git add` / `git commit`.

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

| File | Role |
|------|------|
| `display.py` | Renderer. Loads config, parses args, runs display loop. Panel renderers: clock, date, fact, text, wifi_graphic, divider, debug. |
| `render_preview.py` | PNG export (any platform). Stubs hardware; runs render pipeline offline. |
| `config_validator.py` | YAML schema + semantic validation. Three entry points: CLI, startup, file-based. |
| `drivers/` | Abstract `DisplayDriver` + three concrete implementations (ili9486, st7789, framebuffer). |
| `colors.py` | Named color palette lookup. |
| `platform_utils.py` | `is_raspberry_pi()`, `is_linux()`, `require_pi()` guards. |

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
    panels:
      - type: clock | date | fact | text | divider | wifi_graphic | debug | blank
        # common: color, font_size, font, width, background, justify
        # clock/date: timezone, time_format / date_format
        # fact: source (required), label
        # text: label

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

### System info sources

`_get_fact(source)` maps strings to lambdas:

| source | value |
|--------|-------|
| `ip` | first non-loopback IPv4 |
| `hostname` | system hostname |
| `uptime` | human-readable uptime |
| `cpu` | CPU usage % (delta /proc/stat) |
| `cpu_load` | 1-min load average |
| `mem` | memory % used |
| `disk` | disk % used (root fs) |
| `temp` | CPU temperature (zone0) |
| `ntp_status` | synchronized/unsync (chronyc/timedatectl) |
| `ntp_upstream` | number of upstream sources |
| `wireguard` | wg status (stubbed if no wg) |
| `wifi_*` | from `get_wifi_info()` tuple (status, ssid, signal_dbm, quality) |

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

### Workflows

**Local dev** (Windows):
```bash
pip install -e ".[dev]"
clockish-validate configs/clockish.yaml
clockish-preview configs/clockish.yaml  # outputs docs/previews/*.png
pytest
ruff check .
mypy src/clockish
```

**On Pi** (systemd service):
```bash
bash install.sh  # venv, system deps, run-clockish.sh, edit-clockish-config.sh
./run-clockish.sh --debug-layout  # single frame, layout debug output
./run-clockish.sh --install-service configs/my.yaml  # systemd unit + start
```

**Pre-commit** (GitHub Actions):
- yamllint + ruff + mypy + pytest + coverage

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

| Goal | File | Pattern |
|------|------|---------|
| Add color | `colors.py` | `BY_NAME['mycolor'] = '#rrggbb'` |
| Tweak layout | `configs/*.yaml` | height/width, row bg, panel fonts |
| Debug render | `--debug` flag | prints per-frame ms; `--debug-layout` one-frame exit |
| Fix config | `clockish-validate` | run before deploy; start supports non-fatal errors |
| Test preview | `clockish-preview` | outputs PNG offline; cross-platform |
