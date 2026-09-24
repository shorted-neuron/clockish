"""tests/test_backlight.py

Tests for the pure brightness-resolution functions in clockish/backlight.py:
  - resolve_scheduled_value() -- fixed day/night `schedule:` list
  - resolve_sun_curve_value() -- the sun-following `schedule: sun` curve
  - _apply(cfg, now=...) -- the `now` injection point the simulated-day
    runner (scripts/backlight_hardware_test.py) drives a whole day through
  - current_percent() -- the `backlight` fact source's value
"""
import datetime

import pytest

from clockish import backlight
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
    """The `schedule: sun` trapezoid: min at night, eased ramp starting
    TWILIGHT_MINUTES before sunrise, flat max across the middle half of
    daylight, mirrored ramp ending TWILIGHT_MINUTES after sunset.

    With SUNRISE 07:00 / SUNSET 19:00 and the defaults (50m, 0.25) that is:
    ramp 06:10 -> 10:00, max 10:00-16:00, ramp 16:00 -> 19:50.
    """

    RAMP_START = _at(6, 10)
    PEAK_START = _at(10, 0)
    PEAK_END = _at(16, 0)
    RAMP_END = _at(19, 50)

    def _v(self, now, **kw):
        return resolve_sun_curve_value(SUNRISE, SUNSET, min_=40, max_=255, now=now, **kw)

    def test_deep_night_is_min(self):
        assert self._v(_at(3, 0)) == 40
        assert self._v(_at(22, 0)) == 40

    def test_min_up_to_the_start_of_the_ramp(self):
        assert self._v(self.RAMP_START - datetime.timedelta(minutes=1)) == 40
        assert self._v(self.RAMP_START) == 40  # ramp leaves min with zero slope

    def test_already_climbing_before_sunrise(self):
        """The whole point of the twilight offset: it is getting light before
        the sun clears the horizon, so the panel is already on its way up."""
        assert 40 < self._v(_at(6, 40)) < 255

    def test_sunrise_is_partway_up_the_ramp(self):
        assert 40 < self._v(SUNRISE) < 255

    def test_plateau_sits_at_max(self):
        for t in (self.PEAK_START, _at(13, 0), self.PEAK_END - datetime.timedelta(minutes=1)):
            assert self._v(t) == 255

    def test_below_max_just_before_the_plateau(self):
        assert self._v(self.PEAK_START - datetime.timedelta(minutes=10)) < 255

    def test_plateau_is_half_of_daylight(self):
        """peak_fraction 0.25 either side -> max for the middle 50%."""
        daylight = SUNSET - SUNRISE
        assert self.PEAK_START == SUNRISE + daylight * 0.25
        assert self.PEAK_END == SUNSET - daylight * 0.25
        # peak_end is still max (the ramp leaves it with zero slope); the
        # descent shows up just after.
        assert self._v(self.PEAK_END) == 255
        assert self._v(self.PEAK_END + datetime.timedelta(minutes=10)) < 255

    def test_sunset_is_partway_down_the_ramp(self):
        assert 40 < self._v(SUNSET) < 255

    def test_min_again_from_the_end_of_the_ramp(self):
        assert self._v(self.RAMP_END) == 40
        assert self._v(self.RAMP_END + datetime.timedelta(minutes=10)) == 40

    def test_curve_is_symmetric_around_solar_noon(self):
        solar_noon = SUNRISE + (SUNSET - SUNRISE) / 2
        for offset in (datetime.timedelta(hours=2), datetime.timedelta(hours=4),
                       datetime.timedelta(hours=6)):
            assert self._v(solar_noon - offset) == self._v(solar_noon + offset)

    def test_ramps_are_monotonic(self):
        rising = [self._v(_at(6, 10) + datetime.timedelta(minutes=10 * i)) for i in range(24)]
        assert rising == sorted(rising)
        falling = [self._v(_at(16, 0) + datetime.timedelta(minutes=10 * i)) for i in range(24)]
        assert falling == sorted(falling, reverse=True)

    def test_twilight_minutes_is_overridable(self):
        # No twilight offset: the ramp starts exactly at sunrise.
        assert resolve_sun_curve_value(SUNRISE, SUNSET, min_=40, max_=255,
                                       now=SUNRISE, twilight_minutes=0) == 40
        assert resolve_sun_curve_value(SUNRISE, SUNSET, min_=40, max_=255,
                                       now=_at(6, 40), twilight_minutes=0) == 40

    def test_peak_fraction_is_overridable(self):
        # 10% shoulders -> max reached by sunrise + 1h12m instead of + 3h.
        assert resolve_sun_curve_value(SUNRISE, SUNSET, min_=40, max_=255,
                                       now=_at(8, 30), peak_fraction=0.1) == 255
        assert resolve_sun_curve_value(SUNRISE, SUNSET, min_=40, max_=255,
                                       now=_at(8, 30), peak_fraction=0.25) < 255

    def test_peak_fraction_clamped_below_half(self):
        """>= 0.5 would make the two ramps cross; it degenerates to a single
        point at solar noon instead of producing nonsense."""
        solar_noon = SUNRISE + (SUNSET - SUNRISE) / 2
        assert resolve_sun_curve_value(SUNRISE, SUNSET, min_=40, max_=255,
                                       now=solar_noon, peak_fraction=0.9) == 255

    def test_defaults_match_the_module_constants(self):
        assert (backlight.TWILIGHT_MINUTES, backlight.PEAK_FRACTION) == (50, 0.25)

    def test_value_is_int(self):
        assert isinstance(self._v(_at(8, 0)), int)

    def test_zero_length_daylight_is_min(self):
        assert resolve_sun_curve_value(SUNRISE, SUNRISE, min_=40, max_=255, now=SUNRISE) == 40


