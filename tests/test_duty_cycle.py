"""tests/test_duty_cycle.py

display.duty_cycle: schedule math (shorthand and steps), validator, driver blank()/invert()/
set_level() hooks (fakes only) and the main-loop sequencing in display._run_tick.
"""
from __future__ import annotations

import pytest

import clockish.display as d
from clockish import duty_cycle as dc
from clockish.config_validator import validate_config_dict
from clockish.drivers import framebuffer as fbdrv
from clockish.drivers import ili9486 as ilidrv
from clockish.drivers import ssd1306 as drv
from clockish.drivers import st7789 as stdrv
from clockish.drivers.base import DisplayDriver

MINUTE = {'period': '60s', 'show_at': '55s', 'show_for': '10s'}
STEPS = {'steps': [{'for': '5s', 'level': 'full'}, {'for': '25s', 'level': 'half'},
                   {'for': '30s', 'level': 'dim'}]}
FULL = dc.Level(255, 'high', 'normal')
HALF = dc.Level(32, 'low', 'normal')
DIM = dc.Level(1, 'low', 'normal')
T0 = 1_790_000_040   # divisible by 60: second 0 of a minute


class TestParseDuration:
    @pytest.mark.parametrize('value, secs', [
        (60, 60), ('60', 60), ('60s', 60), ('5m', 300), ('1h', 3600), ('0.5m', 30), (' 10s ', 10), (0, 0),
    ])
    def test_valid(self, value, secs):
        assert dc.parse_duration(value) == secs

    @pytest.mark.parametrize('value', ['', 's', '1d', '-5s', '0.5s', True, None, 1.5, [], '1m30s'])
    def test_invalid(self, value):
        assert dc.parse_duration(value) is None


class TestSharedParser:
    """display._parse_interval and duty_cycle.parse_duration both go through clockish.durations."""

    @pytest.mark.parametrize('value, secs', [
        ('30s', 30), ('1.5m', 90), ('2h', 7200), ('10 s', 10), (45, 45), ('1.9s', 1),
    ])
    def test_parse_interval(self, value, secs):
        assert d._parse_interval(value) == secs

    @pytest.mark.parametrize('value', ['', 'soon', '1d', None])
    def test_parse_interval_rejects(self, value):
        with pytest.raises(ValueError):
            d._parse_interval(value)


class TestIsVisible:
    def test_window_wraps_the_minute(self):
        vis = [s for s in range(60) if dc.is_visible(MINUTE, T0 + s)]
        assert vis == [0, 1, 2, 3, 4, 55, 56, 57, 58, 59]

    def test_off_from_second_5(self):
        assert not dc.is_visible(MINUTE, T0 + 5)
        assert not dc.is_visible(MINUTE, T0 + 54)

    def test_five_minute_period(self):
        cfg = {'period': '5m', 'show_at': '4m', 'show_for': '30s'}
        base = T0 - T0 % 300
        assert dc.is_visible(cfg, base + 240)
        assert dc.is_visible(cfg, base + 269)
        assert not dc.is_visible(cfg, base + 270)
        assert not dc.is_visible(cfg, base + 239)

    def test_hour_period_wraps(self):
        cfg = {'period': '1h', 'show_at': '59m', 'show_for': '2m'}
        base = T0 - T0 % 3600
        assert dc.is_visible(cfg, base + 3540)
        assert dc.is_visible(cfg, base + 3600 + 59)
        assert not dc.is_visible(cfg, base + 3600 + 60)

    def test_absent_is_always_visible(self):
        assert dc.is_visible(None, T0 + 30)
        assert dc.parse(None) is None

    @pytest.mark.parametrize('show_for', ['60s', '2m', 60, 61])
    def test_show_for_at_or_over_period_is_always_visible(self, show_for):
        cfg = {'period': '60s', 'show_at': '55s', 'show_for': show_for}
        assert all(dc.is_visible(cfg, T0 + s) for s in range(60))

    def test_accepts_parsed_object(self):
        parsed = dc.parse(MINUTE)
        assert (parsed.period, parsed.offset) == (60, 55)
        assert parsed.steps == (dc.Step(0, 10, 'normal', 'full'), dc.Step(10, 50, 'blank', 'full'))
        assert dc.is_visible(parsed, T0 + 55)

    def test_invalid_is_always_visible(self):
        assert dc.is_visible({'period': '7s', 'show_at': 0, 'show_for': 1}, T0 + 3)


