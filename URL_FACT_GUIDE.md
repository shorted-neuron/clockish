# cached-facts – Background Remote Data Guide

## Overview

`cached-facts:` is a top-level config section that fetches remote data (HTTP/HTTPS) on a
**background daemon thread**, decoupled entirely from the render loop -- a slow or flaky
network call never delays a frame. `fact` panels then consume an entry via
`source: cached-facts.<name>` plus a `json_path` or `pattern` to extract the specific field
they want, and everything else (label, color, font, `transform:`) works exactly like any other
`fact` panel.

Because the fetch and the extraction are decoupled, **multiple panels can share ONE fetch**:
e.g. a weather API call that returns temperature, wind speed, and wind direction can back a
temperature-in-°F panel, a temperature-in-°C panel (via `transform:`), and a wind-speed panel,
all from a single request every `interval`.

> This replaces the old `url-fact` panel type, which fetched synchronously inline in the render
> loop (one URL per panel, no sharing). It has been removed -- no backward compatibility.
> Migrate a `url-fact` panel by moving its `url`/`interval`/`timeout`/`verify_ssl` into a
> `cached-facts:` entry, and turning the panel itself into a `fact` panel with
> `source: cached-facts.<name>` + `json_path`/`pattern`.

## Configuration

```yaml
cached-facts:
  - name: Denver-Weather                # referenced by panels as cached-facts.Denver-Weather
    type: url-fact                      # fetcher kind; only 'url-fact' (HTTP GET) today
    url: https://api.open-meteo.com/v1/forecast?latitude=39.57&longitude=-104.85&current=temperature_2m,wind_speed_10m
    interval: 20m                       # fetch frequency (default: 5m)
    timeout: 5                          # HTTP timeout, seconds (default: 5)
    verify_ssl: false                   # TLS verification (default: false)
    preview_response: '{"current": {"temperature_2m": 71.8, "wind_speed_10m": 5.2}}'
    # ^ optional: used verbatim by clockish-preview/clockish-time-samples instead of a
    #   real network fetch, keeping preview renders offline/deterministic. If omitted,
    #   preview mode fetches once synchronously (real network) before rendering the frame.

rows:
  - name: weather-row
    height: 40
    panels:
      - type: fact
        source: cached-facts.Denver-Weather
        json_path: current.temperature_2m   # OR pattern: (not both)
        label: "Denver "
        color: white
        font_size: normal
        justify: center
        width: auto
        background: '#000000'
        transform: [{round: 0}, {suffix: "°F"}]

      - type: fact
        source: cached-facts.Denver-Weather   # same fetch, different field + transform
        json_path: current.temperature_2m
        transform: [fahrenheit_to_celsius, {round: 0}, {suffix: "°C"}]
```

### `cached-facts:` entry keys

- `name` (required): unique identifier, referenced by panels as `source: cached-facts.<name>`
- `type` (required): fetcher kind. Only `url-fact` (HTTP/HTTPS GET) is currently supported
- `url` (required): HTTP or HTTPS URL to fetch. YAML block scalars (`|-`) are handy for
  wrapping long URLs across lines -- whitespace is stripped automatically
- `interval` (optional): fetch frequency, e.g. `30s`, `5m`, `1h`, `2.5m` (default: `5m`)
- `timeout` (optional): HTTP request timeout in seconds (default: `5`)
- `verify_ssl` (optional): TLS certificate verification for `https://` URLs (default: `false`;
  ignored for `http://`)
- `preview_response` (optional): raw response body used verbatim in preview mode instead of a
  real fetch

### Consuming `fact` panel keys

- `source: cached-facts.<name>` (required to opt into this mode): `<name>` must match a
  `cached-facts:` entry
