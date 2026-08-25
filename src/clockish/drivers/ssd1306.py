"""
clockish.drivers.ssd1306
~~~~~~~~~~~~~~~~~~~~~~~
Display driver for small monochrome SSD1306 OLED panels over I2C.

This backend follows the repository's generic ``DisplayDriver`` contract and
keeps the normal PIL render pipeline intact.  The final frame is converted to a
1-bit monochrome image before being pushed to the panel.  This matches the
Adafruit SSD1306 example pattern used in the upstream CircuitPython samples.

Config keys under ``display:``
------------------------------

.. code-block:: yaml

    display:
      driver:       ssd1306
      width:        128
      height:       64
      rotation:     0          # optional software rotation: 0, 90, 180, 270
      i2c_addr:     0x3C       # optional, default 0x3C
      scl_pin:      ~          # optional override, defaults to board.SCL
      sda_pin:      ~          # optional override, defaults to board.SDA

The actual I2C bus is created from ``board.SCL`` / ``board.SDA`` unless the
config supplies explicit overrides.  The ``adafruit_ssd1306`` library is used
for the panel protocol; ``adafruit-blinka`` provides the I2C backend.
"""

from __future__ import annotations

from PIL import Image

from clockish.drivers.base import DisplayDriver


def _try_import():
    global board_mod, busio_mod, ssd1306_mod, _IMPORTS_OK
    try:
        import adafruit_ssd1306 as _ssd1306
        import board as _board
        import busio as _busio

        board_mod = _board
        busio_mod = _busio
        ssd1306_mod = _ssd1306
        _IMPORTS_OK = True
    except ImportError as exc:  # pragma: no cover - hardware-only dependency
        raise ImportError(
            "The 'adafruit-blinka' and 'adafruit-circuitpython-ssd1306' packages are "
            "required for the SSD1306 driver. Install them on the target Raspberry Pi "
            "or other CircuitPython-capable host.\n"
            f"Original error: {exc}"
        ) from exc


_IMPORTS_OK = False
board_mod = None
busio_mod = None
ssd1306_mod = None


class SSD1306Driver(DisplayDriver):
    """Concrete :class:`~clockish.drivers.base.DisplayDriver` for SSD1306 OLED panels."""

    # Default to clearing the OLED on process exit unless the user explicitly
    # sets `display.clear_on_exit: false` in their profile.
    DEFAULT_CLEAR_ON_EXIT: bool = True

    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg
        self._lcd = None
        self._i2c = None
        self._width = int(cfg.get("width", 128))
        self._height = int(cfg.get("height", 64))

    def begin(self) -> "SSD1306Driver":
        _try_import()
        cfg = self._cfg

        addr = int(cfg.get("i2c_addr", 0x3C), 0) if isinstance(cfg.get("i2c_addr"), str) else int(cfg.get("i2c_addr", 0x3C))
        scl = cfg.get("scl_pin", None)
        sda = cfg.get("sda_pin", None)

        if scl is None:
            scl = board_mod.SCL
        if sda is None:
            sda = board_mod.SDA

        self._i2c = busio_mod.I2C(scl, sda)
        self._lcd = ssd1306_mod.SSD1306_I2C(self._width, self._height, self._i2c, addr=addr)
        return self

    def display(self, image: Image.Image) -> None:
        if self._lcd is None:
            return

        image_to_send = image
        if image_to_send.mode != "1":
            image_to_send = image_to_send.convert("1")

        rotation = int(self._cfg.get("rotation", 0)) % 360
        if rotation:
            image_to_send = image_to_send.rotate(rotation, expand=True)

        if image_to_send.size != (self._width, self._height):
            image_to_send = image_to_send.resize((self._width, self._height), Image.Resampling.NEAREST)

        self._lcd.image(image_to_send)
        self._lcd.show()

    def close(self) -> None:
        if self._i2c is not None and hasattr(self._i2c, "deinit"):
            try:
                self._i2c.deinit()
            except Exception:
                pass
        self._i2c = None
        self._lcd = None

    def clear(self) -> None:
        """Clear the OLED display (black)."""
        if self._lcd is None:
            return
        try:
            # Adafruit SSD1306: fill(0) then show() clears the display
            if hasattr(self._lcd, 'fill'):
                self._lcd.fill(0)
            else:
                # Fallback: send an all-black PIL image
                img = Image.new('1', (self._width, self._height), 0)
                try:
                    self._lcd.image(img)
                except Exception:
                    pass
            self._lcd.show()
        except Exception:
            # Best-effort: ignore any errors during shutdown
            pass

    @property
    def dimensions(self) -> tuple[int, int]:
        return (self._width, self._height)
