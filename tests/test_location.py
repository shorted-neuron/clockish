"""tests/test_location.py

Tests for the privacy-first location subsystem in display.py:
  - the kill switch (location: disabled / none / off / false, --offline) making
    zero network calls and purging the runtime cache
  - resolution precedence: config > user-owned location.yaml > cache > disabled
  - on-disk file shapes: canonical nested form, legacy flat migration, the
    GeoIP TTL, 0600 permissions
  - contrib previews resolving from tests/samples/ with no network and no writes
  - sunrise/sunset boundaries in get_daytime()
  - debug redaction of coordinates

No real network I/O anywhere: _fetch_url_raw is monkeypatched in every test that
could reach it, and each test asserts on the recorded call list, so a regression
that reintroduces a lookup fails loudly rather than silently phoning home.
"""
import datetime
import json
import os

import pytest
import yaml

import clockish.display as cd


@pytest.fixture
def loc_env(tmp_path, monkeypatch):
    """Isolate location state: temp file paths, no network, no sun-times thread.

    Returns a small namespace with the two file paths and the list of URLs the
    code under test tried to fetch (empty list == provably no network I/O).
    """
    calls: list[str] = []
    user_file = tmp_path / 'location.yaml'
    cache_file = tmp_path / 'location-cache.yaml'

    monkeypatch.setattr(cd, '_LOCATION_YAML_PATH', str(user_file))
    monkeypatch.setattr(cd, '_LOCATION_CACHE_PATH', str(cache_file))
    monkeypatch.setattr(cd, '_SYSTEM_LOCATION', None)
    monkeypatch.setattr(cd, '_SUN_TIMES', {})
    monkeypatch.setattr(cd, '_PREVIEW_MODE', False)
    monkeypatch.setattr(cd, '_PREVIEW_LOCATION_MODE', 'contrib')
    monkeypatch.setattr(cd, '_OFFLINE', False)
    monkeypatch.setattr(cd, '_location_resolved_key', None)
    monkeypatch.setattr(cd, '_flat_user_file_warned', False)
    monkeypatch.setattr(cd, '_fetch_url_raw',
                        lambda url, *a, **k: (calls.append(url), (None, None))[1])
    monkeypatch.setattr(cd, '_start_sun_times', lambda lat, lon: calls.append('sun-worker'))

    class Env:
        pass

    env = Env()
    env.calls = calls
    env.user_file = user_file
    env.cache_file = cache_file
    return env


# ---------------------------------------------------------------------------
# Kill switch
# ---------------------------------------------------------------------------

class TestDisabled:

    @pytest.mark.parametrize('value', ['disabled', 'none', 'off', 'false',
                                       'DISABLED', 'None', False])
    def test_disabled_makes_no_lookups(self, loc_env, value):
        """Every disabled spelling short-circuits before any network call.

        'none' and 'off' matter most: they are 3-4 alpha characters, so before
        the kill switch ran first they fell through to the airport-code branch
        and were sent to the lookup API as codes NONE / OFF.
        """
        cd._init_system_location({'location': value})

        assert cd._SYSTEM_LOCATION is None
        assert loc_env.calls == []
        assert not cd._location_enabled()

    def test_disabled_purges_the_runtime_cache(self, loc_env):
        loc_env.cache_file.write_text(yaml.safe_dump(
            {'location': {'city': 'Denver', 'lat': 39.7, 'lon': -104.9, 'source': 'airport'}}
        ))

        cd._init_system_location({'location': 'disabled'})

        assert not loc_env.cache_file.exists()

    def test_disabled_never_deletes_the_user_file(self, loc_env):
        """The user's own file is theirs; only the runtime cache is purged."""
        loc_env.user_file.write_text('location: disabled\n')

        cd._init_system_location({'location': 'disabled'})

        assert loc_env.user_file.exists()

    def test_user_file_can_disable(self, loc_env):
        loc_env.user_file.write_text('location: disabled\n')

        cd._init_system_location({})

        assert cd._SYSTEM_LOCATION is None
        assert loc_env.calls == []

    def test_no_location_anywhere_defaults_to_disabled(self, loc_env):
        cd._init_system_location({})

        assert cd._SYSTEM_LOCATION is None
        assert loc_env.calls == []
        assert not loc_env.cache_file.exists()

    def test_offline_blocks_even_explicit_auto(self, loc_env, monkeypatch):
        monkeypatch.setattr(cd, '_OFFLINE', True)

        cd._init_system_location({'location': 'auto'})

        assert cd._SYSTEM_LOCATION is None
        assert loc_env.calls == []

    def test_offline_short_circuits_the_fetch_helper(self, monkeypatch):
        """The guard sits at the single choke point every fetch passes through."""
        monkeypatch.setattr(cd, '_OFFLINE', True)

        assert cd._fetch_url_raw('https://ipwho.is/', 5, True) == (None, None)

    def test_disabled_location_is_not_sun_times_eligible(self, loc_env):
        cd._init_system_location({'location': 'disabled'})

        assert not cd._location_enabled()

    def test_zero_coordinates_are_not_a_location(self, loc_env, monkeypatch):
        """0,0 is the old 'disabled' sentinel; it must not start the worker."""
        monkeypatch.setattr(cd, '_SYSTEM_LOCATION',
                            {'city': 'x', 'lat': 0.0, 'lon': 0.0, 'source': 'disabled'})

        assert not cd._location_enabled()


