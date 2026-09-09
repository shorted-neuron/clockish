#!/usr/bin/env python3
"""Download fresh samples for ipwho and open-meteo into tests/samples/.

Usage:
  scripts/update_samples.py [IP]

Defaults to IP 129.72.188.0 (stable sample). Writes:
  tests/samples/ipwho-sample.json
  tests/samples/open-meteo-sun-sample.json

"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen


def fetch_json(url, headers=None, timeout=15):
    req = Request(url, headers=headers or {'User-Agent': 'clockish-sample-fetcher/1.0'})
    with urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def main():
    ip = sys.argv[1] if len(sys.argv) > 1 else '129.72.188.0'
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    samples_dir = os.path.join(repo_root, 'tests', 'samples')
    os.makedirs(samples_dir, exist_ok=True)

    ipwho_url = f'https://ipwho.is/{ip}'
    print('Fetching', ipwho_url)
    try:
        ipwho = fetch_json(ipwho_url)
    except Exception as exc:
        print('Error fetching ipwho:', exc)
        raise

    ipwho_path = os.path.join(samples_dir, 'ipwho-sample.json')
    with open(ipwho_path, 'w', encoding='utf-8') as fh:
        json.dump(ipwho, fh, indent=2, ensure_ascii=False)
    print('Wrote', ipwho_path)

    # ipwho may return 'latitude'/'longitude' or 'lat'/'lon'
    lat = ipwho.get('latitude') or ipwho.get('lat')
    lon = ipwho.get('longitude') or ipwho.get('lon')
    if lat is None or lon is None:
        print('Latitude/longitude not present in ipwho response; skipping open-meteo fetch')
        return

    try:
        latf = float(lat)
        lonf = float(lon)
    except Exception:
        print('Invalid lat/lon values from ipwho:', lat, lon)
        return

    # Fetch sunrise/sunset for today + tomorrow
    today = datetime.now(timezone.utc).date()
    tomorrow = today + timedelta(days=1)
    start = today.isoformat()
    end = tomorrow.isoformat()

    om_url = (
        f'https://api.open-meteo.com/v1/forecast?latitude={latf}&longitude={lonf}'
        f'&daily=sunrise,sunset&start_date={start}&end_date={end}&timezone=UTC'
    )
    print('Fetching', om_url)
    try:
        om = fetch_json(om_url)
    except Exception as exc:
        print('Error fetching open-meteo:', exc)
        raise

    om_path = os.path.join(samples_dir, 'open-meteo-sun-sample.json')
    with open(om_path, 'w', encoding='utf-8') as fh:
        json.dump(om, fh, indent=2, ensure_ascii=False)
    print('Wrote', om_path)

    # Fetch airport samples from FreeAirportDB API for KEGE (ICAO) and DEN (IATA).
    # Write to deterministic filenames for tests.
    API_BASE = 'https://api.freeairportdb.com/v1'
    airport_requests = [
        ('icao', 'KEGE', f"{API_BASE}/airports/KEGE"),
        ('iata', 'DEN', f"{API_BASE}/airports/DEN"),
    ]

    for kind, code, url in airport_requests:
        out_name = f'airport-lookup-{kind}-{code}.json'
        out_path = os.path.join(samples_dir, out_name)
        try:
            print('Fetching', url)
            data = fetch_json(url)
        except Exception as exc:
            print('Failed to fetch', url, ':', exc)
            continue

        # Write raw JSON response as-is
        with open(out_path, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        print('Wrote', out_path)


if __name__ == '__main__':
    main()
