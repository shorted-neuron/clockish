"""tests/test_gpio_backends.py

The GPIO backend chooser for the ILI9486 driver (clockish/gpio_backends.py) and its wiring into
drivers/ili9486.py.  Every facade is a fake planted in sys.modules, so none of this needs a Pi,
pyili9486, gpiod or lgpio.  The real-hardware side lives in the testbed: a Pi 1 where rpi-lgpio
fails at import is exactly the case `auto` exists for.
"""
import pathlib
import sys
import types

import pytest

from clockish import gpio_backends
from clockish.gpio_backends import AUTO_ORDER, GPIO_BACKENDS, GPIOBackendError, make_gpio_facade

MODULES = {
    'gpiod':    'pyili9486.gpio.gpiod_facade',
    'lgpio':    'pyili9486.gpio.lgpio_facade',
    'rpi-gpio': 'pyili9486.gpio.rpilgpio_facade',
}
CLASSES = {'gpiod': 'GPIODFacade', 'lgpio': 'LGPIOFacade', 'rpi-gpio': 'RPiLGPIOFacade'}


class _Recorder:
    """What the fake facades were constructed with, in order."""
    def __init__(self):
        self.calls = []


@pytest.fixture
def rec(monkeypatch):
    monkeypatch.delenv(gpio_backends.ENV_BACKEND, raising=False)
    return _Recorder()


def plant(monkeypatch, rec, backend, fail=None):
    """Make `backend` importable as a fake facade; `fail` is an exception it raises when built,
    or an ImportError subclass instance to make the *import* fail instead."""
    name = MODULES[backend]
    if isinstance(fail, ImportError):
        monkeypatch.setitem(sys.modules, name, None)        # `import` raises ImportError
        return
    takes_chip = backend != 'rpi-gpio'

    if takes_chip:
        def init(self, dc_pin, rs_pin=None, gpio_chip_id=0):
            rec.calls.append((backend, dc_pin, rs_pin, gpio_chip_id))
            if fail:
                raise fail
    else:
        def init(self, dc_pin, rs_pin=None):                  # RPi.GPIO has no chip number
            rec.calls.append((backend, dc_pin, rs_pin, None))
            if fail:
                raise fail
    mod = types.ModuleType(name)
    setattr(mod, CLASSES[backend], type(CLASSES[backend], (), {'__init__': init}))
    monkeypatch.setitem(sys.modules, name, mod)


def missing(monkeypatch, rec, *backends):
    for b in backends:
        plant(monkeypatch, rec, b, fail=ModuleNotFoundError(f"No module named {b!r}"))


class TestAuto:
    def test_prefers_gpiod(self, monkeypatch, rec):
        for b in GPIO_BACKENDS[1:]:
            plant(monkeypatch, rec, b)
        facade, name = make_gpio_facade(24, 25)
        assert name == 'gpiod' and type(facade).__name__ == 'GPIODFacade'
        assert [c[0] for c in rec.calls] == ['gpiod']          # nothing else was even built

    def test_falls_back_to_lgpio_when_gpiod_is_missing(self, monkeypatch, rec):
        missing(monkeypatch, rec, 'gpiod')
        plant(monkeypatch, rec, 'lgpio')
        plant(monkeypatch, rec, 'rpi-gpio')
        assert make_gpio_facade(24, 25)[1] == 'lgpio'

    def test_falls_back_to_rpi_gpio_last(self, monkeypatch, rec):
        missing(monkeypatch, rec, 'gpiod', 'lgpio')
        plant(monkeypatch, rec, 'rpi-gpio')
        assert make_gpio_facade(24, 25)[1] == 'rpi-gpio'

    def test_falls_back_when_a_backend_fails_to_build_not_just_to_import(self, monkeypatch, rec):
        plant(monkeypatch, rec, 'gpiod', fail=PermissionError("/dev/gpiochip0"))
        plant(monkeypatch, rec, 'lgpio')
        assert make_gpio_facade(24, 25)[1] == 'lgpio'

    def test_order_is_gpiod_lgpio_rpi_gpio(self):
        assert AUTO_ORDER == ('gpiod', 'lgpio', 'rpi-gpio')
        assert GPIO_BACKENDS == ('auto',) + AUTO_ORDER


