# AGENTS.md

## Writing Style for This Codebase

Moderate terse. Fluff dies.  Sarcasm valued but not required, keep technical exactness.  Expletives bloody good.

Drop: articles, filler (just/really/basically), pleasantries, hedging, cheerleading.
Fragments OK. Short synonyms. Code unchanged.

Pattern: `[thing] [action] [reason]. [next step].`

**Active every response.** No drift into normal mode mid-conversation.

**Git workflow: propose, don't execute**
- **Never commit**: propose the commit message, user commits
- **Never push**: user handles all pushes
- Proposing commit/PR messages is fine, but always check with the human before a commit or opening a PR -- including the message text itself
- always check before creating new worktree, ask user for worktree/branch names
- Otherwise check before `git add` / `git commit` / `git push`

**No AI attribution or session identifiers in commits/PRs**
- Never add `Co-Authored-By: Claude` (or any AI co-author trailer) to a commit
  message or PR body. This is not an attribution-required project: the work is
  the maintainer's, done under a paid service.
- Never include a session URL, session ID, or any similar agent-run identifier
  anywhere in a commit message, PR body, code comment, or committed file.
  Treat it as an information leak, not a convenience link.
- The same goes for "Generated with ..." footers and tool banners. Commit
  messages describe the change, nothing else.

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
- `clockish-location` → `setup_location.py` (writes `~/.config/clockish/location.yaml`)

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
| `backlight.py`        | Optional `display.backlight:` brightness scheduler -- background thread, sysfs writer.                                          |
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

cached-facts:                  # optional; background-thread-fetched remote data
  - name: Denver-Weather       # referenced by fact panels as source: cached-facts.Denver-Weather
    type: url-fact             # fetcher kind; only 'url-fact' (HTTP GET) supported today
    url: https://api.open-meteo.com/v1/forecast?...
    interval: 20m              # fetch frequency; default 5m
    timeout: 5                 # optional; HTTP timeout seconds, default 5
    verify_ssl: true            # optional; TLS verification, default true
    preview_response: '{...}'  # optional; used verbatim by clockish-preview instead of a real fetch

rows:
  - name: row-name
    height: 40  # pixels | float 0-1 | "15%"
    background: navy  # optional; default black
    font_behavior: default  # optional row-level default: default|scale|scale_numeric|stretch_y|stretch_x
    panels:
      - type: clock | date | fact | text | divider | wifi_graphic | debug | blank
        # common: color, font_size, font, font_behavior, width, background, justify, padding
        # clock/date: timezone, time_format / date_format
        # fact: source (required) -- built-in (ip, cpu, mem, ...) or 'cached-facts.<name>'
        # fact + cached-facts source: json_path or pattern (exactly one) to extract a field
        # text: label
        # clock/date/fact/text: transform (see below)

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
- **fact**: query system info (ip, hostname, cpu%, mem, disk, temp, ntp, wifi_*) OR extract a
  field (via `json_path`/`pattern`) from a `cached-facts.<name>` background-fetched source,
  format with label
- **text**: static label
- **divider**: horizontal line
- **wifi_graphic**: arc-based signal-strength display (0–4 bars + dot)
- **debug**: per-frame timings (prep, ntp, tz, draw, display ms)
- **blank**: reserved space, no draw

Text vertical-centering: `_center_y()` aligns ink baseline within row height.

### font_behavior (row-default / panel-override)