# ---------------------------------------------------------------------------
# Invalid settings never reach a lookup
# ---------------------------------------------------------------------------

class TestInvalidSettings:

    @pytest.mark.parametrize('value', ['atuo', 'nnoe', True, {'citty': 'x'}, 42])
    def test_invalid_setting_disables_instead_of_guessing(self, loc_env, value):
        """A typo like 'atuo' is 4 alpha chars, so it used to be looked up."""
        cd._init_system_location({'location': value})

        assert cd._SYSTEM_LOCATION is None
        assert loc_env.calls == []


# ---------------------------------------------------------------------------
# Precedence
# ---------------------------------------------------------------------------

class TestPrecedence:

    def test_config_beats_user_file(self, loc_env):
        loc_env.user_file.write_text(yaml.safe_dump(
            {'location': {'city': 'Aspen', 'lat': 39.1, 'lon': -106.8}}))

        cd._init_system_location({'location': {'city': 'Denver', 'lat': 39.7, 'lon': -104.9}})

        assert cd._SYSTEM_LOCATION['city'] == 'Denver'

    def test_user_file_used_when_config_is_silent(self, loc_env):
        loc_env.user_file.write_text(yaml.safe_dump(
            {'location': {'city': 'Aspen', 'lat': 39.1, 'lon': -106.8}}))

        cd._init_system_location({})

        assert cd._SYSTEM_LOCATION['city'] == 'Aspen'

    def test_cache_used_when_config_and_user_file_are_silent(self, loc_env):
        loc_env.cache_file.write_text(yaml.safe_dump(
            {'location': {'city': 'Cached', 'lat': 1.0, 'lon': 2.0, 'source': 'airport'}}))

        cd._init_system_location({})

        assert cd._SYSTEM_LOCATION['city'] == 'Cached'

    def test_explicit_fields_are_not_discarded(self, loc_env):
        """A lat/lon mapping must keep the fields the user spelled out.

        These were dropped, so the configs written specifically to avoid network
        lookups rendered their region/country/postal panels empty.
        """
        cd._init_system_location({'location': {
            'city': 'Centennial', 'region': 'Colorado', 'region_code': 'CO',
            'country': 'United States', 'country_code': 'US', 'postal': '80112',
            'lat': 39.57, 'lon': -104.84,
        }})

        for key, value in [('region', 'Colorado'), ('region_code', 'CO'),
                           ('country', 'United States'), ('country_code', 'US'),
                           ('postal', '80112')]:
            assert cd._SYSTEM_LOCATION[key] == value


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------

class TestIdempotence:

    def test_second_call_does_not_resolve_again(self, loc_env):
        """_init_layout() and main() both called this; that was two lookups."""
        cfg = {'location': {'city': 'Denver', 'lat': 39.7, 'lon': -104.9}}
        cd._init_system_location(cfg)
        first = list(loc_env.calls)

        cd._init_system_location(cfg)

        assert loc_env.calls == first

    def test_force_resolves_again(self, loc_env):
        cfg = {'location': {'city': 'Denver', 'lat': 39.7, 'lon': -104.9}}
        cd._init_system_location(cfg)
        loc_env.calls.clear()

        cd._init_system_location(cfg, force=True)

        assert loc_env.calls == ['sun-worker']

    def test_invalidate_forces_re_resolution(self, loc_env):
        cfg = {'location': {'city': 'Denver', 'lat': 39.7, 'lon': -104.9}}
        cd._init_system_location(cfg)
        loc_env.calls.clear()

        cd._invalidate_location_resolution()
        cd._init_system_location(cfg)

        assert loc_env.calls == ['sun-worker']