class TestProblems:
    def test_valid_is_clean(self):
        assert dc.problems(MINUTE) == []

    @pytest.mark.parametrize('cfg, fragment', [
        ('60s', 'mapping'),
        ({**MINUTE, 'on': 5}, "unknown key 'on'"),
        ({'show_at': 1, 'show_for': 1}, "missing required 'period'"),
        ({**MINUTE, 'period': '7s'}, 'divide 86400'),
        ({**MINUTE, 'period': 0}, 'greater than 0'),
        ({**MINUTE, 'show_at': '60s'}, 'less than period'),
        ({**MINUTE, 'show_at': -1}, 'at least 0'),
        ({**MINUTE, 'show_for': 0}, 'never show'),
        ({**MINUTE, 'show_for': 'ten'}, 'whole seconds'),
        ({**MINUTE, 'show_for': True}, 'whole seconds'),
    ])
    def test_errors(self, cfg, fragment):
        assert any(fragment in m for m in dc.problems(cfg)), dc.problems(cfg)


def _validate(display):
    cfg = {'rows': [{'name': 'r', 'height': 10, 'panels': [{'type': 'text', 'label': 'x'}]}],
           'display': display}
    return validate_config_dict(cfg, path='t.yaml', run_yamllint=False)


class TestValidator:
    def test_valid_block(self):
        assert not [i for i in _validate({'duty_cycle': MINUTE}).issues if 'duty_cycle' in i.path]

    def test_bad_block_is_an_error(self):
        res = _validate({'duty_cycle': {**MINUTE, 'show_for': 0}})
        assert res.has_errors
        assert any('never show' in i.message for i in res.errors)

    def test_unknown_key_is_an_error(self):
        res = _validate({'duty_cycle': {**MINUTE, 'off': 3}})
        assert any("unknown key" in i.message for i in res.errors)

    def test_schema_matches_key_list(self):
        import pathlib

        import yaml
        schema = yaml.safe_load((pathlib.Path(__file__).parent.parent / 'configs' / 'schema'
                                 / 'clockish-config.schema.yaml').read_text())
        props = schema['properties']['display']['properties']['duty_cycle']
        assert tuple(props['properties']) == dc.DUTY_CYCLE_KEYS
        assert props['anyOf'] == [{'required': list(dc.SHORTHAND_KEYS)}, {'required': ['steps']}]
        step = props['properties']['steps']['items']
        assert tuple(step['properties']) == dc.STEP_KEYS
        assert step['properties']['mode']['enum'] == list(dc.MODES)
        level = props['properties']['levels']['additionalProperties']['properties']
        assert tuple(level) == dc.LEVEL_KEYS
        assert level['vcomh']['enum'] == list(dc.VCOMH_VALUES)
        assert level['precharge']['enum'] == list(dc.PRECHARGE_VALUES)

    def test_steps_block_is_clean(self):
        assert not [i for i in _validate({'duty_cycle': {**STEPS, 'levels': {'dim': {'contrast': 3}}}}).issues
                    if 'duty_cycle' in i.path]

    def test_bad_level_is_an_error(self):
        res = _validate({'duty_cycle': {**STEPS, 'levels': {'dim': {'contrast': 300}}}})
        assert any('0-255' in i.message for i in res.errors)


# --- driver blank() ---------------------------------------------------------

class _Bus:
    instances: list = []

    def __init__(self, bus, addr):
        self.writes = []
        _Bus.instances.append(self)

    def write(self, data):
        self.writes.append(bytes(data))

    def close(self):
        pass