Text-drawing panels (`clock`/`date`/`fact`/`text`) and rows support `font_behavior:`,
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
  digits have no descenders. `_numeric_ink_metrics()` caches once per `(font_path, font_size)` in
  `_NUMERIC_INK_CACHE` -- **not** `id(font)` (bug history: a previous `id(font)`-keyed version
  could alias two *different* fonts once Python reused a GC'd throwaway font object's memory
  address, causing non-deterministic mis-sizing/clipping that varied between otherwise-identical
  runs; `(path, size)` is a pure function of the inputs and can't alias).
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

#### Reference-text sizing (why `scale`/`scale_numeric`/`stretch_y` don't jitter)

`_fit_font()`'s binary search measures whatever string it's told to. Measuring the panel's
*actual per-frame text* means the fitted point size can legitimately change frame-to-frame just
because the text's character count or glyph advance widths changed -- e.g. a no-pad 12h hour
going from `"1:17"` (4 chars) to `"12:09"` (5 chars) -- visible as the clock digits visibly
growing/shrinking every time the hour crosses a 1-digit/2-digit boundary, or (combined with the
old `id(font)` cache bug above) outright clipping on some renders and not others.

`_draw_text_line()` accepts a `measure_text:` param, used INSTEAD OF the real `text` for the fit
search only (the real `text` is still what's drawn/positioned). Callers compute a synthetic
worst-case reference so the fitted size is CONSTANT for the life of the process (same
`_FIT_FONT_CACHE` key every frame -> same cached font object, not just the same point size):

- **clock panels**: `_clock_reference_text(fmt, font_path, transform)` parses the resolved
  `time_format` strftime string via `_expand_strftime_worst_case()`, substituting each digit
  directive (`%H`/`%M`/`%S`/`%I`/`%-I`/`%d`/`%m`/`%y`/`%Y`) with *this font's own widest digit*
  (`_widest_char()`, measured once via `getbbox()` at a fixed probe size -- TrueType advance
  widths scale linearly with point size, so relative glyph width ordering is size-independent)
  repeated to that directive's max digit count (always the worst case, regardless of pad --
  e.g. `%-I` uses 2 digits even though real values are sometimes 1), and `%p` with whichever of
  `"AM"`/`"PM"` is wider (`_widest_string()`). The synthetic string is then run through the exact
  same `.upper()` + `apply_transforms()` pipeline the real value gets, so `transform: [lower]`
  correctly re-widens the reference for a lowercase `p` descender. Returns `None` (falls back to
  measuring the live text, old behavior) if the format contains a directive it can't model this
  way (weekday/month names -- `%a`/`%A`/`%b`/`%B`; their width varies far more than digit
  substitution can account for). **`date` panels are not covered** -- same reason, out of scope
  for now; they still measure their live text.
- **fact panels**: no format string to parse, so `_generic_numeric_reference()` just
  repeats this font's widest character among `"0123456789.-%"` to the *current* text's length.
  Stable against digit-glyph-width variance, but still shifts if the value's character count
  itself changes (e.g. `"9.9%"` -> `"100.0%"`) -- an inherent limit of not having a format string
  to derive a true worst-case length from.
- **text panels**: not covered (arbitrary label content, not numeric) -- always measure the live
  text, same as before.



`padding:` (integer px, all 4 sides, default `1`) insets a panel's `(px, py, pw, ph)` rect
before dispatch to its type-specific renderer -- applied once in `_dispatch_panel()`, so it
works uniformly on every panel type (text-drawing or not; background fill still covers the
full, unpadded cell). Invalid values (negative, non-numeric) fall back to the default `1px`
(warned by `config_validator.py`, never fatal).

### Value transforms

`clock`, `date`, `fact`, `text` panels support `transform:` -- an ordered list of
named operations applied to the panel's core string before any `label` prefix. Registry lives
in `transforms.py` (`TRANSFORM_REGISTRY`), shared by `display.py` (application) and
`config_validator.py` (name/arg validation). Built-ins: case (`upper`/`lower`/`title`/
`capitalize`/`titlecase`/`pascalcase`/`camelcase`/`strip`), rounding (`round`/`ceil`/`floor`/
`int`, string→float→int), arithmetic (`multiply`/`add`/`subtract`/`divide`/`abs`), unit
conversion (`celsius_to_fahrenheit`/`fahrenheit_to_celsius`, no-arg), string ops
(`replace`/`prefix`/`suffix`), and a `format` escape hatch (raw Python format-spec). See
`URL_FACT_GUIDE.md` for full examples.

### System info sources

`_get_fact(source)` maps strings to lambdas -- used when a `fact` panel's `source:` is one of
the built-ins below. `source: cached-facts.<name>` (see "cached-facts" section further down)
bypasses this entirely and instead extracts a field (`json_path`/`pattern`) from a
background-thread-fetched raw value:

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
| `backlight`    | current brightness as a whole % of the configured `min`..`max`   |
| `wifi_*`       | from `get_wifi_info()` tuple (status, ssid, signal_dbm, quality) |
| `location`     | resolved location as "City, Country (lat,lon)"; empty when disabled |
| `location.*`   | one field of it (`city`, `region`, `country_code`, `lat`, ...)    |
| `daytime`      | 'true'/'false' from real sunrise/sunset; static rule without a location |
| `nighttime`    | inverse of `daytime`                                             |

### cached-facts (background-thread-fetched remote data)

Top-level `cached-facts:` list -- each entry is fetched **out of the render loop entirely**, on
its own background daemon thread, decoupling slow/flaky network I/O from `show_rows()`'s
once-per-second cadence (the old `url-fact` panel type fetched synchronously inline in the
render loop; it has been removed -- no backward compatibility).

```yaml
cached-facts:
  - name: Denver-Weather        # referenced by panels as source: cached-facts.Denver-Weather
    type: url-fact               # fetcher kind; only 'url-fact' (HTTP GET) supported today
    url: https://api.open-meteo.com/v1/forecast?...
    interval: 20m                 # fetch frequency; default 5m
    timeout: 5                    # optional; HTTP timeout seconds, default 5
    verify_ssl: true               # optional; TLS cert verification, default true
    preview_response: '{...}'     # optional; used verbatim by clockish-preview, no real fetch
```

A `fact` panel consumes an entry via `source: cached-facts.<name>` plus exactly one of
`json_path`/`pattern` to extract the field it needs -- multiple panels can pull different
fields out of ONE shared fetch (e.g. temperature in both °F and °C via `transform:`, plus
wind speed/direction elsewhere, from a single weather API call).

**Mechanics** (`display.py`):
- `_init_cached_facts(config)` (called from `_init_layout()`) spawns one daemon
  `threading.Thread` per entry (`_cached_fact_worker()`), staggered across the interval window
  so N entries don't all hit the network at once.
- Each worker writes `_cached_facts_cache[name] = {'raw': str|None, 'ok': bool}` -- the **whole
  dict is replaced**, never mutated in place, so a plain read from the render loop
  (`_render_fact_panel()`) is atomic under the GIL without needing a lock.
- On fetch failure, the previous `raw` value is kept (never cleared) and the retry backs off:
  starts at `interval/10` (min 1s), doubles each consecutive failure, capped at the full
  `interval` -- a transient outage retries soon; a persistent one settles to normal cadence.
- `SIGUSR1` -> `_handle_sigusr1()` sets every entry's `threading.Event`, waking its worker
  immediately for a fresh fetch (instead of waiting out the current interval/backoff).
- Extraction (`json_path`/`pattern`) happens in `_render_fact_panel()`, **every render**, not
  in the worker thread -- cheap (no I/O), and lets multiple panels share one fetch.
- Before a cached-fact's first successful fetch (or if it has never once succeeded), the
  consuming `fact` panel renders an empty string -- there is no `fallback:` key (unlike the
  old `url-fact` panel).

**Preview mode** (`clockish-preview`/`clockish-time-samples`, `_PREVIEW_MODE=True`): no
background threads (a preview render is one-shot). If `preview_response` is set, it's used
verbatim (fully offline/deterministic). If not, `_init_cached_facts()` fetches once,
synchronously, before the frame renders -- a real network call is allowed here (per design),
it just has to complete before rendering, not run in the background.

### location (privacy-first, off by default)

Top-level `location:` drives weather coords, sunrise/sunset and the `location.*`
/ `daytime` / `nighttime` fact sources. **The governing rule: no location
network call happens unless the user asked for one.** Anything added here must
keep that true.

Forms: `disabled`/`none`/`off`/`false` (kill switch), `auto` (GeoIP at
ipwho.is), a 3-4 letter airport code, `"lat,lon"`, or a mapping (airport code,
or lat+lon, or city+region+country). Precedence: config > user-owned
`~/.config/clockish/location.yaml` > runtime `location-cache.yaml` > disabled.
An invalid setting disables rather than guessing -- a bare 3-4 letter scalar is
an airport lookup, so a typo must not be passed through to the API.

Mechanics (`display.py`):

- `_init_system_location(config, force=False)` resolves once per run (keyed on
  the config value + preview mode); the reload path calls
  `_invalidate_location_resolution()` first. It is reached from `_init_layout()`
  only -- do not add a second call site.
- The kill switch runs **first**, before lat,lon parsing and before the airport
  regex: `none`/`off` are themselves 3-4 alpha chars.
- `_disable_location()` is the single OFF state: `_SYSTEM_LOCATION = None`, sun
  worker stopped, cache file deleted. There is no 0,0 sentinel; `_location_enabled()`
  gates every sun-times start site and rejects 0,0.
- Every resolution branch ends at `_resolve_sun_times_for()` (one-shot in
  preview, background worker live). A branch that returns without calling it
  silently leaves `daytime`/`nighttime` on the static fallback.
- `_OFFLINE` (`--offline` / `CLOCKISH_OFFLINE=1`) short-circuits `_fetch_url_raw()`
  itself -- the one choke point every fetch passes through.
- Files: canonical shape is one top-level `location:` key (scalar `disabled` or a
  mapping), mode 0600. The runtime writes **only** `location-cache.yaml`
  (stamped `source` + `resolved_at`, GeoIP entries expire after 30 days);
  `location.yaml` belongs to the user and is written solely by
  `clockish-location` (`clockish/setup_location.py`).
- Debug: exact coordinates, API query strings and raw payloads are gated behind
  `--debug-location` / `CLOCKISH_DEBUG_LOCATION=1`. Plain `--debug` rounds to
  ~11km. Do not print raw coordinates in new debug lines.

Preview modes: `_PREVIEW_LOCATION_MODE` is `contrib` (default -- resolves from
`tests/samples/*.json`, no network, no cache write; what `docs/previews/*.png`
is generated with) or `personal` (`clockish-preview --personal`, real lookups,
output to gitignored `docs/previews/personal/`).

Validation: `config_validator.validate_location_value()` /
`location_value_warnings()` are shared by the validator, `display.py` and
`setup_location.py` -- one definition of a valid location. Unlike the
`KNOWN_FONT_BEHAVIORS` duplication, this one **is** imported (the validator has
no hardware-driver imports; the dependency only runs one way).

Tests: `tests/test_location.py`. Any test that could reach the network
monkeypatches `_fetch_url_raw` and asserts the recorded call list is empty --
keep that pattern, or "makes no network call" stops being a real assertion.

### Backlight scheduling

Optional `backlight:` block under a display profile's `display:` section (see
`configs/display/framebuffer.yaml` for a full worked example). Runs a background daemon thread
(`clockish/backlight.py`) that computes a brightness level roughly every 10 minutes -- from
EITHER a fixed time-of-day `schedule:` OR a sun-following `curve: sun` (mutually exclusive, pick
one) -- and writes it to the backlight hardware, decoupled from the render loop the same way
`cached-facts` is.

