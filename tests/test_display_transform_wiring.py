"""tests/test_display_transform_wiring.py

Regression tests proving `transform:` actually reaches the rendered text for
each supported panel type (clock, date, fact, text -- including a `fact`
panel backed by a `cached-facts.*` source) -- not just that config_validator.py
accepts it. This guards against the transform application call sites
silently disappearing from display.py (has happened once already during
development).

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
                              justify='center', behavior='default', img=None,
                              measure_text=None):
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
        monkeypatch.setattr(cd, '_get_fact', lambda source, options=None: 'hello world')
        panel = {'source': 'hostname', 'transform': ['titlecase']}
        cd._render_fact_panel(panel, 0, 0, 100, 40, d=None)
        assert calls[0] == 'HelloWorld'

    def test_no_transform_keeps_raw_fact_value(self, monkeypatch):
        calls = _capture_draw_text(monkeypatch)
        monkeypatch.setattr(cd, '_get_fact', lambda source, options=None: 'hello world')
        panel = {'source': 'hostname'}
        cd._render_fact_panel(panel, 0, 0, 100, 40, d=None)
        assert calls[0] == 'hello world'


class TestCachedFactsFactTransform:
    """fact panel with source: cached-facts.<name> -- extraction (json_path/
    pattern) happens at render time from the shared cached-facts raw value;
    no network I/O, no background thread (that's covered separately in
    test_config_validator.py / a dedicated cached-facts test module)."""

    def _fresh_cache(self, monkeypatch, raw: str):
        monkeypatch.setattr(cd, '_cached_facts_cache', {'weather': {'raw': raw, 'ok': True}})

    def test_transform_rounds_extracted_value(self, monkeypatch):
        calls = _capture_draw_text(monkeypatch)
        self._fresh_cache(monkeypatch, '{"ip": "71.8"}')
        panel = {'source': 'cached-facts.weather', 'json_path': 'ip', 'transform': ['round']}
        cd._render_fact_panel(panel, 0, 0, 100, 40, d=None)
        assert calls[0] == '72'

    def test_no_transform_keeps_raw_extracted_value(self, monkeypatch):
        calls = _capture_draw_text(monkeypatch)
        self._fresh_cache(monkeypatch, '{"ip": "71.8"}')
        panel = {'source': 'cached-facts.weather', 'json_path': 'ip'}
        cd._render_fact_panel(panel, 0, 0, 100, 40, d=None)
        assert calls[0] == '71.8'

    def test_pattern_extraction(self, monkeypatch):
        calls = _capture_draw_text(monkeypatch)
        self._fresh_cache(monkeypatch, '<title>Example Domain</title>')
        panel = {'source': 'cached-facts.weather', 'pattern': r'<title>([^<]+)</title>'}
        cd._render_fact_panel(panel, 0, 0, 100, 40, d=None)
        assert calls[0] == 'Example Domain'

    def test_no_data_yet_renders_empty_string(self, monkeypatch):
        """Before the background thread's first successful fetch, raw is None
        -- fact panel shows an empty string (no 'fallback' concept)."""
        calls = _capture_draw_text(monkeypatch)
        monkeypatch.setattr(cd, '_cached_facts_cache', {})
        panel = {'source': 'cached-facts.weather', 'json_path': 'ip'}
        cd._render_fact_panel(panel, 0, 0, 100, 40, d=None)
        assert calls[0] == ''

    def test_transform_reapplied_each_render(self, monkeypatch):
        """Editing transform (e.g. via config reload) should affect display
        even though the underlying cached-facts raw value never changes --
        extraction + transform both happen fresh every render."""
        calls = _capture_draw_text(monkeypatch)
        self._fresh_cache(monkeypatch, '{"ip": "71.8"}')
        panel = {'source': 'cached-facts.weather', 'json_path': 'ip', 'transform': ['round']}
        cd._render_fact_panel(panel, 0, 0, 100, 40, d=None)
        cd._render_fact_panel(panel, 0, 0, 100, 40, d=None)
        assert calls == ['72', '72']


class TestTextTransform:
    def test_transform_applied_to_static_label(self, monkeypatch):
        calls = _capture_draw_text(monkeypatch)
        panel = {'label': 'HELLO', 'transform': ['lower']}
        cd._render_text_panel(panel, 0, 0, 100, 40, d=None)
        assert calls[0] == 'hello'

    def test_camelcase_vs_titlecase_on_text_panel(self, monkeypatch):
        calls = _capture_draw_text(monkeypatch)
        panel_camel = {'label': 'hello world', 'transform': ['camelcase']}
        panel_title = {'label': 'hello world', 'transform': ['titlecase']}
        cd._render_text_panel(panel_camel, 0, 0, 100, 40, d=None)
        cd._render_text_panel(panel_title, 0, 0, 100, 40, d=None)
        assert calls == ['helloWorld', 'HelloWorld']
