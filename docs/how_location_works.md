## How location works in clockish

### Overview

Location is **off unless you turn it on**. With no `location:` anywhere, clockish
performs zero location lookups, writes no location files, and renders location
panels empty. Turning it on gets you weather, sunrise/sunset, day/night facts,
and (later) backlight auto-dimming.

Location can be:

- **disabled** (the default) -- `disabled` / `none` / `off` / `false`
- **auto** -- a GeoIP lookup, which sends your public IP to a third party
- **an airport code** -- ICAO (4 char) or IATA (3 char)
- **coordinates** -- `"lat,lon"` or a mapping with `lat:` / `lon:`
- **a structured place** -- city + region + country, geocoded once

### What leaves the device

This is the whole list. Nothing else about the device is ever sent.

| Endpoint | When | What is sent | What comes back |
|----------|------|--------------|-----------------|
| `ipwho.is` | only with `location: auto` | your public IP address | approximate city, region, country, coordinates |
| `api.freeairportdb.com` | only with an airport code | the airport code you typed | coordinates, municipality, region, country, timezone, elevation |
| `geocoding-api.open-meteo.com` | only for a structured place with no coordinates | the place name you typed | coordinates for that name |
| `api.open-meteo.com` | whenever a location is set | your coordinates | today's and tomorrow's sunrise/sunset |

Notes:

- The sun-times call repeats on a schedule for as long as clockish runs, so a
  configured location means recurring contact with `api.open-meteo.com`.
- All four calls verify TLS certificates. (`cached-facts:` entries default to
  `verify_ssl: false`; these location calls are not configurable and are always
  verified.)
- `--offline` (or `CLOCKISH_OFFLINE=1`) blocks every outbound request, location
  and `cached-facts:` alike.
- `location: disabled` stops all four: no lookup, no cache read, no sun-times
  worker, and the runtime cache file is deleted.

### Turning it on

Preferred: run `clockish-location` (the installer runs it too). It shows what
each option sends before contacting anything, and asks for explicit consent
before the GeoIP lookup. It writes `~/.config/clockish/location.yaml`.

Alternatively put `location:` in any config file, which takes precedence over
the file above.

```yaml
location: auto          # consent to a GeoIP lookup at ipwho.is
```
```yaml
location:
  airport: KEGE         # or: location: KEGE
```
```yaml
location:               # nothing leaves the device except the sun-times call
  city: Centennial
  region: Colorado
  region_code: CO
  country: United States
  country_code: US
  postal: 80112
  lat: 39.5701186
  lon: -104.8492931
```
```yaml
location: disabled      # explicit off
```

A bare 3-4 letter string is treated as an **airport code** and looked up, so
`location: home` sends a request for code `HOME`. Write `location: disabled` if
you meant "no location". Values that look like a typo of a reserved word
(`atuo`, `nnoe`) are rejected at validation rather than looked up.

### The two files

| File | Owner | Written by |
|------|-------|------------|
| `~/.config/clockish/location.yaml` | you | `clockish-location` only -- the runtime never rewrites it |
| `~/.config/clockish/location-cache.yaml` | the runtime | clockish, after each resolution |

Both use one canonical shape: a single top-level `location:` key holding either
the scalar `disabled` or a mapping. Both are written mode `0600`, since they
record where you physically are. A legacy flat file (fields at the top level with
no `location:` key) is still read: the cache is migrated silently, and your own
file earns a warning pointing at `clockish-location`, which migrates it.

The cache carries `source:` and `resolved_at:`. GeoIP-derived entries expire
after 30 days and are then deleted -- re-resolving needs live consent, so an
expired entry with no current `location: auto` means disabled, not "look it up
again". Airport and explicit-coordinate entries are deterministic and do not
expire.

`location: disabled` deletes the cache but never touches your own
`location.yaml`. If an older clockish wrote resolved data there, remove it
yourself or run `clockish-location` and choose "disabled".

### Resolution order

1. `location:` in the config file
2. `~/.config/clockish/location.yaml`
3. `~/.config/clockish/location-cache.yaml`
4. disabled

An invalid setting at step 1 or 2 disables location rather than guessing. Each
resolution happens **once per run** (and once more per config reload); it is not
repeated per frame.

### Using it in panels

```yaml
- type: fact
  source: location.city          # or region, region_code, country, country_code,
                                 # postal, lat, lon, latitude, longitude,
                                 # elevation, elevation_ft, timezone
- type: fact
  source: location               # the whole thing: "City, Country (lat,lon)"
- type: fact
  source: daytime                # 'true' / 'false' from real sunrise/sunset
- type: fact
  source: nighttime
```

`lat`/`latitude` and `lon`/`longitude` are synonyms -- spell out either.

Without a location, `daytime`/`nighttime` fall back to a fixed static rule
(roughly "light between 07:00 and 19:00"), which looks like a working sun sensor
but is not one. `clockish-validate` warns when a config is in that state.

### Previews

`clockish-preview` has two location modes:

- **contrib** (default) -- resolves from `tests/samples/*.json`. No network, no
  cache write, deterministic. `docs/previews/*.png` is tracked in git, so this is
  the mode that keeps a contributor's real location out of the repository. An
  airport code with no matching sample renders blank rather than falling back to
  a live lookup.
- **personal** (`--personal`) -- resolves for real, exactly like a live run, and
  writes to `docs/previews/personal/` (gitignored). Use it to check how your own
  location looks in your layout. **Do not commit these renders.**

The mode is printed on every run. The manual preview hook pins `--contrib`.

`clockish-time-samples` always renders in contrib mode.

Refresh the sample payloads with `scripts/update_samples.py` (this makes real
requests, using a fixed sample IP rather than yours).

### Debugging

`--debug` prints the resolved city, the source, and coordinates rounded to ~11km
-- enough to tell a plausible fix from a wrong-hemisphere one without writing
your street-level position into the system journal.

`--debug-location` (or `CLOCKISH_DEBUG_LOCATION=1`) adds exact coordinates, full
API URLs, raw GeoIP payloads, and sunrise/sunset wall times.

### Where to look in the code

- Resolution, precedence, kill switch: `display.py::_init_system_location`
- File shapes, TTL, purge: `_read_system_location_cache`, `_write_location_document`,
  `_purge_location_cache`
- GeoIP: `_fetch_ipwho_coords` -- Airport: `_lookup_airport_code`
- Geocode: `_geocode_open_meteo` -- Sun times: `_fetch_and_store_sun_times`
- Contrib-preview sample loading: `_contrib_preview`, `_sample_location`
- Setting validation (shared by the validator, runtime and setup helper):
  `config_validator.py::validate_location_value`