class TestSSD1306Blank:
    @pytest.fixture
    def driver(self, monkeypatch):
        _Bus.instances = []
        monkeypatch.setattr(drv, '_I2CDevice', _Bus)
        drv_ = drv.SSD1306Driver({'width': 128, 'height': 64}).begin()
        _Bus.instances[0].writes.clear()
        return drv_, _Bus.instances[0]

    def test_blank_sends_display_off(self, driver):
        d_, bus = driver
        d_.blank(True)
        assert bus.writes == [b'\x80\xAE']

    def test_unblank_sends_display_on(self, driver):
        d_, bus = driver
        d_.blank(False)
        assert bus.writes == [b'\x80\xAF']

    def test_blank_does_not_touch_ram(self, driver):
        d_, bus = driver
        d_.blank(True)
        assert not any(w[0] == 0x40 for w in bus.writes)

    def test_noop_when_closed(self, driver):
        d_, bus = driver
        d_.close()
        d_.blank(True)
        assert bus.writes == []


class _BaseDriver(DisplayDriver):
    def __init__(self):
        self.events = []

    def begin(self):
        return self

    def display(self, image):
        self.events.append(('display', image))

    def prepare(self, image):
        self.events.append(('prepare', image.size, image.getextrema()))
        return 'F'

    def push(self, frame):
        self.events.append(('push', frame))

    @property
    def dimensions(self):
        return (8, 4)


class TestBaseBlank:
    def test_blank_pushes_a_black_frame(self):
        b = _BaseDriver()
        b.blank(True)
        assert b.events == [('prepare', (8, 4), ((0, 0), (0, 0), (0, 0))), ('push', 'F')]

    def test_unblank_is_a_noop(self):
        b = _BaseDriver()
        b.blank(False)
        assert b.events == []

    def test_blank_is_not_idle(self):
        b = _BaseDriver()
        b.idle(True)
        assert b.events == []


class TestFramebufferBlank:
    def test_blank_uses_fbioblank_and_idle_is_unchanged(self, monkeypatch):
        calls = []
        monkeypatch.setattr(fbdrv.fcntl, 'ioctl', lambda fd, req, arg: calls.append((req, arg)))
        fb = fbdrv.FramebufferDriver({})
        fb._fb = object()
        fb.blank(True)
        fb.blank(False)
        fb.idle(True)
        assert calls == [(0x4611, 1), (0x4611, 0), (0x4611, 1)]

    def test_close_unblanks_a_blanked_console(self, monkeypatch):
        calls = []
        monkeypatch.setattr(fbdrv.fcntl, 'ioctl', lambda fd, req, arg: calls.append((req, arg)))
        fb = fbdrv.FramebufferDriver({})

        class _F:
            def close(self):
                pass
        fb._fb = _F()
        monkeypatch.setattr(fb, '_leave_graphics_mode', lambda: None)
        fb.blank(True)
        fb.close()
        assert calls == [(0x4611, 1), (0x4611, 0)]


class TestSpiPanels:
    """ILI9486/ST7789 keep the base black frame: DISPOFF on a TN panel with a
    hardwired backlight shows white, not dark."""

    @pytest.mark.parametrize('cls', [ilidrv.ILI9486Driver, stdrv.ST7789Driver])
    def test_no_override(self, cls):
        assert cls.blank is DisplayDriver.blank


# --- main loop -----------------------------------------------------------------

class _Lcd:
    HARDWARE_INVERT = True

    def __init__(self, events):
        self.events = events
        self.prepared: list = []

    def prepare(self, image):
        self.events.append('prepare')
        self.prepared.append(image.getextrema())
        return 'F'

    def invert(self, state=True):
        self.events.append(f'invert({state})')

    def set_level(self, contrast, vcomh, precharge):
        self.events.append(f'level({contrast},{vcomh},{precharge})')

    def push(self, frame):
        self.events.append('push')

    def blank(self, state=True):
        self.events.append(f'blank({state})')


