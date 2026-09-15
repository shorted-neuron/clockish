"""tests/test_backlight.py

Tests for resolve_scheduled_value() in clockish/backlight.py -- the pure
function that picks a brightness level from a `backlight.schedule:` list for
a given time of day.
"""
import datetime

from clockish.backlight import resolve_scheduled_value

SCHEDULE = [
    {'name': 'night', 'start': '22:00', 'end': '06:59', 'value': 42},
    {'name': 'day', 'start': '07:00', 'end': '19:59', 'value': 255},
]


def _at(hh: int, mm: int) -> datetime.datetime:
    return datetime.datetime(2026, 1, 1, hh, mm)


class TestResolveScheduledValue:
    def test_within_day_entry(self):
        assert resolve_scheduled_value(SCHEDULE, min_=40, max_=255, now=_at(12, 0)) == 255

    def test_within_night_entry_wraps_midnight(self):
        assert resolve_scheduled_value(SCHEDULE, min_=40, max_=255, now=_at(23, 30)) == 42
        assert resolve_scheduled_value(SCHEDULE, min_=40, max_=255, now=_at(3, 0)) == 42

    def test_day_start_boundary_inclusive(self):
        assert resolve_scheduled_value(SCHEDULE, min_=40, max_=255, now=_at(7, 0)) == 255

    def test_day_end_boundary_inclusive(self):
        assert resolve_scheduled_value(SCHEDULE, min_=40, max_=255, now=_at(19, 59)) == 255

    def test_night_start_boundary_inclusive(self):
        assert resolve_scheduled_value(SCHEDULE, min_=40, max_=255, now=_at(22, 0)) == 42

    def test_night_end_boundary_inclusive(self):
        assert resolve_scheduled_value(SCHEDULE, min_=40, max_=255, now=_at(6, 59)) == 42

    def test_gap_falls_back_to_midpoint(self):
        gappy = [{'name': 'day', 'start': '07:00', 'end': '19:59', 'value': 255}]
        # 20:00-06:59 is uncovered -> midpoint of min/max
        assert resolve_scheduled_value(gappy, min_=40, max_=255, now=_at(21, 0)) == 148

    def test_gap_midpoint_is_int_not_float(self):
        gappy = [{'name': 'day', 'start': '07:00', 'end': '19:59', 'value': 255}]
        value = resolve_scheduled_value(gappy, min_=40, max_=255, now=_at(21, 0))
        assert isinstance(value, int)

    def test_gap_midpoint_rounds(self):
        # (40 + 255) / 2 = 147.5 -> rounds to 148
        gappy = [{'name': 'day', 'start': '07:00', 'end': '19:59', 'value': 255}]
        assert resolve_scheduled_value(gappy, min_=40, max_=255, now=_at(0, 0)) == 148

    def test_empty_schedule_falls_back_to_midpoint(self):
        assert resolve_scheduled_value([], min_=0, max_=255, now=_at(12, 0)) == 128

    def test_first_matching_entry_wins_on_overlap(self):
        overlapping = [
            {'name': 'a', 'start': '00:00', 'end': '23:59', 'value': 1},
            {'name': 'b', 'start': '10:00', 'end': '11:00', 'value': 2},
        ]
        assert resolve_scheduled_value(overlapping, min_=0, max_=255, now=_at(10, 30)) == 1
