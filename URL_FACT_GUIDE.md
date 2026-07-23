# url-fact Panel Type – Usage Guide

## Overview

The `url-fact` panel type fetches content from a URL at regular intervals, extracts a value using either regex or JSON path, caches the result, and displays it. Useful for displaying external data like weather, public APIs, or system info from remote sources.

## Configuration

```yaml
panels:
  - type: url-fact
    url: https://api.example.com/data
    pattern: 'value: (\d+)'    # OR json_path: (not both)
    json_path: result.value     # OR pattern: (not both)
    interval: 5m               # Fetch frequency (default: 5m)
    timeout: 5                 # HTTP timeout in seconds (default: 5)
    verify_ssl: false          # TLS verification (default: false)
    fallback: "n/a"            # Shown if fetch/parse fails (default: "n/a")
    label: "Temp "             # Optional text prefix
    color: white               # Text color
    font_size: normal          # Font size (scale or px)
    justify: center            # left | center | right
    width: auto                # Panel width
    background: '#000000'      # Panel background color, or named colors default is black
```

### Required Keys
- `url`: HTTP or HTTPS URL to fetch
- Exactly ONE of:
  - `pattern`: Python regex with capture group(s); first group extracted
  - `json_path`: Dot-notation path into JSON response (e.g., `data.temp`)

  For a dot-less `json_path` (e.g. `ip`, `tempF`), lookup is tried in this order:
  1. **Root-level key** -- `{"ip": "203.0.113.42"}` + `json_path: ip` -> `203.0.113.42`
  2. **Nested-wrapper key** (only if the root key is absent) -- for APIs that wrap
     their payload under an opaque/variable top-level key, e.g.
     `{"286114a10300004b": {"tempF": 71.8}}` + `json_path: tempF` -> `71.8`

  Use dot notation (`data.temp`) when you need to navigate an explicitly-named
  nested structure.

### Optional Keys
- `interval`: Frequency to fetch (default: `5m`)
  - Formats: `30s`, `5m`, `1h`, `2.5m`, etc.
- `timeout`: HTTP request timeout (default: `5`)
- `verify_ssl`: Check TLS certificate (default: `false`)
  - HTTP URLs ignore this setting