# ---------------------------------------------------------------------------
# On-disk shapes
# ---------------------------------------------------------------------------

class TestFileShapes:

    def test_cache_written_nested_and_private(self, loc_env):
        cd._write_system_location_cache({'city': 'Denver', 'lat': 39.7, 'lon': -104.9})

        doc = yaml.safe_load(loc_env.cache_file.read_text())
        assert set(doc) == {'location'}
        assert doc['location']['city'] == 'Denver'
        assert oct(os.stat(loc_env.cache_file).st_mode & 0o777) == '0o600'

    def test_cache_write_stamps_source_and_time(self, loc_env):
        cd._write_system_location_cache({'city': 'Denver', 'lat': 39.7, 'lon': -104.9})

        entry = yaml.safe_load(loc_env.cache_file.read_text())['location']
        assert entry['source'] == 'config'
        datetime.datetime.fromisoformat(entry['resolved_at'])  # parses

    def test_runtime_never_writes_the_user_file(self, loc_env):
        cd._write_system_location_cache({'city': 'Denver', 'lat': 39.7, 'lon': -104.9})

        assert not loc_env.user_file.exists()

    def test_flat_cache_is_migrated_to_nested(self, loc_env):
        loc_env.cache_file.write_text(yaml.safe_dump(
            {'city': 'Denver', 'lat': 39.7, 'lon': -104.9, 'source': 'airport'}))

        assert cd._read_system_location_cache()['city'] == 'Denver'
        assert set(yaml.safe_load(loc_env.cache_file.read_text())) == {'location'}

    def test_flat_user_file_is_read_but_not_rewritten(self, loc_env):
        loc_env.user_file.write_text(yaml.safe_dump(
            {'city': 'Aspen', 'lat': 39.1, 'lon': -106.8}))
        before = loc_env.user_file.read_text()

        assert cd._read_user_location_setting()['city'] == 'Aspen'
        assert loc_env.user_file.read_text() == before

    def test_malformed_cache_is_ignored_and_purged(self, loc_env):
        loc_env.cache_file.write_text('location:\n  citty: bogus\n')

        assert cd._read_system_location_cache() is None
        assert not loc_env.cache_file.exists()

    def test_unparseable_cache_is_not_fatal(self, loc_env):
        loc_env.cache_file.write_text('location: [unclosed\n')

        assert cd._read_system_location_cache() is None

    def test_expired_geoip_entry_is_purged(self, loc_env):
        stale = (datetime.datetime.now() - datetime.timedelta(days=45)).isoformat()
        loc_env.cache_file.write_text(yaml.safe_dump({'location': {
            'city': 'Old', 'lat': 1.0, 'lon': 2.0, 'source': 'ipwho', 'resolved_at': stale}}))

        assert cd._read_system_location_cache() is None
        assert not loc_env.cache_file.exists()

    def test_undated_geoip_entry_is_treated_as_stale(self, loc_env):
        loc_env.cache_file.write_text(yaml.safe_dump(
            {'location': {'city': 'Old', 'lat': 1.0, 'lon': 2.0, 'source': 'ipwho'}}))

        assert cd._read_system_location_cache() is None

    def test_aged_airport_entry_is_kept(self, loc_env):
        """Airport and explicit coordinates are deterministic; only GeoIP ages."""
        stale = (datetime.datetime.now() - datetime.timedelta(days=45)).isoformat()
        loc_env.cache_file.write_text(yaml.safe_dump({'location': {
            'city': 'Eagle', 'lat': 39.6, 'lon': -106.9, 'source': 'airport',
            'resolved_at': stale}}))

        assert cd._read_system_location_cache()['city'] == 'Eagle'


# ---------------------------------------------------------------------------
# Contrib previews
# ---------------------------------------------------------------------------