@pytest.fixture
def loop(monkeypatch):
    events: list = []
    now = {'t': float(T0 + 50)}

    def fake_sleep(s):
        now['t'] += s

    monkeypatch.setattr(d, 'lcd', _Lcd(events))
    monkeypatch.setattr(d, '_LAYOUT', [])
    monkeypatch.setattr(d, 'DEBUG', False)
    monkeypatch.setattr(d, '_avg_display_ms', None)
    monkeypatch.setattr(d, '_panel_blanked', False)
    monkeypatch.setattr(d, '_panel_level', FULL)      # the driver's init level
    monkeypatch.setattr(d, '_panel_inverted', False)
    monkeypatch.setattr(d, '_duty_step', -1)
    monkeypatch.setattr(d, '_duty', dc.parse(MINUTE))
    monkeypatch.setattr(d, 'get_ntp_status', lambda: '')
    monkeypatch.setattr(d, 'get_ntp_upstream_count', lambda: '')
    monkeypatch.setattr(d.time, 'time', lambda: now['t'])
    monkeypatch.setattr(d.time, 'sleep', fake_sleep)
    return events


class TestRunTick:
    def test_visible_ticks_render_and_push_only(self, loop):
        d._run_tick(T0 + 56)
        assert loop == ['prepare', 'push']

    def test_hidden_tick_blanks_once_without_rendering(self, loop):
        d._run_tick(T0 + 5)
        d._run_tick(T0 + 6)
        d._run_tick(T0 + 7)
        assert loop == ['blank(True)']
        assert d._panel_blanked

    def test_hidden_tick_sleeps_to_the_boundary(self, loop, monkeypatch):
        monkeypatch.setattr(d.time, 'time', lambda: T0 + 4.2)
        slept: list = []
        monkeypatch.setattr(d.time, 'sleep', slept.append)
        d._run_tick(T0 + 5)
        assert slept == [pytest.approx(0.8)]

    def test_wake_pushes_the_frame_then_unblanks(self, loop):
        d._run_tick(T0 + 5)
        loop.clear()
        d._run_tick(T0 + 55)
        assert loop == ['prepare', 'push', 'blank(False)']
        assert not d._panel_blanked

    def test_full_minute_sequence(self, loop):
        for s in range(60):
            d._run_tick(T0 + s)
        assert [e for e in loop if e.startswith('blank')] == ['blank(True)', 'blank(False)']
        assert loop.count('push') == 10
        assert loop.index('blank(True)') > loop.index('push')
        i = loop.index('blank(False)')
        assert loop[i - 2:i] == ['prepare', 'push']

    def test_no_duty_cycle_never_blanks(self, loop, monkeypatch):
        monkeypatch.setattr(d, '_duty', None)
        for s in range(0, 60, 7):
            d._run_tick(T0 + s)
        assert 'blank(True)' not in loop

    def test_load_duty_cycle_invalid_warns_and_is_absent(self, capsys):
        assert d._load_duty_cycle({'duty_cycle': {**MINUTE, 'show_for': 0}}) is None
        assert 'duty_cycle ignored' in capsys.readouterr().err
        assert d._load_duty_cycle({}) is None
        assert d._load_duty_cycle({'duty_cycle': MINUTE}) == dc.parse(MINUTE)


# --- steps schedule -------------------------------------------------------------