- `fallback`: Value shown if fetch/parse fails (default: `n/a`)
- `label`: Text prepended to the extracted value
- `transform`: Ordered list of value transforms applied before `label` -- see [Transforms](#transforms) below
- Standard styling: `color`, `font_size`, `justify`, `width`, `background`

## Transforms

Any text-producing panel type (`clock`, `date`, `fact`, `text`, `url-fact`) supports a `transform:`
key -- an ordered list of operations applied to the panel's core value *before* any label is added.
Great for cleaning up messy remote data (e.g., `"71.8"` -> `"72"`), or just for fun on static text.

```yaml
transform: [upper]                       # simple, no-argument form
transform: [round]                       # rounding transforms also work bare -- defaults to 0 decimal places
transform: [{round: 1}]                  # parameterised, single-key mapping form -- only needed for non-zero decimals
transform: [lower, {suffix: "!"}]        # chained -- applied left to right
```

### Available transforms

| Name                    | Argument            | Example (input -> output)                                              |
|-------------------------|----------------------|------------------------------------------------------------------------|
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
- type: url-fact
  url: http://sensor-master.lan/last-json
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

Failed/unsupported conversions (e.g. rounding a non-numeric fallback like `"n/a"`) leave the
value unchanged rather than crashing the render loop.


## Sample URLs to Test

### 1. Public IP Address (JSON)
```yaml
- type: url-fact
  url: https://api.ipify.org?format=json
  json_path: ip
  interval: 3m
  label: "IP "
  timeout: 5
  verify_ssl: false
```
Fetches: `{"ip":"203.0.113.42"}`
Extracts: `203.0.113.42`

### 2. Random Quote (JSON)
```yaml
- type: url-fact
  url: https://api.quotable.io/random
  json_path: content
  interval: 1h
  timeout: 5
  verify_ssl: false
  fallback: "No quote today"
  color: '#ffff00'
  font_size: small
```
Fetches: `{"_id":"...", "content":"Life is 10% what...", "author":"Charles R. Swindoll"}`
Extracts: First ~40 chars of `content` (truncated to panel width)

### 3. Joke (JSON nested path)
```yaml
- type: url-fact
  url: https://v2.jokeapi.dev/joke/Programming?format=json
  json_path: setup
  interval: 2h
  timeout: 5
  verify_ssl: false
```
Fetches: `{"setup":"Why do programmers...", "delivery":"...", ...}`
Extracts: `setup` field value

### 4. HTML Example (Regex)
```yaml
- type: url-fact
  url: http://example.com/
  pattern: '<title>([^<]+)</title>'
  interval: 6h
  timeout: 5
  label: "Title: "
```
Fetches: HTML `<title>Example Domain</title>`
Extracts: `Example Domain`

### 5. Weather (via Open Meteo – Free)
```yaml
- type: url-fact
  url: https://api.open-meteo.com/v1/forecast?latitude=51.5074&longitude=-0.1278&current=temperature_2m,weather_code
  json_path: current.temperature_2m
  interval: 10m
  timeout: 5
  verify_ssl: false
  label: "London Temp: "
  color: '#00ccff'
```
Extracts: Current temperature in Celsius

### 6. GitHub User Info (JSON)
```yaml
- type: url-fact
  url: https://api.github.com/users/octocat
  json_path: public_repos
  interval: 1h
  timeout: 5
  verify_ssl: false
  label: "Repos: "
```
Extracts: Public repo count for user `octocat`

### 7. Dog Breed Fact (Text + Regex)
```yaml
- type: url-fact
  url: https://dog-api.kinduff.com/api/facts/
  pattern: '"fact":"([^"]+)"'
  interval: 5m
  timeout: 5
  verify_ssl: false
  fallback: "Fetch failed"
  color: '#ff8800'
  font_size: small
```
Extracts: A random dog fact

## Staggered Initialization

When multiple `url-fact` panels use the same interval (e.g., 8 panels with 10s intervals), fetches are **automatically staggered** during startup to avoid thundering herd. Each panel fetches at a different offset within the interval window, spreading requests over time.

Example: 8 panels with 10s interval space fetches over ~10 seconds.

## Cache & Refresh

### Cache Duration
- Each panel caches its last fetched value until its interval expires
- Shows `fallback` until first fetch completes (or on errors)

### Manual Refresh (Linux/Pi only)
Send **SIGUSR1** signal to the clockish process to invalidate all `url-fact` caches:
```bash
kill -USR1 <clockish_pid>
```
Caches reset, all fetches staggered again on next render cycle.

On Windows/macOS, SIGUSR1 is gracefully ignored; caches refresh only by interval.

## Error Handling

If a fetch fails or pattern doesn't match:
1. Last cached value is reused (if available)
2. `fallback` is shown (if no prior cache)
3. Display continues normally (non-fatal)

Common failures:
- Network timeout → show `fallback`
- Invalid regex → show `fallback`
- JSON path not found → show `fallback`
- HTTP error (404, 500, etc.) → show `fallback`

Enable `--debug` to see detailed error messages:
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

By default, `verify_ssl: false` (no certificate validation). This is intentional for non-critical facts. If you want strict validation:

```yaml
verify_ssl: true
```

Note: `verify_ssl` only applies to HTTPS URLs; HTTP URLs always ignore this setting.

## Tips

1. **Slow networks**: Increase `timeout` to 10-15s for flaky connections
2. **Sensitive APIs**: Use `interval: 1h` or longer to avoid rate-limiting
3. **Large responses**: Keep `pattern` / `json_path` simple; first match wins
4. **Debugging**: Use `clockish-validate --no-yamllint my-config.yaml` to validate config before deploy
5. **Dynamic data**: Remember fetches happen every interval; displayed value updates live

## Example Complete Config

See `configs/url-fact-sample.yaml` for a full working example with:
- IP address (JSON)
- Random quote (JSON nested)
- HTML title (regex)
- Static text (for comparison)

Validate it:
```bash
clockish-validate configs/url-fact-sample.yaml --no-yamllint
```