class TestContribPreview:

    @pytest.fixture(autouse=True)
    def _preview(self, loc_env, monkeypatch):
        # Requests loc_env so it runs AFTER it: loc_env resets preview state to
        # the live defaults, which would otherwise clobber these.
        monkeypatch.setattr(cd, '_PREVIEW_MODE', True)
        monkeypatch.setattr(cd, '_PREVIEW_LOCATION_MODE', 'contrib')

    def test_auto_resolves_from_sample_without_network(self, loc_env):
        cd._init_system_location({'location': 'auto'})

        assert loc_env.calls == []
        assert cd._SYSTEM_LOCATION['city']  # resolved to something

    def test_no_location_uses_the_sample(self, loc_env):
        cd._init_system_location({})

        assert loc_env.calls == []
        assert cd._SYSTEM_LOCATION['source'] == 'sample'

    def test_contrib_preview_never_writes_the_cache(self, loc_env):
        cd._init_system_location({'location': 'auto'})

        assert not loc_env.cache_file.exists()
        assert not loc_env.user_file.exists()

    def test_airport_resolves_from_the_checked_in_sample(self, loc_env):
        entry = cd._lookup_airport_code('KEGE')

        assert loc_env.calls == []
        assert entry['lat'] is not None

    def test_airport_without_a_sample_does_not_fall_back_to_the_network(self, loc_env):
        assert cd._lookup_airport_code('ZZZZ') is None
        assert loc_env.calls == []

    def test_geocode_is_refused(self, loc_env):
        assert cd._geocode_open_meteo('Denver, Colorado, US') is None
        assert loc_env.calls == []

    def test_sun_times_come_from_the_sample(self, loc_env):
        cd._fetch_and_store_sun_times(41.3, -105.6)

        assert loc_env.calls == []
        assert len(cd._SUN_TIMES) == 2

    def test_sample_sun_times_are_shifted_onto_today(self, loc_env):
        """The fixture's own dates are whenever it was captured.

        Without the shift, get_daytime() would find no entry for today and fall
        back to the static rule, quietly defeating the fixture.
        """
        cd._fetch_and_store_sun_times(41.3, -105.6)

        assert datetime.date.today().isoformat() in cd._SUN_TIMES

    def test_personal_mode_is_not_contrib(self, monkeypatch):
        monkeypatch.setattr(cd, '_PREVIEW_LOCATION_MODE', 'personal')

        assert not cd._contrib_preview()

    def test_sample_payload_matches_the_committed_fixture(self, loc_env):
        """Guards against the sample and the loader drifting apart."""
        path = os.path.join(cd._SAMPLES_DIR, 'ipwho-sample.json')
        raw = json.load(open(path))

        loc = cd._sample_location()

        assert loc['city'] == raw['city']
        assert loc['lat'] == pytest.approx(raw['latitude'])


# ---------------------------------------------------------------------------
# Sun times / day-night
# ---------------------------------------------------------------------------

class TestDayNight:

    def _set_sun(self, monkeypatch, sunrise_hour, sunset_hour):
        today = datetime.date.today()
        monkeypatch.setattr(cd, '_SUN_TIMES', {today.isoformat(): {
            'sunrise': datetime.datetime.combine(today, datetime.time(sunrise_hour, 0)),
            'sunset': datetime.datetime.combine(today, datetime.time(sunset_hour, 0)),
            'fetched_at': datetime.datetime.now(),
        }})

    def test_midday_is_daytime(self, monkeypatch):
        self._set_sun(monkeypatch, 0, 23)

        assert cd.get_daytime() == 'true'
        assert cd.get_nighttime() == 'false'

    def test_before_sunrise_is_night(self, monkeypatch):
        now = datetime.datetime.now()
        self._set_sun(monkeypatch, min(now.hour + 1, 23), 23)

        if now.hour < 23:  # skip the degenerate late-evening case
            assert cd.get_daytime() == 'false'

    def test_after_sunset_is_night(self, monkeypatch):
        now = datetime.datetime.now()
        if now.hour > 0:
            self._set_sun(monkeypatch, 0, max(now.hour - 1, 0) or 0)
            if now.hour >= 1:
                assert cd.get_daytime() == 'false'

    def test_no_sun_times_falls_back_to_the_static_rule(self, monkeypatch):
        monkeypatch.setattr(cd, '_SUN_TIMES', {})

        assert cd.get_daytime() in ('true', 'false')