- Exactly ONE of:
  - `json_path`: dot-notation path into the raw JSON response (e.g. `current.temp`)
  - `pattern`: Python regex with a capture group; first group extracted (for non-JSON
    responses, e.g. scraping an HTML `<title>`)

  For a dot-less `json_path` (e.g. `ip`, `tempF`), lookup is tried in this order:
  1. **Root-level key** -- `{"ip": "203.0.113.42"}` + `json_path: ip` -> `203.0.113.42`
  2. **Nested-wrapper key** (only if the root key is absent) -- for APIs that wrap
     their payload under an opaque/variable top-level key, e.g.
     `{"286114a10300004b": {"tempF": 71.8}}` + `json_path: tempF` -> `71.8`

  Use dot notation (`data.temp`) when you need to navigate an explicitly-named
  nested structure.
- `transform`: ordered list of value transforms applied before `label` -- see [Transforms](#transforms) below
- Standard `fact` panel styling: `label`, `color`, `font_size`, `justify`, `width`, `background`

### No `fallback:` key

Unlike the old `url-fact` panel, a `fact` panel consuming a `cached-facts` source has no
`fallback:` concept. Before the background thread's first successful fetch (or if it has
*never* once succeeded), the panel simply renders an **empty string**.

## Transforms

Any text-producing panel type (`clock`, `date`, `fact`, `text`) supports a `transform:`
key -- an ordered list of operations applied to the panel's core value *before* any label is added.
Great for cleaning up messy remote data (e.g., `"71.8"` -> `"72"`), unit conversion, or just for
fun on static text.

```yaml
transform: [upper]                       # simple, no-argument form
transform: [round]                       # rounding transforms also work bare -- defaults to 0 decimal places
transform: [{round: 1}]                  # parameterised, single-key mapping form -- only needed for non-zero decimals
transform: [lower, {suffix: "!"}]        # chained -- applied left to right
transform: [fahrenheit_to_celsius, {round: 0}, {suffix: "°C"}]   # unit conversion + rounding + suffix
```

### Available transforms