```yaml
display:
  backlight:
    method: sysfs        # how the backlight is driven; only 'sysfs' exists today
    device: 10-0045      # folder under /sys/class/backlight/ -- NOT a full path
    logging: true        # optional; log every actual brightness change, default false
    off_value: 0         # fully off. NOT named 'off' -- see gotcha below
    min:       40
    max:       255

    # EITHER a fixed schedule:
    schedule:
      - name: night
        start: "22:00"   # 24h "HH:MM"; end < start wraps past midnight
        end:   "06:59"
        value: 42
      - name: day
        start: "07:00"
        end:   "19:59"
        value: 255

    # OR a sun-following curve (mutually exclusive with schedule: above):
    # curve: sun
```

**Why `off_value` and not `off`**: an unquoted `off:` YAML key is parsed under YAML 1.1 (PyYAML's
default) as the boolean `False`, not the string `"off"` -- silently corrupting the config (the
dict ends up with a `False` key instead of `'off'`). This isn't a style preference; it's the same
class of gotcha as `yes`/`no`/`on`/`off`/`true`/`false` all being reserved boolean words. Never
add a bare `off:`/`on:`/`yes:`/`no:` key to any clockish config schema -- quote it or rename it.

**Mechanics** (`backlight.py`):
- `resolve_scheduled_value(schedule, min_, max_, now)` -- pure function, no I/O. Matches `now`
  against each entry's `start`/`end` (inclusive both ends; `end < start` wraps past midnight).
  Time not covered by any entry falls back to `round((min_ + max_) / 2)` (always an `int`).
