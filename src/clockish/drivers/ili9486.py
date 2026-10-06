"""
clockish.drivers.ili9486
~~~~~~~~~~~~~~~~~~~~~~~~
Display driver for ILI9486-based SPI TFT screens wired to a Raspberry Pi.

Tested with:
  * MPI3501   --  3.5" RPi display, RGB666 pixel format
  * MHS3528   --  3.5" RPi display, RGB565 pixel format

Config keys (all optional  --  defaults listed below) go under the ``display:``
section of your YAML config:

.. code-block:: yaml

    display:
      driver:       ili9486
      width:        320
      height:       480
      rotation:     90       # 0 / 90 / 180 / 270
      sku:          MPI3501  # MPI3501 | MHS3528
      spi_bus:      0
      spi_device:   0
      spi_speed_hz: 64000000
      dc_pin:       24       # BCM GPIO number for Data/Command
      rst_pin:      25       # BCM GPIO number for Reset
      gpio_backend: auto     # auto | gpiod | lgpio | rpi-gpio  (see clockish.gpio_backends)
      gpio_chip:    0        # /dev/gpiochipN for gpiod/lgpio; ignored by rpi-gpio

Hardware dependencies (Pi-only):
  * pyili9486   --  https://github.com/SirLefti/Python_ILI9486
  * spidev
  * one GPIO library: gpiod (preferred), lgpio, or RPi.GPIO / rpi-lgpio.  ``auto`` tries them in
    that order, so a Pi 1 (which rpi-lgpio rejects) works without RPi.GPIO.
"""

from __future__ import annotations

from PIL import Image

from clockish.drivers.base import DisplayDriver
from clockish.gpio_backends import make_gpio_facade

# Lazy imports so the module can be *imported* on non-Pi platforms without
# raising ImportError.  The error surfaces only when begin() is called.
_IMPORTS_OK = False


def _try_import():
    global _IMPORTS_OK, SpiDev, ILI9486, Origin, SKU
    global PixelFormat, image_to_data, CMD_WRMEM
    try:
        from pyili9486 import CMD_WRMEM as _CMD_WRMEM
        from pyili9486 import ILI9486 as _ILI9486  # noqa: F401
        from pyili9486 import SKU as _SKU
        from pyili9486 import Origin as _Origin
        from pyili9486 import PixelFormat as _PixelFormat
        from pyili9486 import image_to_data as _image_to_data
        from spidev import SpiDev as _SpiDev  # noqa: F401
        SpiDev = _SpiDev
        ILI9486 = _ILI9486
        Origin = _Origin
        SKU = _SKU
        PixelFormat = _PixelFormat
        image_to_data = _image_to_data
        CMD_WRMEM = _CMD_WRMEM
        _IMPORTS_OK = True
    except ImportError as exc:
        raise ImportError(
            "pyili9486 and spidev are required for the ILI9486 driver "
            "and are only available on Raspberry Pi hardware.  "
            f"Original error: {exc}"
        ) from exc


# Maps user-facing rotation degrees -> pyili9486 Origin corner constants.
_ROTATION_TO_ORIGIN_NAME = {
    0:   "UPPER_LEFT",
    90:  "UPPER_RIGHT",
    180: "LOWER_RIGHT",
    270: "LOWER_LEFT",
}

# Maps SKU strings to pyili9486 SKU enum names.
_SKU_NAMES = {
    "MPI3501": "MPI3501",
    "MHS3528": "MHS3528",
}


class ILI9486Driver(DisplayDriver):
    """Concrete :class:`~clockish.drivers.base.DisplayDriver` for ILI9486 displays.

    All configuration is read from the ``display:`` section of the YAML config
    dict passed to the constructor.
    """

    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg
        self._spi = None
        self._lcd = None
        self._pixel_format = None

    # ------------------------------------------------------------------
    def begin(self) -> "ILI9486Driver":
        """Open SPI, configure GPIO, and initialise the ILI9486 controller."""
        _try_import()
        cfg = self._cfg

        rotation   = int(cfg.get("rotation",     90))
        sku_name   = str(cfg.get("sku",          "MPI3501")).upper()
        spi_bus    = int(cfg.get("spi_bus",       0))
        spi_dev    = int(cfg.get("spi_device",    0))
        spi_speed  = int(cfg.get("spi_speed_hz",  64_000_000))
        dc_pin     = int(cfg.get("dc_pin",        24))
        rst_pin    = int(cfg.get("rst_pin",       25))
        gpio_backend = str(cfg.get("gpio_backend", "auto"))
        gpio_chip  = int(cfg.get("gpio_chip",     0))

        origin_name = _ROTATION_TO_ORIGIN_NAME.get(rotation, "UPPER_RIGHT")
        origin      = getattr(Origin, origin_name)

        resolved_sku_name = _SKU_NAMES.get(sku_name, "MPI3501")
        sku = getattr(SKU, resolved_sku_name)
        self._pixel_format = PixelFormat.from_sku(sku)

        # GPIOBackendError / ValueError here is fatal on purpose: with no GPIO there is no display.
        gpio, gpio_name = make_gpio_facade(dc_pin, rst_pin, backend=gpio_backend, chip=gpio_chip)
        print(f"ILI9486: GPIO via {gpio_name}")

        spi = SpiDev(spi_bus, spi_dev)
        spi.mode = 0b10
        spi.max_speed_hz = spi_speed
        self._spi = spi

        self._lcd = ILI9486(spi=spi, gpio_facade=gpio, origin=origin, sku=sku).begin()
        return self

    # ------------------------------------------------------------------
    def display(self, image: Image.Image) -> None:
        """Push a PIL Image frame to the physical display."""
        self.push(self.prepare(image))

    # pyili9486's display() split in two: RGB666/565 conversion (~60ms on a
    # Pi 2, no visible change) ahead of the tick, SPI transfer on it.
    def prepare(self, image: Image.Image) -> bytes | list[int]:
        """Convert a full-screen RGB frame to the panel's pixel bytes."""
        return image_to_data(image, self._pixel_format)

    def push(self, frame: bytes | list[int]) -> None:
        """Write :meth:`prepare` bytes to the whole screen."""
        self._lcd.set_window()
        self._lcd.command(CMD_WRMEM)
        self._lcd.data(frame)

    def idle(self, state: bool = True) -> None:
        """Forward idle-mode request to the underlying ILI9486 object."""
        self._lcd.idle(state)

    def close(self) -> None:
        """Close the SPI bus.  (The GPIO facade releases its lines itself.)"""
        if self._spi is not None:
            self._spi.close()
            self._spi = None

    # ------------------------------------------------------------------
    @property
    def dimensions(self) -> tuple[int, int]:
        """Return ``(width, height)`` as reported by the ILI9486 object."""
        return self._lcd.dimensions

    @property
    def is_landscape(self) -> bool:
        """Delegate to the underlying ILI9486 object."""
        return self._lcd.is_landscape