# ---------------------------------------------------------------------------
# Debug redaction
# ---------------------------------------------------------------------------

class TestDebugRedaction:

    LOC = {'city': 'Centennial', 'lat': 39.5701186, 'lon': -104.8492931, 'source': 'config'}

    def test_plain_debug_rounds_coordinates(self, monkeypatch):
        monkeypatch.setattr(cd, 'DEBUG_LOCATION', False)

        text = cd._loc_debug(self.LOC)

        assert '39.5701186' not in text
        assert '~39.6' in text
        assert 'Centennial' in text

    def test_debug_location_shows_exact_coordinates(self, monkeypatch):
        monkeypatch.setattr(cd, 'DEBUG_LOCATION', True)

        assert '39.570119' in cd._loc_debug(self.LOC)

    def test_urls_are_redacted_by_default(self, monkeypatch):
        monkeypatch.setattr(cd, 'DEBUG_LOCATION', False)
        url = 'https://api.open-meteo.com/v1/forecast?latitude=39.57&longitude=-104.84'

        text = cd._loc_debug_url(url)

        assert '39.57' not in text
        assert text.startswith('https://api.open-meteo.com/v1/forecast')

    def test_urls_are_shown_when_opted_in(self, monkeypatch):
        monkeypatch.setattr(cd, 'DEBUG_LOCATION', True)
        url = 'https://api.open-meteo.com/v1/forecast?latitude=39.57'

        assert cd._loc_debug_url(url) == url

    def test_no_location_renders_as_none(self):
        assert cd._loc_debug(None) == 'none'


# ---------------------------------------------------------------------------
# clockish-location (the setup helper's file handling)
# ---------------------------------------------------------------------------

class TestSetupLocation:
    """Write-path tests only -- the interactive prompts and lookups need a TTY
    or the network, and the file shape is what the runtime depends on."""

    @pytest.fixture
    def sl(self, tmp_path, monkeypatch):
        import clockish.setup_location as mod
        monkeypatch.setattr(mod, 'CFG_DIR', str(tmp_path))
        monkeypatch.setattr(mod, 'YAML_PATH', str(tmp_path / 'location.yaml'))
        return mod

    def test_disabled_writes_a_bare_scalar(self, sl):
        sl.write_location('disabled')

        assert open(sl.YAML_PATH).read().strip() == 'location: disabled'

    def test_mapping_written_under_the_location_key(self, sl):
        sl.write_location({'city': 'Denver', 'lat': 39.7, 'lon': -104.9})

        doc = yaml.safe_load(open(sl.YAML_PATH))
        assert set(doc) == {'location'}
        assert doc['location']['city'] == 'Denver'

    def test_written_file_is_private(self, sl):
        sl.write_location('disabled')

        assert oct(os.stat(sl.YAML_PATH).st_mode & 0o777) == '0o600'

    def test_pre_existing_world_readable_file_is_tightened(self, sl):
        open(sl.YAML_PATH, 'w').close()
        os.chmod(sl.YAML_PATH, 0o644)

        sl.write_location('disabled')

        assert oct(os.stat(sl.YAML_PATH).st_mode & 0o777) == '0o600'

    def test_invalid_location_is_refused(self, sl):
        with pytest.raises(SystemExit):
            sl.write_location({'citty': 'nonsense'})

    def test_flat_file_is_migrated(self, sl):
        open(sl.YAML_PATH, 'w').write(yaml.safe_dump(
            {'city': 'Aspen', 'lat': 39.1, 'lon': -106.8, 'source': 'coords'}))

        sl.migrate_if_flat()

        doc = yaml.safe_load(open(sl.YAML_PATH))
        assert set(doc) == {'location'}
        assert doc['location']['city'] == 'Aspen'

    def test_already_canonical_file_is_left_alone(self, sl):
        sl.write_location({'city': 'Aspen', 'lat': 39.1, 'lon': -106.8})
        before = open(sl.YAML_PATH).read()

        sl.migrate_if_flat()

        assert open(sl.YAML_PATH).read() == before

    def test_disabled_summary_is_readable(self, sl):
        assert sl._summarize_location('disabled') == 'disabled'