- `resolve_sun_curve_value(sunrise, sunset, min_, max_, now, twilight_minutes, peak_fraction)` --
  pure function for `curve: sun`. An **eased trapezoid**, not a bump: the display should sit at
  `max_` for most of the day and pass through the in-between levels as briefly as looks natural.

  ```
  min_ ______/^^^^^^^^^^^^^^^^^^^^^\______ min_
            ^         plateau       ^
  sunrise - twilight             sunset + twilight
  ```

  - `min_` before `sunrise - TWILIGHT_MINUTES` and from `sunset + TWILIGHT_MINUTES` on.
  - Ramp up from there to `max_` at `PEAK_FRACTION` of the way from sunrise to sunset;
    mirrored ramp down over the last `PEAK_FRACTION`, finishing at `min_` past sunset.
  - Flat `max_` in between -- the middle 50% of daylight at the defaults.

  `TWILIGHT_MINUTES = 50` (module constant, overridable per call): the sky is already usefully
  light before the sun clears the horizon and still light after it drops below, so a backlight
  keyed strictly to sunrise/sunset lags the actual room. Roughly the end of nautical twilight at
  mid latitudes. `PEAK_FRACTION = 0.25` gives the half-day plateau; it's clamped below `0.5`,
  where the two ramps would cross and there'd be no plateau left.

  Each ramp is a **half**-period cosine (`(1 - cos(pi*u)) / 2`, `u` = fraction of THAT RAMP
  elapsed), which has zero slope at both ends -- leaving the flat night level and meeting the
  flat plateau with no visible kink at either junction. Applying that same half-period cosine
  across the whole sunrise-to-sunset span instead of per ramp is the classic mistake here: it
  yields a monotonic all-day climb that only reaches `max_` at sunset. An earlier version used a
  full-period cosine bump across all of daylight (`1 - cos(2*pi*t)`, peaking at solar noon) --
  correct-looking but it holds `max_` for about a minute a day, which is not what the feature is
  for. If this curve ever looks like a smooth hill rather than a flat-topped plateau, one of
  those two shapes has crept back in.
