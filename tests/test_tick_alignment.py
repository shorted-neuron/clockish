"""tests/test_tick_alignment.py
==============================
Wall-clock tick stepping (``_next_tick``) and the ``show_rows(at=, push_at=)``
contract: render the given second, ``prepare()`` before sleeping, sleep to
``push_at``, then ``push()`` the prepared frame.  See
docs/how_tick_alignment_works.md.
"""
from __future__ import annotations

import datetime
import zoneinfo

import pytest

import clockish.display as d


class TestNextTick:

    def test_steady_state_advances_one_second(self) -> None:
        # Last push straddled 100; loop comes back just after the boundary.
        assert d._next_tick(100, 100.03) == 101

    def test_push_ending_short_of_boundary_does_not_repeat(self) -> None:
        # Fast display: back round before 100 actually arrived.
        assert d._next_tick(100, 99.99) == 101

    def test_stall_skips_ahead_to_next_whole_second(self) -> None:
        # Config reload or slow frame ate several seconds.
        assert d._next_tick(100, 104.5) == 105

    def test_first_pass_from_zero(self) -> None:
        assert d._next_tick(0, 1_790_000_000.4) == 1_790_000_001

    def test_clock_stepped_back_resyncs(self) -> None:
        # Wall clock jumped back an hour; must not sleep until 3701 comes round.
        assert d._next_tick(3700, 100.2) == 101

    def test_small_step_back_still_never_repeats(self) -> None:
        # Within the slack: keep counting up rather than redraw a second.
        assert d._next_tick(100, 99.5) == 101


class TestNowInTz:

    def test_local_at(self) -> None:
        at = 1_790_000_000
        assert d._now_in_tz('local', at) == datetime.datetime.fromtimestamp(at)

    def test_named_zone_at(self) -> None:
        at = 1_790_000_000
        tz = zoneinfo.ZoneInfo('America/Denver')
        assert d._now_in_tz('America/Denver', at) == datetime.datetime.fromtimestamp(at, tz)


class _RecordingLcd:
    def __init__(self, events: list) -> None:
        self.events = events

    def prepare(self, image):
        self.events.append(('prepare', image))
        return 'FRAME'

    def push(self, frame) -> None:
        self.events.append(('push', frame))


@pytest.fixture
def harness(monkeypatch):
    """show_rows() against a recording lcd, fake clock and no real sleep."""
    events: list = []
    now = {'t': 1000.25}

    def fake_sleep(s: float) -> None:
        events.append(('sleep', s))
        now['t'] += s

    monkeypatch.setattr(d, 'lcd', _RecordingLcd(events))
    monkeypatch.setattr(d, '_LAYOUT', [])
    monkeypatch.setattr(d, 'DEBUG', False)
    monkeypatch.setattr(d, '_avg_display_ms', None)
    monkeypatch.setattr(d, 'get_ntp_status', lambda: '')
    monkeypatch.setattr(d, 'get_ntp_upstream_count', lambda: '')
    monkeypatch.setattr(d.time, 'time', lambda: now['t'])
    monkeypatch.setattr(d.time, 'sleep', fake_sleep)
    return events


class TestShowRowsContract:

    def test_prepare_then_sleep_then_push_prepared_frame(self, harness) -> None:
        d.show_rows(at=1001, push_at=1000.95)
        kinds = [e[0] for e in harness]
        assert kinds == ['prepare', 'sleep', 'push']
        assert harness[0][1] is d.image
        assert harness[1][1] == pytest.approx(0.70)
        assert harness[2][1] == 'FRAME'

    def test_push_at_in_past_pushes_without_sleeping(self, harness) -> None:
        d.show_rows(at=1001, push_at=999.0)
        assert [e[0] for e in harness] == ['prepare', 'push']

    def test_no_push_at_pushes_immediately(self, harness) -> None:
        d.show_rows()
        assert [e[0] for e in harness] == ['prepare', 'push']

    def test_avg_display_ms_tracks_push_cost(self, harness) -> None:
        d.show_rows()
        first = d._avg_display_ms
        assert first is not None and first >= 0
        d.show_rows()
        assert d._avg_display_ms == pytest.approx((first + d._last_display_ms) / 2)

    def test_clock_panels_get_at_not_now(self, harness, monkeypatch) -> None:
        seen: dict = {}

        def fake_render_row(r, row_idx, ry, rw, rh, tz_cache, timings, t0):
            seen.update(tz_cache)

        monkeypatch.setattr(d, '_render_row', fake_render_row)
        monkeypatch.setattr(d, '_LAYOUT', [({'panels': [
            {'type': 'clock'},
            {'type': 'date', 'timezone': 'UTC'},
        ]}, 0, 10)])
        d.show_rows(at=1_790_000_000, push_at=None)
        assert seen['local'] == datetime.datetime.fromtimestamp(1_790_000_000)
        assert seen['UTC'] == datetime.datetime.fromtimestamp(
            1_790_000_000, zoneinfo.ZoneInfo('UTC'))
