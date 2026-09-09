## How location works in clockish

### Overview

Clockish keeps location handling small and privacy-first. Location can be "disabled" (default), "auto" (GeoIP), an explicit lat,lon, a structured object (city/region/country), or an airport code (ICAO 4 chars or IATA 3 chars). Resolved location is normalized and cached to ~/.config/clockish/location.yaml.

### Config-level tips

- If you want all location fields to be very precise, set it all like [location-static.yaml](../configs/location-static.yaml) .  This also avoids location network calls (but not weather if those facts are used)
- To avoid network calls in previews: populate `location:` in your config with explicit fields (city, region, country, country_code, postal, lat, lon).
- To let clockish derive coordinates and sunrise/sunset: 
  - set `location: auto` (consent to GeoIP)  
  - or `location: "KEGE"` (airport). Airport codes are matched case-insensitively; 
    airport lookup returns lat/lon and attempts to translate often used location fields.

### Example config dictionaries
Preferred:  Installer will help you write primary location config in your home directory `~/.config/clockish/location.yaml`
Alternate: you may specify location in any regular config file, which will take precedence.

```yaml
location: auto  # use auto to get location from ipwho.is
```
OR use a nearby airport facts
```yaml
location:
  airport: KEGE
```
OR supplement with your own precise or chosen values
```yaml
location:
  # airport: kapa  # not really needed since its all specified below
  # explicit fields provided so previews don't need reverse geocoding
  city: Centennial
  region: Colorado
  region_code: CO
  country: United States
  country_code: US
  postal: 80112
  lat: 39.5701186
  lon: -104.8492931
```

### Resolution flow (single-pass summary)

- Config override: top-level `location` wins. Accepts: any of:
  - "disabled"/"none", 
    - no lookups done at all.  location or weather fact helpers may return empty data
  - "auto", 
    - call GeoIP (ipwho.is) to obtain rough city + coordinates;
    - use those coords for downstream calls to weather services
  - lat,lon string ("lat,lon"), 
  - a mapping with lat/lon, 
  - a mapping with {city,region,country}, 
  - or an airport code/field.  resolves to coordinates:
    - Airport codes: runtime uses FreeAirportDB HTTP API (https://api.freeairportdb.com/v1/airports/{code}) and normalizes its payload into clockish' canonical location fields. Preview mode forces fresh lookups; production performs per-lookup fetches (no persistent local cache).
    - Placenames: Open‑Meteo geocoding for names; direct parse for lat/lon.
- Note: clockish does not perform reverse-geocoding during normal runtime. If you need postal codes, provide them explicitly in your `location:` config (postal is optional and will not trigger warnings).
- Sun-times: Open‑Meteo daily sunrise/sunset endpoint is fetched for today+tomorrow and cached; preview mode fetches synchronously so panels render deterministically.

### External APIs used

- ipwho.is — GeoIP for "auto" behavior
  - URL used: https://ipwho.is/
  - Purpose: quick, privacy-oriented city + latitude/longitude when user opts into auto.
  - Fields consumed: city, latitude/longitude, region, region_code, country, country_code (postal if present; but postal is optional and not warned on).
  - Local sample: ../tests/samples/ipwho-sample.json

- FreeAirportDB API — airport code → coordinates & metadata
  - URL used: https://api.freeairportdb.com/v1/airports/{code}
  - Purpose: resolve ICAO (4-char) or IATA (3-char) airport codes to lat/lon, municipality, timezone, elevation, and country/region metadata.
  - Runtime behavior: responses are normalized into clockish' canonical location mapping and returned to the caller; preview mode forces fresh lookups. There is no persistent local airport cache.
  - Local samples: ../tests/samples/airport-lookup-icao-KEGE.json and ../tests/samples/airport-lookup-iata-DEN.json

- Open‑Meteo geocoding API — name → coordinates
  - URL used (search): https://geocoding-api.open-meteo.com/v1/search?name={q}&count=1&language=en
  - Purpose: when user supplies structured city/region/country or a placename string, resolve to coords.
  - Local sample: ../tests/samples/open-meteo-geocode.raw.json

- Open‑Meteo sun-times endpoint — sunrise/sunset for dates
  - URL used: https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=sunrise,sunset&start_date={start}&end_date={end}&timezone=auto
  - Purpose: produce daytime/nighttime facts and backlight scheduling data.
  - Local sample: ../tests/samples/open-meteo-sun-sample.json

Notes on preview and tests

- Deterministic previews/tests: code uses `tests/samples/` payloads for cached-facts when present so many preview paths and unit tests can run without network I/O.
- Airport resolution: runtime uses FreeAirportDB HTTP API to obtain lat/lon and metadata; no reverse-geocode enrichment is performed. Sun-times are still fetched live from Open‑Meteo.

Where to look in the code

- Resolution & caching: src/clockish/display.py::_init_system_location and _write_system_location_cache
- GeoIP fetch: src/clockish/display.py::_fetch_ipwho_coords
- Airport lookup: src/clockish/display.py::_lookup_airport_code
- Geocode / sun-times: src/clockish/display.py:_geocode_open_meteo and _fetch_and_store_sun_times