- `curve: sun` needs today's sunrise/sunset. Rather than importing `display.py` (circular --
  it already imports `backlight.py` -- and it'd pull in hardware-driver code this module has no
  business depending on), `start_backlight(display_cfg, get_sun_times=...)` takes a callable;
  `display.py`'s `_get_today_sun_times()` reads its own `_SUN_TIMES` global and is passed in at
  the call site. Returns `None` before the first sunrise/sunset fetch completes, in which case
  `_apply()` falls back to the `min`/`max` midpoint (same fallback shape as a schedule gap).
- `method:` is a dispatch key (`_METHODS` dict) so other control schemes (GPIO pin toggle,
  PWM, etc.) can be added later without reshaping the module -- only `'sysfs'` is implemented
  today, which writes the integer to `/sys/class/backlight/<device>/brightness`.
- `_backlight_worker()` mirrors `display.py`'s `_sun_times_worker()` threading pattern exactly:
  a daemon thread looping on `event.wait(timeout=600)`, woken early by config reload.
- `start_backlight(display_cfg, get_sun_times=None)` (called from `_init_layout()`, so it
  re-applies on every config reload too) applies the current value **synchronously before
  returning** -- a restart never leaves the backlight at a stale level (e.g. full brightness at
  2am) until the first 10-minute tick.