class TestSteps:
    def test_minute_of_levels(self):
        levels = [dc.state_at(STEPS, T0 + s).level for s in range(60)]
        assert levels == [FULL] * 5 + [HALF] * 25 + [DIM] * 30
        assert dc.state_at(STEPS, T0 + 60).level == FULL

    def test_index_and_mode(self):
        st = dc.state_at(STEPS, T0 + 31)
        assert (st.index, st.mode) == (2, 'normal')

    def test_offset_shifts_step_one(self):
        cfg = {**STEPS, 'offset': '55s'}
        assert dc.state_at(cfg, T0 + 55).level == FULL
        assert dc.state_at(cfg, T0 + 59).level == FULL
        assert dc.state_at(cfg, T0 + 0).level == HALF

    def test_invert_and_blank_steps(self):
        cfg = {'steps': [{'for': 10}, {'for': 10, 'mode': 'blank'}, {'for': 10, 'mode': 'invert'},
                         {'for': 10, 'mode': 'blank'}]}
        modes = [dc.state_at(cfg, T0 + s).mode for s in (0, 9, 10, 20, 29, 30, 39, 40)]
        assert modes == ['normal', 'normal', 'blank', 'invert', 'invert', 'blank', 'blank', 'normal']
        assert not dc.parse(cfg).always_visible
        assert dc.parse(STEPS).always_visible

    def test_levels_override_and_add(self):
        cfg = {**STEPS, 'levels': {'dim': {'contrast': 0}, 'glow': {'vcomh': 'mid'}}}
        cfg['steps'] = cfg['steps'] + [{'for': '60s', 'level': 'glow'}]
        parsed = dc.parse(cfg)
        assert parsed.levels['dim'] == dc.Level(0, 'low', 'normal')
        assert parsed.levels['glow'] == dc.Level(255, 'mid', 'normal')    # unset keys from 'full'
        assert parsed.levels['full'] == FULL

    def test_defaults(self):
        st = dc.state_at({'steps': [{'for': '1m'}]}, T0 + 7)
        assert (st.mode, st.level) == ('normal', FULL)

    def test_absent_leaves_levels_alone(self):
        assert dc.state_at(None, T0) == dc.ALWAYS_ON
        assert dc.ALWAYS_ON.level is None

    def test_shorthand_full_window_is_full_level(self):
        assert dc.state_at(MINUTE, T0 + 56).level == FULL


class TestStepsProblems:
    def test_clean(self):
        assert dc.problems(STEPS) == []
        assert dc.problems({**STEPS, 'offset': 59, 'levels': {'dim': {'precharge': 'short'}}}) == []

    @pytest.mark.parametrize('cfg, fragment', [
        ({**STEPS, **MINUTE}, 'not both'),
        ({**STEPS, 'period': 60}, 'not both'),
        ({'steps': []}, 'non-empty list'),
        ({'steps': {'for': 5}}, 'non-empty list'),
        ({'steps': ['5s']}, 'must be a mapping'),
        ({'steps': [{'level': 'dim'}]}, "missing required 'for'"),
        ({'steps': [{'for': 0}]}, 'whole seconds > 0'),
        ({'steps': [{'for': '1.5s'}]}, 'whole seconds > 0'),
        ({'steps': [{'for': 60, 'mode': 'off'}]}, 'mode must be one of'),
        ({'steps': [{'for': 60, 'mode': False}]}, 'mode must be one of'),
        ({'steps': [{'for': 60, 'level': 'bright'}]}, "unknown level 'bright'"),
        ({'steps': [{'for': 60, 'level': ['dim']}]}, 'unknown level'),
        ({'steps': [{'for': 60, 'on': 1}]}, "unknown key 'on'"),
        ({'steps': [{'for': 7}]}, 'add up to 7s'),
        ({**STEPS, 'offset': '60s'}, 'less than the period (60s)'),
        ({**STEPS, 'offset': 'soon'}, 'whole seconds'),
        ({**MINUTE, 'offset': 5}, 'show_at is the offset'),
        ({**STEPS, 'levels': ['dim']}, 'levels must be a mapping'),
        ({**STEPS, 'levels': {'dim': 1}}, 'levels.dim must be a mapping'),
        ({**STEPS, 'levels': {'dim': {'contrast': 256}}}, 'integer 0-255'),
        ({**STEPS, 'levels': {'dim': {'contrast': True}}}, 'integer 0-255'),
        ({**STEPS, 'levels': {'dim': {'contrast': '1'}}}, 'integer 0-255'),
        ({**STEPS, 'levels': {'dim': {'vcomh': 'lowest'}}}, 'vcomh must be one of'),
        ({**STEPS, 'levels': {'dim': {'precharge': 'long'}}}, 'precharge must be one of'),
        ({**STEPS, 'levels': {'dim': {'brightness': 1}}}, "unknown key 'brightness'"),
        ({}, "missing required 'period'"),
    ])
    def test_errors(self, cfg, fragment):
        assert any(fragment in m for m in dc.problems(cfg)), dc.problems(cfg)

    def test_custom_level_name_is_known(self):
        cfg = {'steps': [{'for': 60, 'level': 'glow'}], 'levels': {'glow': {'contrast': 9}}}
        assert dc.problems(cfg) == []

    def test_invalid_is_always_on(self):
        assert dc.state_at({'steps': [{'for': 7}]}, T0) == dc.ALWAYS_ON