| Name                    | Argument            | Example (input -> output)                                              |
|-------------------------|----------------------|--------------------------------------------------------------------------|
| `upper`                 | none                 | `hello` -> `HELLO`                                                     |
| `lower`                 | none                 | `HELLO` -> `hello`                                                     |
| `title`                 | none                 | `hello world` -> `Hello World`                                         |
| `capitalize`            | none                 | `hello world` -> `Hello world`                                         |
| `titlecase` / `pascalcase` | none              | `hello world` -> `HelloWorld`                                          |
| `camelcase`             | none                 | `hello world` -> `helloWorld`                                          |
| `strip`                 | none                 | `"  hi  "` -> `hi`                                                     |
| `round`                 | decimal places (default 0) | `71.8` -> `72`  (banker's rounding)                                    |
| `ceil`                  | decimal places (default 0) | `71.1` -> `72`  (always rounds up)                                     |
| `floor`                 | decimal places (default 0) | `71.9` -> `71`  (always rounds down)                                   |
| `int`                   | none                 | `71.8` -> `71`  (truncate, no rounding)                                |
| `abs`                   | none                 | `-5` -> `5`                                                            |
| `multiply`              | number (required)   | `10` + `{multiply: 1.8}` -> `18`                                       |
| `add`                   | number (required)   | `32` + `{add: 10}` -> `42`                                             |
| `subtract`              | number (required)   | `100` + `{subtract: 32}` -> `68`                                       |
| `divide`                | number (required)   | `18` + `{divide: 1.8}` -> `10`                                          |
| `celsius_to_fahrenheit` | none                 | `20` -> `68`                                                            |
| `fahrenheit_to_celsius` | none                 | `68` -> `20`                                                            |
| `replace`               | `{from, to}` (required) | `hello world` + `{replace: {from: world, to: there}}` -> `hello there` |
| `prefix`                | string (required)   | `72` + `{prefix: "IP: "}` -> `IP: 72`                                  |
| `suffix`                | string (required)   | `72` + `{suffix: "F"}` -> `72F`                                        |
| `format`                | Python format-spec (required) | `71.8` + `{format: "{:.1f}F"}` -> `71.8F`                              |

### `titlecase`/`pascalcase` vs `camelcase`

Both split on whitespace/`_`/`-` and capitalize each word, but `titlecase` (alias `pascalcase`)
capitalizes the **first** word too, while `camelcase` leaves the first word lowercase:

```yaml
# input: "hello world"
transform: [titlecase]   # -> "HelloWorld"
transform: [pascalcase]  # -> "HelloWorld"  (same as titlecase)
transform: [camelcase]   # -> "helloWorld"  (first word lowercase)
```

### Three rounding modes -- string -> float -> int

`round`, `ceil`, and `floor` all convert the value to a float first, then apply a distinct
rounding rule (contrast with `int`, which truncates with no rounding at all). **All three
work as bare strings with no argument** -- the decimal-places arg defaults to `0`; only add
`{round: N}` / `{ceil: N}` / `{floor: N}` if you want N > 0 decimal places kept:

```yaml
# input: "71.8"  (a string, as returned by json_path/pattern)
transform: [round]   # -> "72"  (round-half-even: nearest int)  -- bare form, no arg needed
transform: [ceil]    # -> "72"  (always rounds up)               -- bare form, no arg needed
transform: [floor]   # -> "71"  (always rounds down)             -- bare form, no arg needed
transform: [int]     # -> "71"  (truncate toward zero -- no rounding)

# non-zero decimal places require the mapping form:
transform: [{round: 1}]   # "71.849" -> "71.8"
```

### The `format` escape hatch

For anything the named transforms don't cover, `format` applies a raw Python format-spec
to the (numeric, when possible) value:

```yaml
- type: fact
  source: cached-facts.sensor
  json_path: tempF
  transform: [{format: "{:.1f}°F"}]   # "71.8" -> "71.8°F"
```

Numeric conversion is attempted first; if the value isn't numeric, the format-spec is applied
to the raw string instead (e.g. `{format: "[{}]"}` on `"hello"` -> `"[hello]"`).

### Chaining example (silly but valid)

```yaml
- type: text
  label: "HELLO"
  transform: [lower]          # "HELLO" -> "hello"
- type: text
  label: "hello world"
  transform: [camelcase]      # "hello world" -> "helloWorld"
```

Failed/unsupported conversions (e.g. rounding a non-numeric value) leave the
value unchanged rather than crashing the render loop.

## Sample cached-facts + fact pairs to try

### 1. Public IP Address (JSON)
```yaml
cached-facts:
  - name: MyIP
    type: url-fact
    url: https://api.ipify.org?format=json
    interval: 3m
    timeout: 5
    verify_ssl: false
```
```yaml
- type: fact
  source: cached-facts.MyIP
  json_path: ip
  label: "IP "
```
Fetches: `{"ip":"203.0.113.42"}` -- Extracts: `203.0.113.42`

### 2. Random Quote (JSON)
```yaml
cached-facts:
  - name: Quote
    type: url-fact
    url: https://api.quotable.io/random
    interval: 1h
    timeout: 5
    verify_ssl: false
```
```yaml
- type: fact
  source: cached-facts.Quote
  json_path: content
  color: '#ffff00'
  font_size: small
```
Fetches: `{"_id":"...", "content":"Life is 10% what...", "author":"..."}` -- Extracts: `content`

### 3. HTML `<title>` (Regex)
```yaml
cached-facts:
  - name: ExampleTitle
    type: url-fact
    url: http://example.com/
    interval: 6h
    timeout: 5
```
```yaml
- type: fact
  source: cached-facts.ExampleTitle
  pattern: '<title>([^<]+)</title>'
  label: "Title: "
```
Fetches: HTML `<title>Example Domain</title>` -- Extracts: `Example Domain`

### 4. Weather, one fetch, two units (via Open Meteo -- Free)
```yaml
cached-facts:
  - name: London-Weather
    type: url-fact
    url: https://api.open-meteo.com/v1/forecast?latitude=51.5074&longitude=-0.1278&current=temperature_2m,wind_speed_10m
    interval: 10m
    timeout: 5
    verify_ssl: false
```
```yaml
- type: fact
  source: cached-facts.London-Weather
  json_path: current.temperature_2m
  label: "London: "
  color: '#00ccff'
  transform: [{round: 0}, {suffix: "°C"}]

- type: fact
  source: cached-facts.London-Weather   # SAME fetch, different field
  json_path: current.wind_speed_10m
  label: "Wind: "
  transform: [{round: 1}, {suffix: " km/h"}]
```

## Staggered Initialization

When multiple `cached-facts` entries exist, their fetches are **automatically staggered**
across the interval window at startup to avoid a thundering herd (e.g. 8 entries with 10s
intervals space their fetches over ~10 seconds).

## Cache & Refresh

- Each entry's background thread keeps its last fetched raw value until its interval expires,
  then refetches -- independent of any panel's render cadence.
- Consuming `fact` panels re-run extraction (`json_path`/`pattern`) and `transform:` on
  **every render** (cheap, no I/O), so editing `transform:`/`json_path` and reloading clockish
  takes effect immediately without needing a fresh fetch.

### Manual Refresh (Linux/Pi only)
Send **SIGUSR1** signal to the clockish process to wake every cached-facts background thread
for an immediate refetch (instead of waiting out its current interval/backoff):
```bash
kill -USR1 <clockish_pid>
```

On Windows/macOS, SIGUSR1 is gracefully ignored; entries refresh only by interval.

## Error Handling

If a fetch fails (network/timeout error), that entry's background thread:
1. Keeps the previous raw value (if any) -- consuming panels keep showing their most
   recent good value instead of flashing blank.
2. Backs off: the next retry starts at **1/10th the configured `interval`** (minimum 1s),
   then **doubles on each consecutive failure**, capped at the full `interval` -- a transient
   outage retries soon; a persistent one settles down to normal cadence instead of hammering
   the remote endpoint.
3. If the entry has *never* had a successful fetch, consuming `fact` panels render an
   **empty string** (no `fallback:` key).
4. A missing `json_path` key logs a warning (stderr + syslog if available); a non-matching
   `pattern` just yields an empty extracted value. Display continues normally either way
   (non-fatal).

Enable `--debug` to see detailed fetch/retry messages:
```bash
clockish --debug my-config.yaml
```

## Interval Format

| Format | Meaning                   |
|--------|---------------------------|
| `30s`  | 30 seconds                |
| `5m`   | 5 minutes                 |
| `1h`   | 1 hour                    |
| `2.5m` | 2.5 minutes (150 seconds) |

## TLS/SSL Certificate Verification

By default, `verify_ssl: false` (no certificate validation). This is intentional for non-critical
data. If you want strict validation:

```yaml
verify_ssl: true
```

Note: `verify_ssl` only applies to HTTPS URLs; HTTP URLs always ignore this setting.

## Preview mode (`clockish-preview` / `clockish-time-samples`)

No background threads are spawned in preview mode (a preview render is one-shot):
- If a `cached-facts` entry has `preview_response` set, it's used verbatim -- fully
  offline/deterministic, recommended for configs tracked/rendered in CI.
- If not, the entry is fetched once, **synchronously**, before the frame renders -- a real
  network call is allowed here, it just has to complete before rendering rather than running
  in the background.

## Tips

1. **Slow networks**: Increase `timeout` to 10-15s for flaky connections
2. **Sensitive APIs**: Use `interval: 1h` or longer to avoid rate-limiting
3. **Share fetches**: One `cached-facts` entry can back many `fact` panels with different
   `json_path`/`pattern`/`transform` combinations -- fewer remote calls, more display options
4. **Debugging**: Use `clockish-validate --no-yamllint my-config.yaml` to validate config before deploy
5. **Dynamic data**: Fetches happen every `interval`, independent of rendering; displayed
   value updates live as soon as the background thread writes it

## Example Complete Config

See `configs/url-fact-sample.yaml` -- **note**: this sample still uses the old, removed
`url-fact` panel type and needs migrating to `cached-facts:` + `fact` panels (see "Overview"
above for the migration pattern). `configs/landscape-demo.yaml` is a working example of the
current pattern (3 weather `cached-facts` entries backing 6 `fact` panels in both °F and °C).

Validate a config:
```bash
clockish-validate configs/landscape-demo.yaml --no-yamllint
```