- Writes are de-duplicated: `_apply()` only touches sysfs (and only logs, if `logging: true`)
  when the computed value actually changed since the last write.
- `config_validator.py` validates the whole block: required keys, `method` enum, 0-255 ranges,
  `min <= max`, exactly one of `schedule`/`curve` present, `curve` enum, `HH:MM` format, and
  schedule-entry overlap (midnight-wrap aware).

**`fact: backlight`**: `current_percent()` reports where the panel sits in its own configured
`min`..`max` span -- `min` is 0%, `max` is 100%, whole numbers only (`min: 2`, `max: 255`, level
129 -> `50%`). Deliberately NOT a percentage of 0..255: the useful range is what was configured,
and a display whose `min: 40` is its dimmest legible level should read 0% there, not 16%.
`current_value()` reads the level back from the device (`_READERS`, the read-side twin of
`_METHODS`) rather than trusting `_last_written_value`, so a level changed outside clockish shows
up honestly; it falls back to the last written value when the device can't be read. Levels outside
the span clamp to 0/100%. The panel renders empty when no `backlight:` block is configured --
`_active_cfg` (set by `start_backlight()`, cleared by `stop_backlight()`) is what the fact reads
min/max/device from.

**Verified against real hardware** (SSH to a Pi with a `/sys/class/backlight/10-0045/brightness`
DSI panel): `_write_sysfs()` and the full `start_backlight()`/`stop_backlight()` lifecycle both
confirmed working against the actual sysfs file (readback matched every write), including the
`logging: true` message. No `sudo` needed -- the `video` group already has write access to the
brightness file.

**Simulated-day runner** (`scripts/backlight_hardware_test.py`): replays a whole day against the
real panel in ~2.5 minutes -- one wall-second per tick, each tick advancing a simulated clock by 10
simulated minutes (the worker's own cadence), running the REAL `backlight._apply()` + sysfs write
for that moment AND a REAL `display.show_rows()` frame with the simulated time injected, so the
clock on screen agrees with the brightness being watched. Prints a per-tick bar, then checks the
collected samples (night == min, a flat `max` plateau centred on solar noon and covering ~50% of
daylight, monotonic ramps either side, every sysfs readback matched) and exits non-zero on failure
-- the plateau checks are what catch the wrong-curve-shape regressions described above.

Before the replay it prints a **location provenance block** -- the setting and which file it came
from (config > `~/.config/clockish/location.yaml` > runtime cache), what it resolved to and by
which method, the cache file's own stamp, and the sun times in use (fetched, or injected via
`--sunrise/--sunset`). `curve: sun` is only as good as the sun times behind it, and the resolution
chain is deliberately quiet, so a replay should never leave you guessing which location it used.
Coordinates follow the same gating as the rest of clockish: ~11 km rounding unless
`--debug-location` is passed.

```bash
python3 scripts/backlight_hardware_test.py configs/my.yaml      # the day, on real hardware
python3 scripts/backlight_hardware_test.py --dry-run --no-frames \
        --tick-secs 0 --sunrise 06:22 --sunset 19:48            # instant, offline, dev box
python3 scripts/backlight_hardware_test.py --checks-only        # old unit-level hw checks
```

Time injection lives entirely in that script (it monkeypatches `display._now_in_tz`,
`get_daytime`, `get_nighttime`, and hands `display._init()` a synthetic `sys.argv`). The only
accommodation in shipped source is the optional `now` parameter on `backlight._apply()` /
`_resolve_value()` -- defaulting to `None` (= real now), so `_backlight_worker` is unchanged.
Keep it that way: a new backlight behaviour should stay drivable by passing a `now`, not by
patching module-level `datetime`.

**TODO -- other backlight control methods**: `st7789` (and other non-sysfs displays) need a
different brightness-control mechanism (GPIO pin? different sysfs path?) -- untested, needs a
real device over SSH. `method:` and `_METHODS` in `backlight.py` are the extension point.

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

