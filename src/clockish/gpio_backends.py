"""
clockish.gpio_backends
~~~~~~~~~~~~~~~~~~~~~~
Pick the GPIO library the ILI9486 driver (and its demo utilities) drives the DC/RST pins with.

pyili9486 ships three interchangeable facades.  ``auto`` tries them in order of how current and
how widely supported the underlying library is:

  gpiod     libgpiod v2, the kernel's own GPIO interface.  Actively maintained, works on every
            Pi (the ST7789 driver already uses it).
  lgpio     the lgpio library.  Stable, works on every Pi.
  rpi-gpio  the ``RPi.GPIO`` module, from ``rpi-lgpio`` or the old ``RPi.GPIO``.  Last resort:
            ``rpi-lgpio`` rejects old-style (Pi 1 family) board revisions at import, and
            ``RPi.GPIO`` has had no release since 2022 and does not support the Pi 5.

Only stdlib is imported at module level, so config_validator.py can import GPIO_BACKENDS
without pulling in any hardware library; the facades are imported when one is built.
"""

from __future__ import annotations

import importlib
import os

#: Values the display profile's ``gpio_backend:`` may take.  config_validator.py imports this.
GPIO_BACKENDS: tuple[str, ...] = ('auto', 'gpiod', 'lgpio', 'rpi-gpio')

#: What ``auto`` tries, first working one wins.
AUTO_ORDER: tuple[str, ...] = ('gpiod', 'lgpio', 'rpi-gpio')

#: Environment variable the demo utilities (no config file) read to force a backend.
ENV_BACKEND = 'CLOCKISH_GPIO_BACKEND'

# backend -> (pyili9486 module, facade class, does the constructor take a gpiochip number)
_FACADES: dict[str, tuple[str, str, bool]] = {
    'gpiod':    ('pyili9486.gpio.gpiod_facade',    'GPIODFacade',    True),
    'lgpio':    ('pyili9486.gpio.lgpio_facade',    'LGPIOFacade',    True),
    'rpi-gpio': ('pyili9486.gpio.rpilgpio_facade', 'RPiLGPIOFacade', False),
}

_INSTALL_HINTS = {
    'gpiod':    "install it with 'sudo apt install python3-libgpiod' (or 'pip install gpiod')",
    'lgpio':    "install it with 'sudo apt install python3-lgpio'",
    'rpi-gpio': "install 'python3-rpi-lgpio' (or, on a Pi 1, 'pip install RPi.GPIO')",
}


class GPIOBackendError(RuntimeError):
    """No usable GPIO backend; the message says why each one failed."""


def _why(backend: str, exc: BaseException) -> str:
    """One line: what went wrong with ``backend``, and what to do about it."""
    if isinstance(exc, NotImplementedError) and 'old-style revision' in str(exc):
        return ("this board reports an old-style (Pi 1 family) revision code, which rpi-lgpio "
                "rejects; use gpio_backend: gpiod or lgpio, or install the older RPi.GPIO")
    if isinstance(exc, ImportError):
        return f"not installed ({exc}); {_INSTALL_HINTS[backend]}"
    if isinstance(exc, PermissionError):
        return f"permission denied ({exc}); add your user to the 'gpio' group and log in again"
    return f"{type(exc).__name__}: {exc}"


def _build(backend: str, dc_pin: int, rst_pin: int | None, chip: int):
    module_name, class_name, takes_chip = _FACADES[backend]
    facade_cls = getattr(importlib.import_module(module_name), class_name)
    kwargs = {'gpio_chip_id': chip} if takes_chip else {}
    return facade_cls(dc_pin=dc_pin, rs_pin=rst_pin, **kwargs)


def make_gpio_facade(dc_pin: int, rst_pin: int | None, backend: str | None = None, chip: int = 0):
    """Return ``(facade, backend_name)`` for the first usable backend.

    ``backend`` is one of GPIO_BACKENDS; None reads ``$CLOCKISH_GPIO_BACKEND``, then ``auto``.
    An explicit backend is used alone: if it fails there is no fallback, because the user asked
    for that one.  ``chip`` is the /dev/gpiochipN number for gpiod and lgpio (RPi.GPIO has none).

    Any exception from a facade means "unusable here" -- ImportError when the library is absent,
    NotImplementedError from rpi-lgpio on a Pi 1, OSError/PermissionError from the gpiochip --
    so this deliberately catches Exception and reports every reason together.
    """
    if backend is None:
        backend = os.environ.get(ENV_BACKEND, 'auto')
    if backend not in GPIO_BACKENDS:
        raise ValueError(f"unknown gpio_backend {backend!r}; use one of {', '.join(GPIO_BACKENDS)}")

    order = AUTO_ORDER if backend == 'auto' else (backend,)
    failures: list[tuple[str, BaseException]] = []
    for name in order:
        try:
            return _build(name, dc_pin, rst_pin, chip), name
        except Exception as exc:
            failures.append((name, exc))

    lines = [f"no usable GPIO backend ({', '.join(order)}):"]
    lines += [f"  {name}: {_why(name, exc)}" for name, exc in failures]
    raise GPIOBackendError('\n'.join(lines)) from failures[-1][1]