class TestShippedOledProfiles:
    """Both SSD1306 profiles ship the 5s full / 25s half / 30s dim minute."""

    @pytest.mark.parametrize('name', ['ssd1306-128x32.yaml', 'ssd1306-128x64.yaml'])
    def test_profile_schedule(self, name):
        import pathlib

        import yaml
        path = pathlib.Path(__file__).parent.parent / 'configs' / 'display' / name
        cfg = yaml.safe_load(path.read_text())['display']['duty_cycle']
        assert dc.problems(cfg) == []
        assert [dc.state_at(cfg, T0 + s).level for s in (0, 4, 5, 29, 30, 59)] == [FULL, FULL, HALF, HALF, DIM, DIM]


# --- driver invert() / set_level() ------------------------------------------------

class TestSSD1306Levels:
    @pytest.fixture
    def driver(self, monkeypatch):
        _Bus.instances = []
        monkeypatch.setattr(drv, '_I2CDevice', _Bus)
        drv_ = drv.SSD1306Driver({'width': 128, 'height': 64}).begin()
        _Bus.instances[0].writes.clear()
        return drv_, _Bus.instances[0]

    def test_hardware_invert(self, driver):
        d_, bus = driver
        assert d_.HARDWARE_INVERT
        d_.invert(True)
        d_.invert(False)
        assert bus.writes == [b'\x80\xA7', b'\x80\xA6']

    @pytest.mark.parametrize('level, payload', [
        (DIM, [0x81, 0x01, 0xDB, 0x00, 0xD9, 0xF1]),
        (HALF, [0x81, 0x20, 0xDB, 0x00, 0xD9, 0xF1]),
        (dc.Level(0, 'mid', 'short'), [0x81, 0x00, 0xDB, 0x20, 0xD9, 0x11]),
    ])
    def test_set_level_one_transfer(self, driver, level, payload):
        d_, bus = driver
        d_.set_level(level.contrast, level.vcomh, level.precharge)
        assert bus.writes == [b''.join(bytes((0x80, c)) for c in payload)]

    def test_full_equals_init(self):
        cmds = drv.init_commands(128, 64)
        assert cmds[cmds.index(drv.SET_CONTRAST) + 1] == FULL.contrast
        assert cmds[cmds.index(drv.SET_VCOM_DESEL) + 1] == drv.VCOMH_LEVELS[FULL.vcomh]
        assert cmds[cmds.index(drv.SET_PRECHARGE) + 1] == drv.PRECHARGE_LEVELS[FULL.precharge]

    def test_clear_uninverts_before_black(self, driver):
        d_, bus = driver
        d_.invert(True)
        bus.writes.clear()
        d_.clear()
        assert bus.writes[0] == b'\x80\xA6'
        assert bus.writes[-1][0] == 0x40 and not any(bus.writes[-1][1:])

    def test_noop_when_closed(self, driver):
        d_, bus = driver
        d_.close()
        d_.invert(True)
        d_.set_level(1, 'low', 'normal')
        assert bus.writes == []


class TestBaseLevels:
    def test_noops(self):
        b = _BaseDriver()
        b.invert(True)
        b.set_level(1, 'low', 'normal')
        assert b.events == []

    @pytest.mark.parametrize('cls', [fbdrv.FramebufferDriver, ilidrv.ILI9486Driver, stdrv.ST7789Driver])
    def test_software_invert_drivers(self, cls):
        assert not cls.HARDWARE_INVERT
        assert cls.set_level is DisplayDriver.set_level