class TestExplicit:
    def test_uses_only_the_named_backend(self, monkeypatch, rec):
        for b in GPIO_BACKENDS[1:]:
            plant(monkeypatch, rec, b)
        assert make_gpio_facade(24, 25, backend='lgpio')[1] == 'lgpio'
        assert [c[0] for c in rec.calls] == ['lgpio']

    def test_no_fallback_when_the_named_backend_fails(self, monkeypatch, rec):
        plant(monkeypatch, rec, 'gpiod')
        missing(monkeypatch, rec, 'lgpio')
        with pytest.raises(GPIOBackendError, match='lgpio'):
            make_gpio_facade(24, 25, backend='lgpio')
        assert rec.calls == []                                  # gpiod was never tried

    def test_unknown_backend_is_a_value_error_naming_the_choices(self, rec):
        with pytest.raises(ValueError, match="'gpio'.*auto, gpiod, lgpio, rpi-gpio"):
            make_gpio_facade(24, 25, backend='gpio')


class TestArguments:
    def test_pins_and_chip_reach_gpiod_and_lgpio(self, monkeypatch, rec):
        plant(monkeypatch, rec, 'gpiod')
        plant(monkeypatch, rec, 'lgpio')
        make_gpio_facade(24, 25, backend='gpiod', chip=4)
        make_gpio_facade(8, None, backend='lgpio', chip=1)
        assert rec.calls == [('gpiod', 24, 25, 4), ('lgpio', 8, None, 1)]

    def test_rpi_gpio_gets_no_chip_argument(self, monkeypatch, rec):
        plant(monkeypatch, rec, 'rpi-gpio')                     # its fake __init__ rejects gpio_chip_id
        assert make_gpio_facade(24, 25, backend='rpi-gpio', chip=4)[1] == 'rpi-gpio'
        assert rec.calls == [('rpi-gpio', 24, 25, None)]


class TestEnvironment:
    def test_env_var_picks_the_backend(self, monkeypatch, rec):
        for b in GPIO_BACKENDS[1:]:
            plant(monkeypatch, rec, b)
        monkeypatch.setenv(gpio_backends.ENV_BACKEND, 'lgpio')
        assert make_gpio_facade(24, 25)[1] == 'lgpio'

    def test_explicit_argument_beats_the_env_var(self, monkeypatch, rec):
        for b in GPIO_BACKENDS[1:]:
            plant(monkeypatch, rec, b)
        monkeypatch.setenv(gpio_backends.ENV_BACKEND, 'lgpio')
        assert make_gpio_facade(24, 25, backend='gpiod')[1] == 'gpiod'

    def test_bad_env_value_is_rejected(self, monkeypatch, rec):
        monkeypatch.setenv(gpio_backends.ENV_BACKEND, 'wiringpi')
        with pytest.raises(ValueError, match='wiringpi'):
            make_gpio_facade(24, 25)


class TestErrors:
    def test_all_failing_lists_every_backend_with_its_reason(self, monkeypatch, rec):
        missing(monkeypatch, rec, 'gpiod')
        plant(monkeypatch, rec, 'lgpio', fail=PermissionError("/dev/gpiochip0"))
        plant(monkeypatch, rec, 'rpi-gpio', fail=RuntimeError("This module can only be run on a Raspberry Pi!"))
        with pytest.raises(GPIOBackendError) as err:
            make_gpio_facade(24, 25)
        msg = str(err.value)
        assert 'gpiod: not installed' in msg and 'apt install python3-libgpiod' in msg
        assert "lgpio: permission denied" in msg and "'gpio' group" in msg
        assert 'rpi-gpio: RuntimeError: This module can only be run' in msg

    def test_old_style_revision_error_explains_the_pi1_case(self, monkeypatch, rec):
        missing(monkeypatch, rec, 'gpiod', 'lgpio')
        plant(monkeypatch, rec, 'rpi-gpio',
              fail=NotImplementedError('This module does not understand old-style revision codes'))
        with pytest.raises(GPIOBackendError) as err:
            make_gpio_facade(24, 25)
        assert 'old-style (Pi 1 family)' in str(err.value)
        assert 'gpio_backend: gpiod or lgpio' in str(err.value)


# --------------------------------------------------------------------------
# drivers/ili9486.py: reads the keys, uses the factory, never touches RPi directly
# --------------------------------------------------------------------------
class _FakeEnum:
    def __getattr__(self, name):
        return name


