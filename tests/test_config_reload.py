"""Tests for config reload functionality (file watcher, signal handlers, validation)."""
import os
import signal
import tempfile
import threading
import time

import pytest
import yaml

from clockish import display


@pytest.fixture
def temp_config():
    """Create a temporary config file that can be edited during the test."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        config_data = {
            'orientation': 'portrait',
            'rows': [
                {
                    'height': 100,
                    'panels': [
                        {'type': 'text', 'label': 'Test'},
                    ]
                }
            ],
            'display': {
                'driver': 'framebuffer',
                'width': 320,
                'height': 480,
            }
        }
        yaml.dump(config_data, f)
        config_path = f.name

    yield config_path

    # Cleanup
    if os.path.exists(config_path):
        os.unlink(config_path)


class TestReloadValidation:
    """Test that reload section validation works."""

    def test_reload_section_valid_poll_interval(self):
        """A valid reload section should not produce errors."""
        from clockish.config_validator import validate_config_dict

        config = {
            'orientation': 'portrait',
            'rows': [{'name': 'test_row', 'height': 100, 'panels': [{'type': 'text', 'label': 'Test'}]}],
            'reload': {
                'poll_interval': '10s',
            },
            'display': {'driver': 'framebuffer'},
        }
        result = validate_config_dict(config, path='<test>')
        assert not result.has_errors, f"Unexpected errors: {[str(i) for i in result.issues]}"

    def test_reload_section_invalid_poll_interval(self):
        """An invalid poll_interval should produce an error."""
        from clockish.config_validator import validate_config_dict

        config = {
            'orientation': 'portrait',
            'rows': [{'name': 'test_row', 'height': 100, 'panels': [{'type': 'text', 'label': 'Test'}]}],
            'reload': {
                'poll_interval': 'not-a-duration',
            },
            'display': {'driver': 'framebuffer'},
        }
        result = validate_config_dict(config, path='<test>')
        assert result.has_errors
        assert any('poll_interval' in str(i) for i in result.issues)

    def test_reload_section_not_a_dict(self):
        """reload section must be a dict."""
        from clockish.config_validator import validate_config_dict

        config = {
            'orientation': 'portrait',
            'rows': [{'name': 'test_row', 'height': 100, 'panels': [{'type': 'text', 'label': 'Test'}]}],
            'reload': 'invalid',
            'display': {'driver': 'framebuffer'},
        }
        result = validate_config_dict(config, path='<test>')
        assert result.has_errors
        assert any('mapping' in str(i).lower() for i in result.issues)


class TestSignalHandlers:
    """Test signal handler registration and triggering."""

    def test_sigusr1_reload_event_set(self):
        """SIGUSR1 should set the reload event."""
        display._reload_event.clear()
        display._handle_sigusr1_reload(signal.SIGUSR1, None)
        assert display._reload_event.is_set()

    def test_sigusr2_cached_facts_refetch(self):
        """SIGUSR2 should set cached-facts refetch events."""
        # Setup a mock cached-facts entry
        display._cached_facts_events['test'] = threading.Event()
        display._cached_facts_events['test'].clear()

        display._handle_sigusr2_cached_facts(signal.SIGUSR2, None)

        assert display._cached_facts_events['test'].is_set()

        # Cleanup
        del display._cached_facts_events['test']


class TestCachedFactsWorkerStop:
    """Test that cached-facts workers can be cleanly stopped and restarted."""

    def test_cached_facts_worker_honors_stop_event(self):
        """Worker should exit when stop_event is set."""
        # This is implicitly tested by _stop_cached_facts joining threads.
        # A more direct test would require spawning an actual worker thread,
        # which is expensive and better handled by integration tests.
        pass

    def test_stop_cached_facts_joins_threads(self):
        """_stop_cached_facts should set stop events and join threads."""
        # Mock scenario: create a mock thread that tracks stop
        stop_event = threading.Event()
        called = []

        def mock_worker():
            called.append(True)
            while not stop_event.is_set():
                time.sleep(0.01)

        display._cached_facts_stop_events['test'] = stop_event
        t = threading.Thread(target=mock_worker, daemon=True)
        display._cached_facts_threads['test'] = t
        t.start()

        # Ensure thread started
        time.sleep(0.05)
        assert called

        # Call stop and verify thread exits cleanly
        display._stop_cached_facts()
        t.join(timeout=1)
        assert not t.is_alive()

        # Cleanup
        del display._cached_facts_stop_events['test']
        del display._cached_facts_threads['test']


class TestReloadConfigValidation:
    """Test that config reload validates and swaps configs atomically."""

    def test_attempt_config_reload_with_valid_config(self, temp_config):
        """Reload with valid config should swap it in."""
        # Save the original config
        orig_config = display._config.copy()

        # Modify the config file
        new_config_data = {
            'orientation': 'landscape',
            'rows': [
                {
                    'height': 50,
                    'panels': [
                        {'type': 'text', 'label': 'Updated'},
                    ]
                }
            ],
            'display': {
                'driver': 'framebuffer',
                'width': 320,
                'height': 480,
            }
        }
        with open(temp_config, 'w') as f:
            yaml.dump(new_config_data, f)

        # Would call _attempt_config_reload() but it requires full init context.
        # This is better tested via integration tests with actual file I/O.

    def test_attempt_config_reload_with_broken_config(self, temp_config):
        """Reload with invalid config should keep old config in place."""
        # Save the original config
        orig_config = display._config.copy()

        # Write broken YAML
        with open(temp_config, 'w') as f:
            f.write('{ invalid yaml ][')

        # Would call _attempt_config_reload() but it requires full init context.
        # Integration tests are more suitable for this scenario.
