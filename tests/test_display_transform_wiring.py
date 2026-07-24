"""tests/test_display_transform_wiring.py

Regression tests proving `transform:` actually reaches the rendered text for
each supported panel type (clock, date, fact, url-fact, text) -- not just
that config_validator.py accepts it. This guards against the transform
application call sites silently disappearing from display.py (has happened
once already during development).

Renderers are exercised directly with `_draw_text_line` and font/fetch
helpers monkeypatched out, so no real fonts, files, or network I/O are
touched.
"""
import datetime

import clockish.display as cd


def _capture_draw_text(monkeypatch):
    """Monkeypatch _draw_text_line to record every text string it's asked to draw."""
    calls: list[str] = []

    def _fake_draw_text_line(d, px, py, pw, ph, text, f, color, x_offset=0,
                              justify='center', behavior='default'):
        calls.append(text)

    monkeypatch.setattr(cd, '_draw_text_line', _fake_draw_text_line)
    monkeypatch.setattr(cd, '_get_font', lambda name: object())
    return calls


class TestClockTransform:
    def test_transform_lowercases_rendered_time(self, monkeypatch):
        calls = _capture_draw_text(monkeypatch)
        panel = {'time_format': '%I:%M %p', 'transform': ['lower']}
        now = datetime.datetime(2024, 1, 1, 13, 5)
        cd._render_clock_panel(panel, 0, 0, 100, 40, now, d=None)
        assert calls, "expected _draw_text_line to be called"
        assert calls[0] == '01:05 pm'

    def test_no_transform_keeps_uppercase_default(self, monkeypatch):
        calls = _capture_draw_text(monkeypatch)
        panel = {'time_format': '%I:%M %p'}
        now = datetime.datetime(2024, 1, 1, 13, 5)
        cd._render_clock_panel(panel, 0, 0, 100, 40, now, d=None)
        assert calls[0] == '01:05 PM'


class TestDateTransform:
    def test_transform_uppercases_rendered_date(self, monkeypatch):
        calls = _capture_draw_text(monkeypatch)
        panel = {'date_format': '%A', 'transform': ['upper']}
        now = datetime.datetime(2024, 1, 1)  # a Monday
        cd._render_date_panel(panel, 0, 0, 100, 40, now, d=None)
        assert calls[0] == 'MONDAY'

    def test_no_transform_keeps_original_case(self, monkeypatch):
        calls = _capture_draw_text(monkeypatch)
        panel = {'date_format': '%A'}
        now = datetime.datetime(2024, 1, 1)  # a Monday
        cd._render_date_panel(panel, 0, 0, 100, 40, now, d=None)
        assert calls[0] == 'Monday'


class TestFactTransform:
    def test_transform_applied_to_fact_value(self, monkeypatch):
        calls = _capture_draw_text(monkeypatch)
        monkeypatch.setattr(cd, '_get_fact', lambda source: 'hello world')
        panel = {'source': 'hostname', 'transform': ['titlecase']}
        cd._render_fact_panel(panel, 0, 0, 100, 40, d=None)
        assert calls[0] == 'HelloWorld'

    def test_no_transform_keeps_raw_fact_value(self, monkeypatch):
        calls = _capture_draw_text(monkeypatch)
        monkeypatch.setattr(cd, '_get_fact', lambda source: 'hello world')
        panel = {'source': 'hostname'}
        cd._render_fact_panel(panel, 0, 0, 100, 40, d=None)
        assert calls[0] == 'hello world'


class TestUrlFactTransform:
    def _fresh_panel(self, **overrides):
        panel = {
            'url': 'https://example.com',
            'json_path': 'ip',
        }
        panel.update(overrides)
        return panel

    def test_transform_rounds_fetched_value(self, monkeypatch):
        calls = _capture_draw_text(monkeypatch)
        monkeypatch.setattr(cd, '_fetch_and_extract', lambda *a, **k: '71.8')
        # Ensure a clean cache slot for this specific panel dict instance.
        panel = self._fresh_panel(transform=['round'])
        cd._remote_fact_cache.pop(id(panel), None)
        cd._render_url_fact_panel(panel, 0, 0, 100, 40, d=None)
        assert calls[0] == '72'

    def test_no_transform_keeps_raw_fetched_value(self, monkeypatch):
        calls = _capture_draw_text(monkeypatch)
        monkeypatch.setattr(cd, '_fetch_and_extract', lambda *a, **k: '71.8')
        panel = self._fresh_panel()
        cd._remote_fact_cache.pop(id(panel), None)
        cd._render_url_fact_panel(panel, 0, 0, 100, 40, d=None)
        assert calls[0] == '71.8'

    def test_transform_reapplied_on_cached_value_without_refetch(self, monkeypatch):
        """Editing transform (e.g. via config reload) should affect display
        even for an already-cached raw value -- no need to invalidate cache."""
        calls = _capture_draw_text(monkeypatch)
        fetch_calls = []

        def _fake_fetch(*a, **k):
            fetch_calls.append(1)
            return '71.8'

        monkeypatch.setattr(cd, '_fetch_and_extract', _fake_fetch)
        panel = self._fresh_panel(transform=['round'])
        cd._remote_fact_cache.pop(id(panel), None)

        cd._render_url_fact_panel(panel, 0, 0, 100, 40, d=None)  # first: fetches + caches raw
        cd._render_url_fact_panel(panel, 0, 0, 100, 40, d=None)  # second: cache hit, still transforms

        assert fetch_calls == [1]  # only fetched once (interval not expired)
        assert calls == ['72', '72']


class TestTextTransform:
    def test_transform_applied_to_static_label(self, monkeypatch):
        calls = _capture_draw_text(monkeypatch)
        panel = {'label': 'HELLO', 'transform': ['lower']}
        cd._render_text_panel(panel, 0, 0, 100, 40, d=None)
        assert calls[0] == 'hello'

    def test_camelcase_vs_titlecase_on_text_panel(self, monkeypatch):
        calls = _capture_draw_text(monkeypatch)
        cd._render_text_panel({'label': 'hello world', 'transform': ['camelcase']}, 0, 0, 100, 40, d=None)
        cd._render_text_panel({'label': 'hello world', 'transform': ['titlecase']}, 0, 0, 100, 40, d=None)
        assert calls == ['helloWorld', 'HelloWorld']