@pytest.fixture
def recorder(monkeypatch):
    """Swap the sysfs writer for a recorder and reset _apply's dedup state."""
    written: list[int] = []

    def fake_write(device: str, value: int) -> bool:
        written.append(value)
        return True

    monkeypatch.setitem(backlight._METHODS, 'sysfs', fake_write)
    monkeypatch.setattr(backlight, '_last_written_value', None)
    monkeypatch.setattr(backlight, '_get_sun_times', None)
    return written


class TestApplyNowInjection:
    """`_apply(cfg, now=...)` -- same code path the worker thread takes, but
    resolved for an arbitrary moment (the simulated-day runner's one seam)."""

    SCHEDULE_CFG = {
        'method': 'sysfs', 'device': 'test-dev',
        'min': 40, 'max': 255, 'schedule': SCHEDULE,
    }
    CURVE_CFG = {
        'method': 'sysfs', 'device': 'test-dev',
        'min': 40, 'max': 255, 'schedule': 'sun',
    }

    def test_schedule_writes_value_due_at_the_given_moment(self, recorder):
        backlight._apply(self.SCHEDULE_CFG, now=_at(12, 0))
        assert recorder == [255]

    def test_schedule_replaying_a_day_follows_the_schedule(self, recorder):
        # 00:00 night, 08:00 + 16:00 day (the second deduped), 23:00 night
        for hour in (0, 8, 16, 23):
            backlight._apply(self.SCHEDULE_CFG, now=_at(hour, 0))
        assert recorder == [42, 255, 42]

    def test_repeated_same_value_is_deduped(self, recorder):
        backlight._apply(self.SCHEDULE_CFG, now=_at(12, 0))
        backlight._apply(self.SCHEDULE_CFG, now=_at(13, 0))
        assert recorder == [255]

    def test_curve_uses_injected_now_not_real_now(self, recorder, monkeypatch):
        monkeypatch.setattr(backlight, '_get_sun_times', lambda: (SUNRISE, SUNSET))
        solar_noon = SUNRISE + (SUNSET - SUNRISE) / 2
        backlight._apply(self.CURVE_CFG, now=solar_noon)
        backlight._apply(self.CURVE_CFG, now=_at(2, 0))  # middle of the night
        assert recorder == [255, 40]

    def test_default_now_is_real_time(self, recorder, monkeypatch):
        """Omitting `now` must behave exactly as before -- the worker relies on it."""
        monkeypatch.setattr(backlight, '_get_sun_times', lambda: (SUNRISE, SUNSET))
        backlight._apply(self.CURVE_CFG)
        expected = resolve_sun_curve_value(SUNRISE, SUNSET, min_=40, max_=255)
        assert recorder == [expected]

    def test_resolve_value_passes_now_through_for_schedule(self):
        assert backlight._resolve_value(self.SCHEDULE_CFG, now=_at(23, 0)) == 42

    def test_resolve_value_passes_now_through_for_curve(self, monkeypatch):
        monkeypatch.setattr(backlight, '_get_sun_times', lambda: (SUNRISE, SUNSET))
        solar_noon = SUNRISE + (SUNSET - SUNRISE) / 2
        assert backlight._resolve_value(self.CURVE_CFG, now=solar_noon) == 255


