#!/usr/bin/env python3
"""Interactive helper to populate ~/.config/clockish/location.yaml

Prompts user to choose one of: GeoIP (auto), airport code lookup, coordinates, structured place, or disabled.
Writes canonical mapping to ~/.config/clockish/location.yaml and legacy JSON.

Uses only the standard library (urllib) so it works in a minimal environment.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

import yaml

HOME = os.path.expanduser('~')
CFG_DIR = os.path.join(HOME, '.config', 'clockish')
YAML_PATH = os.path.join(CFG_DIR, 'location.yaml')

IPWHO_URL = 'https://ipwho.is/'
FREEAIRPORT_BASE = 'https://api.freeairportdb.com/v1/airports'
OPEN_METEO_GEOCODE = 'https://geocoding-api.open-meteo.com/v1/search'

USER_AGENT = 'clockish-setup-location/1.0'


def fetch_json(url: str, timeout: int = 10) -> dict | None:
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            return json.loads(data)
    except urllib.error.HTTPError as e:
        print(f'HTTP error fetching {url}: {e.code} {e.reason}')
    except Exception as e:
        print(f'Error fetching {url}: {e}')
    return None


def write_location(loc: dict) -> None:
    os.makedirs(CFG_DIR, exist_ok=True)
    with open(YAML_PATH, 'w', encoding='utf-8') as fh:
        yaml.safe_dump(loc, fh, sort_keys=False)
    print('\nWrote:')
    print('  ', YAML_PATH)


def prompt_yesno(prompt: str, default: bool = False) -> bool:
    yn = 'Y/n' if default else 'y/N'
    r = input(f'{prompt} [{yn}] ').strip().lower()
    if r == '':
        return default
    return r in ('y', 'yes')


def show_location_yaml(loc: dict) -> None:
    """Display location dict in YAML format, prefixed with 'location:' for copy-paste into config."""
    output = yaml.dump({'location': loc}, sort_keys=False)
    print(output)


def show_existing():
    if os.path.isfile(YAML_PATH):
        print('Existing location config:\n---\n')
        try:
            with open(YAML_PATH) as f:
                data = yaml.safe_load(f)
                if isinstance(data, dict):
                    # Handle both old format (plain dict) and new format (wrapped in 'location')
                    if 'location' in data:
                        show_location_yaml(data['location'])
                    else:
                        # Old format: file stored plain dict, show with location: wrapper
                        show_location_yaml(data)
                else:
                    # Not a dict, print raw
                    print(data)
        except Exception as e:
            print('  (failed to read existing config:', e, ')')
        return True
    return False


def do_geoip():
    print('Querying GeoIP (ipwho.is) for your public IP...')
    data = fetch_json(IPWHO_URL)
    if not data:
        print('GeoIP lookup failed.')
        return None
    # ipwho fields: city, latitude, longitude, region, region_code, country, country_code, postal
    loc = {
        'city': data.get('city') or '',
        'lat': float(data.get('latitude')) if data.get('latitude') is not None else None,
        'lon': float(data.get('longitude')) if data.get('longitude') is not None else None,
        'region': data.get('region') or None,
        'region_code': data.get('region_code') or None,
        'country': data.get('country') or None,
        'country_code': data.get('country_code') or None,
        'postal': data.get('postal') or None,
        'source': 'geoip',
    }
    return loc


def do_airport():
    code = input('Enter airport code (ICAO 4-char or IATA 3-char): ').strip().upper()
    if not code:
        print('No code provided.')
        return None
    url = f'{FREEAIRPORT_BASE}/{urllib.parse.quote(code)}'
    print('Querying FreeAirportDB:', url)
    payload = fetch_json(url)
    if not payload or not isinstance(payload, dict):
        print('Airport lookup failed.')
        return None
    data = payload.get('data') or payload
    # normalize
    loc = {
        'city': data.get('municipality') or data.get('city') or None,
        'region': data.get('region_name') or None,
        'region_code': data.get('region_code') or None,
        'country': data.get('country_name') or None,
        'country_code': data.get('country_code') or None,
        'lat': float(data.get('latitude')) if data.get('latitude') is not None else None,
        'lon': float(data.get('longitude')) if data.get('longitude') is not None else None,
        'elevation': data.get('elevation') or None,
        'elevation_ft': data.get('elevation_ft') or None,
        'timezone': data.get('time_zone') or data.get('time_zone') or None,
        'source': 'airport',
    }
    return loc


def do_coords():
    s = input('Enter coordinates as "lat,lon" (e.g. 39.7392,-104.9903): ').strip()
    if ',' not in s:
        print('Invalid format')
        return None
    lat_s, lon_s = [p.strip() for p in s.split(',', 1)]
    try:
        lat = float(lat_s)
        lon = float(lon_s)
    except Exception:
        print('Invalid numeric coordinates')
        return None
    city = input('Optional city name (leave blank to skip): ').strip() or ''
    region = input('Optional region/state (leave blank to skip): ').strip() or None
    country = input('Optional country name or code (leave blank to skip): ').strip() or None
    loc = {
        'city': city,
        'lat': lat,
        'lon': lon,
        'region': region,
        'country': country,
        'source': 'coords',
    }
    return loc


def do_structured():
    city = input('City name: ').strip()
    region = input('Region/state: ').strip()
    country = input('Country (name or ISO code): ').strip()
    if not city:
        print('City required for structured lookup')
        return None
    # Try Open-Meteo geocode to get coordinates
    q = urllib.parse.quote(f"{city}, {region}, {country}")
    url = f'{OPEN_METEO_GEOCODE}?name={q}&count=1&language=en'
    print('Querying Open-Meteo geocode:', url)
    payload = fetch_json(url)
    lat = lon = None
    if payload and isinstance(payload, dict):
        results = payload.get('results') or []
        if results:
            r = results[0]
            try:
                lat = float(r.get('latitude'))
                lon = float(r.get('longitude'))
            except Exception:
                lat = lon = None
    loc = {
        'city': city,
        'region': region or None,
        'country': country or None,
        'lat': lat,
        'lon': lon,
        'source': 'structured',
    }
    return loc


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Interactive or scripted setup for ~/.config/clockish/location.yaml')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--auto', action='store_true', help='Use GeoIP (ipwho.is) to infer location')
    group.add_argument('--airport', metavar='CODE', help='Airport code (ICAO or IATA) to lookup via FreeAirportDB')
    group.add_argument('--coords', metavar='LAT,LON', help='Coordinates as "lat,lon"')
    group.add_argument(
        '--structured',
        metavar='CITY,REGION,COUNTRY',
        help='Structured place; will attempt geocode via Open-Meteo',
    )
    group.add_argument('--disabled', action='store_true', help='Set location to disabled')
    parser.add_argument('-y', '--yes', action='store_true', help='Assume yes for prompts (non-interactive)')
    args = parser.parse_args()

    # Non-interactive path
    if args.auto or args.airport or args.coords or args.structured or args.disabled:
        if show_existing() and not args.yes:
            if not prompt_yesno('Reconfigure location?', default=False):
                print('Keeping existing location. Exiting.')
                return
        loc = None
        if args.auto:
            loc = do_geoip()
        elif args.airport:
            # simulate input for airport
            def input_override(prompt=''):
                return args.airport
            # monkey patch input used by do_airport
            orig_input = __builtins__['input']
            __builtins__['input'] = input_override
            try:
                loc = do_airport()
            finally:
                __builtins__['input'] = orig_input
        elif args.coords:
            # parse coords
            try:
                lat_s, lon_s = [p.strip() for p in args.coords.split(',', 1)]
                lat = float(lat_s)
                lon = float(lon_s)
                loc = {'city': '', 'lat': lat, 'lon': lon, 'source': 'coords'}
            except Exception:
                print('Invalid --coords format; expected LAT,LON')
                return
        elif args.structured:
            parts = [p.strip() for p in args.structured.split(',', 2)]
            while len(parts) < 3:
                parts.append('')
            city, region, country = parts
            # call structured geocode
            def input_override(prompt=''):
                # return values in order for do_structured prompts
                if 'City' in prompt:
                    return city
                if 'Region' in prompt:
                    return region
                if 'Country' in prompt:
                    return country
                return ''
            orig_input = __builtins__['input']
            __builtins__['input'] = input_override
            try:
                loc = do_structured()
            finally:
                __builtins__['input'] = orig_input
        elif args.disabled:
            loc = 'disabled'

        if loc is None:
            print('Setup failed or cancelled.')
            return
        if loc == 'disabled':
            write_location({'location': 'disabled'})
            print('Location set to disabled.')
            return
        # normalize and write
        norm = {}
        norm['city'] = loc.get('city') or ''
        if loc.get('lat') is not None:
            try:
                norm['lat'] = float(loc.get('lat'))
                norm['latitude'] = norm['lat']
            except Exception:
                pass
        if loc.get('lon') is not None:
            try:
                norm['lon'] = float(loc.get('lon'))
                norm['longitude'] = norm['lon']
            except Exception:
                pass
        for k in (
            'region',
            'region_code',
            'country',
            'country_code',
            'postal',
            'elevation',
            'elevation_ft',
            'timezone',
        ):
            if k in loc and loc.get(k) not in (None, ''):
                norm[k] = loc.get(k)
        norm['source'] = loc.get('source') or 'manual'

        write_location(norm)
        print('\nYour location config:\n---\n')
        show_location_yaml(norm)
        return

    # Interactive path
    print('Clockish location setup')
    print('------------------------')
    if show_existing():
        if not prompt_yesno('Reconfigure location?', default=False):
            print('Keeping existing location. Exiting.')
            return
    print('\nChoose setup method:')
    print('  1) GeoIP (auto) - use ipwho.is to infer city and coords')
    print('  2) Airport code - resolve via FreeAirportDB (ICAO/IATA)')
    print('  3) Coordinates - enter lat,lon directly')
    print('  4) Structured - enter city,region,country and attempt geocode')
    print('  5) Disabled - keep location disabled')

    choice = input('Enter choice [1-5]: ').strip()
    loc = None
    if choice == '1':
        loc = do_geoip()
    elif choice == '2':
        loc = do_airport()
    elif choice == '3':
        loc = do_coords()
    elif choice == '4':
        loc = do_structured()
    elif choice == '5':
        loc = 'disabled'
    else:
        print('Invalid choice')
        return

    if loc is None:
        print('Setup failed or cancelled.')
        return

    if loc == 'disabled':
        write_location({'location': 'disabled'})
        print('Location set to disabled.')
        return

    # Normalize: ensure lat/lon keys as numbers and city present
    norm = {}
    norm['city'] = loc.get('city') or ''
    if loc.get('lat') is not None:
        try:
            norm['lat'] = float(loc.get('lat'))
            norm['latitude'] = norm['lat']
        except Exception:
            pass
    if loc.get('lon') is not None:
        try:
            norm['lon'] = float(loc.get('lon'))
            norm['longitude'] = norm['lon']
        except Exception:
            pass
    for k in (
        'region',
        'region_code',
        'country',
        'country_code',
        'postal',
        'elevation',
        'elevation_ft',
        'timezone',
    ):
        if k in loc and loc.get(k) not in (None, ''):
            norm[k] = loc.get(k)
    norm['source'] = loc.get('source') or 'manual'

    write_location(norm)
    print('\nYour location config:')
    show_location_yaml(norm)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nCancelled by user')
        sys.exit(1)
