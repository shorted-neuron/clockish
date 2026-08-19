"""tests/test_cached_facts.py

Tests for the cached-facts background-thread fetch machinery in display.py:
  - _init_cached_facts() in preview mode (synchronous fetch / preview_response,
    no threads spawned)
  - _init_cached_facts() in live mode (daemon threads spawned, staggered,
    populate the shared cache without blocking)
  - _cached_fact_worker()'s retry/backoff behavior on fetch failure
  - _handle_sigusr1() waking a worker early for an immediate refetch

No real network I/O -- _fetch_url_raw is monkeypatched throughout.
"""
import time

import clockish.display as cd


def _reset_cached_facts(monkeypatch):
    monkeypatch.setattr(cd, '_cached_facts_cache', {})
    monkeypatch.setattr(cd, '_cached_facts_events', {})
    monkeypatch.setattr(cd, '_cached_facts_threads', {})


class TestPreviewMode:
    def test_preview_response_used_verbatim_no_network(self, monkeypatch):
        _reset_cached_facts(monkeypatch)
        monkeypatch.setattr(cd, '_PREVIEW_MODE', True)

        calls = []
        monkeypatch.setattr(cd, '_fetch_url_raw', lambda *a, **k: calls.append(1) or ('LIVE', 200))

        cfg = {'cached-facts': [
            {'name': 'weather', 'type': 'url-fact', 'url': 'https://example.com',
             'preview_response': '{"temp": 71.8}'},
        ]}
        cd._init_cached_facts(cfg)

        assert cd._cached_facts_cache['weather'] == {'raw': '{"temp": 71.8}', 'ok': True}
        assert calls == []  # never touched the network
        assert cd._cached_facts_threads == {}  # no background thread in preview mode

    def test_no_preview_response_fetches_synchronously(self, monkeypatch):
        _reset_cached_facts(monkeypatch)
        monkeypatch.setattr(cd, '_PREVIEW_MODE', True)
        monkeypatch.setattr(cd, '_fetch_url_raw', lambda url, timeout, verify_ssl: ('RAW', 200))

        cfg = {'cached-facts': [
            {'name': 'weather', 'type': 'url-fact', 'url': 'https://example.com'},
        ]}
        cd._init_cached_facts(cfg)

        assert cd._cached_facts_cache['weather'] == {'raw': 'RAW', 'ok': True}
        assert cd._cached_facts_threads == {}

    def test_synchronous_fetch_failure_leaves_raw_none(self, monkeypatch):
        _reset_cached_facts(monkeypatch)
        monkeypatch.setattr(cd, '_PREVIEW_MODE', True)
        monkeypatch.setattr(cd, '_fetch_url_raw', lambda url, timeout, verify_ssl: (None, None))

        cfg = {'cached-facts': [
            {'name': 'weather', 'type': 'url-fact', 'url': 'https://example.com'},
        ]}
        cd._init_cached_facts(cfg)

        assert cd._cached_facts_cache['weather'] == {'raw': None, 'ok': False}


class TestLiveMode:
    def test_spawns_daemon_thread_and_populates_cache(self, monkeypatch):
        _reset_cached_facts(monkeypatch)
        monkeypatch.setattr(cd, '_PREVIEW_MODE', False)
        monkeypatch.setattr(cd, '_fetch_url_raw', lambda url, timeout, verify_ssl: ('OK', 200))

        cfg = {'cached-facts': [
            {'name': 'weather', 'type': 'url-fact', 'url': 'https://example.com', 'interval': '5m'},
        ]}
        cd._init_cached_facts(cfg)

        assert 'weather' in cd._cached_facts_threads
        t = cd._cached_facts_threads['weather']
        assert t.daemon is True

        # Give the worker thread a moment to complete its first (unstaggered,
        # single-entry) fetch.
        for _ in range(50):
            if cd._cached_facts_cache.get('weather', {}).get('raw') is not None:
                break
            time.sleep(0.01)

        assert cd._cached_facts_cache['weather'] == {'raw': 'OK', 'ok': True}

    def test_sigusr1_wakes_worker_for_immediate_refetch(self, monkeypatch):
        _reset_cached_facts(monkeypatch)
        monkeypatch.setattr(cd, '_PREVIEW_MODE', False)

        fetch_count = {'n': 0}

        def _fake_fetch(url, timeout, verify_ssl):
            fetch_count['n'] += 1
            return (f'fetch-{fetch_count["n"]}', 200)

        monkeypatch.setattr(cd, '_fetch_url_raw', _fake_fetch)

        # Huge interval so the worker would NOT refetch on its own within
        # this test's lifetime -- only SIGUSR1 should trigger the 2nd fetch.
        cfg = {'cached-facts': [
            {'name': 'weather', 'type': 'url-fact', 'url': 'https://example.com', 'interval': '1h'},
        ]}
        cd._init_cached_facts(cfg)

        for _ in range(50):
            if fetch_count['n'] >= 1:
                break
            time.sleep(0.01)
        assert fetch_count['n'] == 1

        cd._handle_sigusr1(None, None)

        for _ in range(50):
            if fetch_count['n'] >= 2:
                break
            time.sleep(0.01)
        assert fetch_count['n'] == 2


class TestRetryBackoff:
    def test_failure_keeps_previous_raw_value(self, monkeypatch):
        _reset_cached_facts(monkeypatch)
        monkeypatch.setattr(cd, '_PREVIEW_MODE', False)

        responses = iter([('first', 200), (None, None)])
        monkeypatch.setattr(cd, '_fetch_url_raw', lambda *a, **k: next(responses))

        cfg = {'cached-facts': [
            {'name': 'weather', 'type': 'url-fact', 'url': 'https://example.com', 'interval': '1h'},
        ]}
        cd._init_cached_facts(cfg)

        for _ in range(50):
            if cd._cached_facts_cache.get('weather', {}).get('raw') == 'first':
                break
            time.sleep(0.01)
        assert cd._cached_facts_cache['weather'] == {'raw': 'first', 'ok': True}

        # Wake the worker; it will fetch again (fails this time) but must
        # keep showing the last good raw value instead of clearing it.
        cd._handle_sigusr1(None, None)
        for _ in range(50):
            if cd._cached_facts_cache.get('weather', {}).get('ok') is False:
                break
            time.sleep(0.01)
        assert cd._cached_facts_cache['weather'] == {'raw': 'first', 'ok': False}
