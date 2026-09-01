## How location works in clockish

### Overview

Clockish keeps location handling small and privacy-first. Location can be "disabled" (default), "auto" (GeoIP), an explicit lat,lon, a structured object (city/region/country), or an airport code (ICAO 4 chars or IATA 3 chars). Resolved location is normalized and cached to ~/.config/clockish/location.yaml.

### Resolution flow (single-pass summary)

- Config override: top-level `location` wins. Accepts: "disabled"/"none", "auto", a lat,lon string ("lat,lon"), a mapping with lat/lon, a mapping with {city,region,country}, or an airport code/field.
- If "auto": call GeoIP (ipwho.is) to obtain rough city + coordinates; use those coords for downstream calls.
- If airport code or placename (or lat/lon): resolve to coords (OurAirports CSV for airport codes; Open‑Meteo geocoding for names; direct parse for lat/lon).
- Note: clockish does not perform reverse-geocoding. If you need region/state, country, country_code, or postal fields, provide them explicitly in your `location:` config.
- Sun-times: Open‑Meteo daily sunrise/sunset endpoint is fetched for today+tomorrow and cached; preview mode fetches synchronously so panels render deterministically.

### External APIs used

- ipwho.is — GeoIP for "auto" behavior
  - URL used: https://ipwho.is/
  - Purpose: quick, privacy-oriented city + latitude/longitude when user opts into auto.
  - Fields consumed: city, latitude/longitude, region, region_code, country, country_code, postal (if present).
  - Local sample: ../tests/samples/ipwho.raw.json

- OurAirports CSV — airport code → coordinates
  - URL used: https://ourairports.com/data/airports.csv
  - Purpose: resolve ICAO (4-char) or IATA (3-char) airport codes to lat/lon and city/name.
  - Code parses CSV, caches mapping to ~/.config/clockish/airports_cache.json and looks up by ident or iata.
  - Local samples: ../tests/samples/airport_KDEN.raw.json and ../tests/samples/airport_DEN.raw.json

- Open‑Meteo geocoding API — name → coordinates
  - URL used (search): https://geocoding-api.open-meteo.com/v1/search?name={q}&count=1&language=en
  - Purpose: when user supplies structured city/region/country or a placename string, resolve to coords.
  - Local sample: ../tests/samples/open-meteo-geocode.raw.json

- Open‑Meteo sun-times endpoint — sunrise/sunset for dates
  - URL used: https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=sunrise,sunset&start_date={start}&end_date={end}&timezone=auto
  - Purpose: produce daytime/nighttime facts and backlight scheduling data.
  - Local sample: ../tests/samples/open-meteo-sun.raw.json

Notes on preview and tests

- Deterministic previews/tests: code uses `tests/samples/` payloads for cached-facts when present so many preview paths and unit tests can run without network I/O.
- Airport resolution: runtime uses OurAirports CSV to obtain lat/lon only. No reverse-geocode enrichment is performed; sun-times are still fetched live.

Config-level tips (short)

- To avoid network calls in previews entirely: populate `location:` in your config with explicit fields (city, region, country, country_code, postal, lat, lon).
- To let clockish derive coordinates and sunrise/sunset: set `location: auto` (consent to GeoIP) or `location: "KDEN"` (airport). Airport codes are matched case-insensitively; airport lookup returns lat/lon only.

Where to look in the code

- Resolution & caching: src/clockish/display.py::_init_system_location and _write_system_location_cache
- GeoIP fetch: src/clockish/display.py::_fetch_ipwho_coords
- Airport lookup: src/clockish/display.py::_lookup_airport_code and _fetch_airports_csv_and_cache
- Geocode / sun-times: src/clockish/display.py:_geocode_open_meteo and _fetch_and_store_sun_times
