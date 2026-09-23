"""tests/test_backlight.py

Tests for the pure brightness-resolution functions in clockish/backlight.py:
  - resolve_scheduled_value() -- fixed day/night `schedule:` list
  - resolve_sun_curve_value() -- sun-following `curve: sun` cosine ease
"""
import datetime

from clockish.backlight import resolve_scheduled_value, resolve_sun_curve_value

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


# Sunrise 07:00, sunset 19:00 -- solar noon at 13:00, 12h of daylight.
SUNRISE = _at(7, 0)
SUNSET = _at(19, 0)


class TestResolveSunCurveValue:
    def test_before_sunrise_is_min(self):
        assert resolve_sun_curve_value(SUNRISE, SUNSET, min_=40, max_=255, now=_at(5, 0)) == 40

    def test_at_and_after_sunset_is_min(self):
        assert resolve_sun_curve_value(SUNRISE, SUNSET, min_=40, max_=255, now=SUNSET) == 40
        assert resolve_sun_curve_value(SUNRISE, SUNSET, min_=40, max_=255, now=_at(21, 0)) == 40

    def test_at_sunrise_is_min(self):
        assert resolve_sun_curve_value(SUNRISE, SUNSET, min_=40, max_=255, now=SUNRISE) == 40

    def test_at_solar_noon_is_max(self):
        solar_noon = SUNRISE + (SUNSET - SUNRISE) / 2
        assert resolve_sun_curve_value(SUNRISE, SUNSET, min_=40, max_=255, now=solar_noon) == 255

    def test_curve_is_symmetric_around_solar_noon(self):
        solar_noon = SUNRISE + (SUNSET - SUNRISE) / 2
        offset = datetime.timedelta(hours=2)
        before = resolve_sun_curve_value(SUNRISE, SUNSET, min_=40, max_=255, now=solar_noon - offset)
        after = resolve_sun_curve_value(SUNRISE, SUNSET, min_=40, max_=255, now=solar_noon + offset)
        assert before == after

    def test_curve_rises_monotonically_toward_noon(self):
        values = [
            resolve_sun_curve_value(SUNRISE, SUNSET, min_=40, max_=255, now=_at(h, 0))
            for h in (7, 9, 11, 13)
        ]
        assert values == sorted(values)

    def test_value_is_int(self):
        assert isinstance(resolve_sun_curve_value(SUNRISE, SUNSET, min_=40, max_=255, now=_at(10, 0)), int)

    def test_zero_length_daylight_is_min(self):
        assert resolve_sun_curve_value(SUNRISE, SUNRISE, min_=40, max_=255, now=SUNRISE) == 40