# --- main loop with steps ---------------------------------------------------------

class TestRunTickSteps:
    def test_levels_change_once_per_step(self, loop, monkeypatch):
        monkeypatch.setattr(d, '_duty', dc.parse(STEPS))
        for s in range(60):
            d._run_tick(T0 + s)
        assert [e for e in loop if e.startswith('level')] == ['level(32,low,normal)', 'level(1,low,normal)']
        assert loop.count('push') == 60
        i = loop.index('level(32,low,normal)')
        assert loop[i - 2:i] == ['prepare', 'push']       # new frame first, then the level

    def test_first_tick_sets_level_from_unknown(self, loop, monkeypatch):
        monkeypatch.setattr(d, '_duty', dc.parse(STEPS))
        monkeypatch.setattr(d, '_panel_level', None)       # fresh driver after a reload
        d._run_tick(T0 + 40)
        assert loop == ['prepare', 'push', 'level(1,low,normal)']

    def test_hardware_invert_step(self, loop, monkeypatch):
        cfg = {'steps': [{'for': 10, 'mode': 'invert'}, {'for': 10, 'mode': 'blank'}, {'for': 10}]}
        monkeypatch.setattr(d, '_duty', dc.parse(cfg))
        for s in range(30):
            d._run_tick(T0 + s)
        flips = [e for e in loop if e.startswith(('invert', 'blank'))]
        assert flips == ['invert(True)', 'blank(True)', 'invert(False)', 'blank(False)']
        assert d.lcd.prepared[0] == ((0, 0), (0, 0), (0, 0))   # the frame itself is not inverted

    def test_software_invert_step(self, loop, monkeypatch):
        monkeypatch.setattr(d.lcd, 'HARDWARE_INVERT', False)
        monkeypatch.setattr(d, '_duty', dc.parse({'steps': [{'for': 30, 'mode': 'invert'}, {'for': 30}]}))
        d._run_tick(T0 + 1)
        d._run_tick(T0 + 31)
        assert not any(e.startswith('invert') for e in loop)
        assert d.lcd.prepared == [((255, 255), (255, 255), (255, 255)), ((0, 0), (0, 0), (0, 0))]

    def test_wake_into_dim_pushes_then_levels_then_unblanks(self, loop, monkeypatch):
        cfg = {'steps': [{'for': 30, 'mode': 'blank'}, {'for': 30, 'level': 'dim'}]}
        monkeypatch.setattr(d, '_duty', dc.parse(cfg))
        d._run_tick(T0 + 29)
        loop.clear()
        d._run_tick(T0 + 30)
        assert loop == ['prepare', 'push', 'level(1,low,normal)', 'blank(False)']

    def test_no_duty_cycle_never_touches_levels(self, loop, monkeypatch):
        monkeypatch.setattr(d, '_duty', None)
        monkeypatch.setattr(d, '_panel_level', None)
        for s in range(0, 60, 7):
            d._run_tick(T0 + s)
        assert set(loop) == {'prepare', 'push'}

    def test_debug_prints_each_step_once(self, loop, monkeypatch, capsys):
        monkeypatch.setattr(d, '_duty', dc.parse(STEPS))
        monkeypatch.setattr(d, 'DEBUG', True)
        monkeypatch.setattr(d, 'show_rows', lambda **kw: None)
        for s in range(60):
            d._run_tick(T0 + s)
        lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith('duty_cycle: step')]
        assert lines == [f'duty_cycle: step 1 mode=normal contrast=255 vcomh=high precharge=normal at tick={T0}',
                         f'duty_cycle: step 2 mode=normal contrast=32 vcomh=low precharge=normal at tick={T0 + 5}',
                         f'duty_cycle: step 3 mode=normal contrast=1 vcomh=low precharge=normal at tick={T0 + 30}']