**pytest**: `tests/test_config_validator.py`, `tests/test_cached_facts.py` (background-thread
fetch/retry/SIGUSR1 machinery), `tests/test_location.py` (location resolution, kill switch,
file shapes, contrib-preview no-network guarantees), `tests/test_display_transform_wiring.py`,
`test_platform_utils.py`, `test_all_encoding.py`.

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
3. jsonschema (structural: orientation, rows, panel types, cached-facts entries required)
4. Semantic walker (deprecated keys, unknown attrs, fact source checks, cached-facts name
   uniqueness + panel-reference checks, font misuse)

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
clockish-preview configs/clockish.yaml  # contrib mode: docs/previews/*.png + mock/ (no location network I/O)
clockish-preview --personal configs/clockish.yaml  # real location -> docs/previews/personal/ (gitignored, do not commit)
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

**Add a cached-facts fetcher type** (currently only `url-fact` exists):
1. Add name to `KNOWN_CACHED_FACT_TYPES` in `config_validator.py`; validate its required
   attrs in the `cached-facts` section of `_validate_semantics()`
2. Implement the fetch logic (parallel to `_fetch_url_raw()`) and wire it into
   `_init_cached_facts()`/`_cached_fact_worker()` in `display.py` -- entries of this type still
   just need to end up writing `_cached_facts_cache[name] = {'raw': ..., 'ok': ...}`
3. Test in `test_cached_facts.py` + `test_config_validator.py::TestCachedFacts`

**Add a value transform**:
1. Write `_t_<name>(value: str, arg) -> str` in `transforms.py`, add to `TRANSFORM_REGISTRY`
2. Classify it in `NO_ARG_TRANSFORMS` / `REQUIRED_ARG_TRANSFORMS` /
   `OPTIONAL_NUMERIC_ARG_TRANSFORMS` (used by validator's arg-shape checks)
3. Add arg-shape validation case in `config_validator.py` if it needs custom checks (e.g. `replace`)
4. Document in `URL_FACT_GUIDE.md` transforms table
5. Test in `test_transforms.py` + `test_config_validator.py::TestTransform`

**Add a location source / touch location code**:
1. Keep the rule: no network call without an explicit user setting. New lookups
   go behind an existing consented form, never behind the default path
2. Gate the fetch so `_contrib_preview()` resolves it from `tests/samples/`
   instead, and add the sample payload + a `scripts/update_samples.py` entry
3. End the resolution branch at `_resolve_sun_times_for()` if it produces coords
4. Extend `validate_location_value()` (shared) rather than adding a second
   notion of validity
5. Test in `tests/test_location.py` asserting zero network calls

**Add a font_behavior**:
1. Add name to `KNOWN_FONT_BEHAVIORS` in **both** `display.py` and `config_validator.py`
   (duplicated on purpose -- see font_behavior section above)
2. Implement the drawing logic in `_draw_text_line()` in `display.py`
3. Test in `test_display_fonts.py` + `test_config_validator.py::TestFontBehavior`

**Add a backlight control method** (currently only `sysfs` exists):
1. Add name to `KNOWN_BACKLIGHT_METHODS` in `config_validator.py`
2. Write a `_write_<method>(device, value) -> bool` function in `backlight.py`, add it to
   `_METHODS`
3. Test in `test_backlight.py` + `test_config_validator.py::TestBacklight`

**Add a backlight curve** (currently only `curve: sun` exists, alongside fixed `schedule:`):
1. Add name to `KNOWN_BACKLIGHT_CURVES` in `config_validator.py`
2. Write a `resolve_<name>_curve_value(..., min_, max_, now) -> int` pure function in
   `backlight.py`, wire it into `_resolve_value()`'s `cfg.get('curve')` dispatch
3. Test in `test_backlight.py` + `test_config_validator.py::TestBacklightCurve`

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
| Set location | `clockish-location` | writes `~/.config/clockish/location.yaml`; off by default |
| Kill network | `--offline`         | blocks location + cached-facts fetches               |