class TestCurrentPercent:
    """`fact: backlight` -- where the panel's percentage comes from."""

    CFG = {'method': 'sysfs', 'device': 'test-dev', 'min': 2, 'max': 255}

    @pytest.fixture
    def active(self, monkeypatch):
        """Pretend start_backlight() ran with CFG, with a readable device."""
        level = {'value': 129}
        monkeypatch.setattr(backlight, '_active_cfg', self.CFG)
        monkeypatch.setattr(backlight, '_last_written_value', None)
        monkeypatch.setitem(backlight._READERS, 'sysfs', lambda device: level['value'])
        return level

    def test_midpoint_of_the_configured_span(self, active):
        # 129 sits halfway between min 2 and max 255, not halfway up 0..255
        assert backlight.current_percent() == '50%'

    def test_min_is_zero_percent(self, active):
        active['value'] = 2
        assert backlight.current_percent() == '0%'

    def test_max_is_hundred_percent(self, active):
        active['value'] = 255
        assert backlight.current_percent() == '100%'

    def test_no_decimal_places(self, active):
        active['value'] = 100
        assert backlight.current_percent() == '39%'  # 98/253 = 38.7%

    def test_below_min_clamps_to_zero(self, active):
        active['value'] = 0  # e.g. off_value, or set externally
        assert backlight.current_percent() == '0%'

    def test_above_max_clamps_to_hundred(self, active):
        active['value'] = 300
        assert backlight.current_percent() == '100%'

    def test_unreadable_device_falls_back_to_last_written(self, monkeypatch):
        monkeypatch.setattr(backlight, '_active_cfg', self.CFG)
        monkeypatch.setattr(backlight, '_last_written_value', 255)
        monkeypatch.setitem(backlight._READERS, 'sysfs', lambda device: None)
        assert backlight.current_percent() == '100%'

    def test_unknown_level_is_none(self, monkeypatch):
        monkeypatch.setattr(backlight, '_active_cfg', self.CFG)
        monkeypatch.setattr(backlight, '_last_written_value', None)
        monkeypatch.setitem(backlight._READERS, 'sysfs', lambda device: None)
        assert backlight.current_percent() is None

    def test_no_backlight_configured_is_none(self, monkeypatch):
        monkeypatch.setattr(backlight, '_active_cfg', None)
        assert backlight.current_percent() is None
        assert backlight.current_value() is None

    def test_zero_width_span_is_none(self, monkeypatch):
        monkeypatch.setattr(backlight, '_active_cfg', {'method': 'sysfs', 'min': 40, 'max': 40})
        monkeypatch.setitem(backlight._READERS, 'sysfs', lambda device: 40)
        assert backlight.current_percent() is None

    def test_start_and_stop_track_the_active_config(self, monkeypatch):
        written = []
        monkeypatch.setitem(backlight._METHODS, 'sysfs',
                            lambda device, value: written.append(value) or True)
        monkeypatch.setattr(backlight, '_last_written_value', None)
        cfg = dict(self.CFG, schedule=[{'name': 'all', 'start': '00:00',
                                        'end': '23:59', 'value': 129}])
        backlight.start_backlight({'backlight': cfg})
        try:
            assert backlight._active_cfg is cfg
        finally:
            backlight.stop_backlight()
        assert backlight._active_cfg is None
        assert backlight.current_percent() is None


class TestScheduleDispatch:
    """`schedule:` is either a sun scalar or a list of time-of-day entries;
    _resolve_value() tells them apart by type."""

    BASE = {'method': 'sysfs', 'device': 'test-dev', 'min': 40, 'max': 255}

    @pytest.fixture(autouse=True)
    def _sun(self, monkeypatch):
        monkeypatch.setattr(backlight, '_get_sun_times', lambda: (SUNRISE, SUNSET))

    @pytest.mark.parametrize('spelling', sorted(backlight.SUN_SCHEDULE_VALUES))
    def test_every_sun_spelling_follows_the_sun(self, spelling):
        solar_noon = SUNRISE + (SUNSET - SUNRISE) / 2
        cfg = dict(self.BASE, schedule=spelling)
        assert backlight._resolve_value(cfg, now=solar_noon) == 255
        assert backlight._resolve_value(cfg, now=_at(2, 0)) == 40

    def test_list_schedule_still_resolves_by_time_of_day(self):
        cfg = dict(self.BASE, schedule=SCHEDULE)
        assert backlight._resolve_value(cfg, now=_at(12, 0)) == 255
        assert backlight._resolve_value(cfg, now=_at(23, 30)) == 42

    def test_unknown_scalar_falls_back_to_midpoint(self):
        cfg = dict(self.BASE, schedule='moon')
        assert backlight._resolve_value(cfg, now=_at(12, 0)) == 148

    def test_missing_schedule_falls_back_to_midpoint(self):
        assert backlight._resolve_value(dict(self.BASE), now=_at(12, 0)) == 148

    def test_sun_without_sun_times_falls_back_to_midpoint(self, monkeypatch):
        monkeypatch.setattr(backlight, '_get_sun_times', lambda: None)
        cfg = dict(self.BASE, schedule='sun')
        assert backlight._resolve_value(cfg, now=_at(12, 0)) == 148