@pytest.fixture
def driver_modules(monkeypatch):
    """Fake pyili9486 + spidev so ILI9486Driver.begin() runs without hardware."""
    from clockish.drivers import ili9486 as drv

    seen = {}

    class FakeLCD:
        def __init__(self, spi, gpio_facade, origin, sku):
            seen.update(spi=spi, gpio=gpio_facade, origin=origin, sku=sku)

        def begin(self):
            return self

    class FakeSpi:
        def __init__(self, bus, dev):
            seen['spi_args'] = (bus, dev)

    pyili = types.ModuleType('pyili9486')
    pyili.CMD_WRMEM = 0x2C
    pyili.ILI9486 = FakeLCD
    pyili.SKU = _FakeEnum()
    pyili.Origin = _FakeEnum()
    pyili.PixelFormat = types.SimpleNamespace(from_sku=lambda sku: 'rgb666')
    pyili.image_to_data = lambda image, fmt: b''
    spidev = types.ModuleType('spidev')
    spidev.SpiDev = FakeSpi
    monkeypatch.setitem(sys.modules, 'pyili9486', pyili)
    monkeypatch.setitem(sys.modules, 'spidev', spidev)
    monkeypatch.delitem(sys.modules, MODULES['rpi-gpio'], raising=False)
    monkeypatch.delenv(gpio_backends.ENV_BACKEND, raising=False)
    yield drv, seen
    for n in ('SpiDev', 'ILI9486', 'Origin', 'SKU', 'PixelFormat', 'image_to_data', 'CMD_WRMEM'):
        if hasattr(drv, n):
            delattr(drv, n)
    drv._IMPORTS_OK = False


class TestDriverWiring:
    def test_begin_uses_the_configured_backend_pins_and_chip(self, monkeypatch, rec, driver_modules, capsys):
        drv, seen = driver_modules
        for b in GPIO_BACKENDS[1:]:
            plant(monkeypatch, rec, b)
        drv.ILI9486Driver({'gpio_backend': 'lgpio', 'gpio_chip': 3, 'dc_pin': 17, 'rst_pin': 27}).begin()
        assert rec.calls == [('lgpio', 17, 27, 3)]
        assert type(seen['gpio']).__name__ == 'LGPIOFacade'
        assert 'ILI9486: GPIO via lgpio' in capsys.readouterr().out

    def test_default_is_auto_with_the_old_pins(self, monkeypatch, rec, driver_modules):
        drv, _ = driver_modules
        missing(monkeypatch, rec, 'gpiod')
        plant(monkeypatch, rec, 'lgpio')
        drv.ILI9486Driver({}).begin()
        assert rec.calls == [('lgpio', 24, 25, 0)]

    def test_a_pi1_style_failure_of_rpi_gpio_is_not_raised_at_import(self, monkeypatch, rec, driver_modules):
        # rpi-lgpio raises NotImplementedError (not ImportError) at *import* on a Pi 1.  begin()
        # must not import the RPi-namespace facade unless the backend chain reaches it.
        drv, _ = driver_modules
        plant(monkeypatch, rec, 'gpiod')
        plant(monkeypatch, rec, 'rpi-gpio', fail=NotImplementedError('old-style revision codes'))
        drv.ILI9486Driver({}).begin()                          # gpiod wins; no exception
        assert [c[0] for c in rec.calls] == ['gpiod']

    def test_unknown_backend_fails_fast_with_a_clear_message(self, driver_modules):
        drv, _ = driver_modules
        with pytest.raises(ValueError, match='gpio_backend'):
            drv.ILI9486Driver({'gpio_backend': 'wiringpi'}).begin()


class TestNoHardcodedRPiFacade:
    """The RPi.GPIO facade is reachable only through gpio_backends, so a Pi 1 never trips over it."""

    @pytest.mark.parametrize('rel', [
        'drivers/ili9486.py', 'colortest.py', 'colordepth.py', 'colorchart.py', 'fontdemo.py',
    ])
    def test_module_does_not_import_the_rpi_facade_directly(self, rel):
        src = (pathlib.Path(gpio_backends.__file__).parent / rel).read_text()
        assert 'rpilgpio_facade' not in src and 'RPiLGPIOFacade' not in src
        assert 'make_gpio_facade' in src
