"""
clockish.drivers.st7789
~~~~~~~~~~~~~~~~~~~~~~~
Display driver for ST7789-based SPI TFT screens (Pimoroni ``st7789`` library).

Tested with:
  * Adafruit 1.14" 240x135 Color TFT (product #4383)  --  the ST7789 controller
    internally manages a 320x240 framebuffer; the visible panel is addressed via
    ``offset_left`` / ``offset_top``.
  * Pimoroni 240x240 round-corner displays

Library: https://github.com/pimoroni/st7789-python
Install: ``pip install st7789``  (Pi only  --  requires gpiod / spidev)

Config keys (all optional  --  defaults listed below) under ``display:`` in YAML:

.. code-block:: yaml

    display:
      driver:       st7789
      width:        240        # logical canvas width  (PIL image width)
      height:       135        # logical canvas height (PIL image height)

      # Hardware / GPIO
      spi_port:     0          # SPI bus number
      spi_cs:       0          # SPI chip-select (0 = CE0, 1 = CE1)
      dc_pin:       1          # BCM GPIO for Data/Command
      rst_pin:      ~          # BCM GPIO for Reset (null = not connected)
      backlight_pin: ~         # BCM GPIO for backlight (null = always on)
      spi_speed_hz: 64000000

      # Display panel geometry
      offset_left:  0          # X offset into ST7789 controller framebuffer
      offset_top:   0          # Y offset into ST7789 controller framebuffer
      invert:       true       # colour inversion (required by most ST7789 panels)

      # Image rotation applied in software before sending pixels.
      # NOTE: the Pimoroni library only supports rotation  in  {0, 180} for
      # non-square panels (width != height).  Use 0 for landscape panels
      # such as the Adafruit 240x135  --  the hardware already renders them
      # landscape.  For square panels (240x240) 90/270 are also valid.
      rotation:     0

Adafruit 1.14" 240x135 example:

.. code-block:: yaml

    display:
      driver:       st7789
      width:        240
      height:       135
      spi_port:     0
      spi_cs:       0
      dc_pin:       25         # board.D25 in CircuitPython terms
      backlight_pin: ~
      spi_speed_hz: 64000000
      offset_left:  53
      offset_top:   40
      invert:       true
      rotation:     0
"""

from __future__ import annotations

from PIL import Image

from clockish.drivers.base import DisplayDriver


def _try_import():
    global st7789_mod, _IMPORTS_OK
    try:
        import st7789 as _st7789
        st7789_mod = _st7789
        _IMPORTS_OK = True
    except ImportError as exc:
        raise ImportError(
            "The 'st7789' package (Pimoroni) is required for the ST7789 driver "
            "and is only available on Raspberry Pi hardware.  "
            "Install it with:  pip install st7789\n"
            f"Original error: {exc}"
        ) from exc


_IMPORTS_OK = False
st7789_mod = None


class ST7789Driver(DisplayDriver):
    """Concrete :class:`~clockish.drivers.base.DisplayDriver` for ST7789 displays.

    Uses the Pimoroni ``st7789`` library (``pip install st7789``).
    All configuration is read from the ``display:`` section of the YAML config
    dict passed to the constructor.
    """

    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg
        self._lcd = None
        self._width  = int(cfg.get("width",  240))
        self._height = int(cfg.get("height", 135))

    # ------------------------------------------------------------------
    def begin(self) -> "ST7789Driver":
        """Initialise the ST7789 hardware.

        The Pimoroni ST7789 library performs all hardware setup (SPI open,
        GPIO, display init sequence) inside ``__init__``, so this method
        simply constructs the object and returns *self*.
        """
        _try_import()
        cfg = self._cfg

        port         = int(cfg.get("spi_port",     0))
        cs           = int(cfg.get("spi_cs",        0))
        dc           = int(cfg.get("dc_pin",        1))
        rst          = cfg.get("rst_pin",       None)
        backlight    = cfg.get("backlight_pin", None)
        spi_speed    = int(cfg.get("spi_speed_hz",  64_000_000))
        offset_left  = int(cfg.get("offset_left",   0))
        offset_top   = int(cfg.get("offset_top",    0))
        invert       = bool(cfg.get("invert",        True))
        rotation     = int(cfg.get("rotation",       0))

        # The Pimoroni library raises ValueError for non-square panels if
        # rotation is 90 or 270.  Guard against misconfigured YAMLs.
        if self._width != self._height and rotation in (90, 270):
            import sys
            print(
                f"WARNING: ST7789 driver  --  rotation={rotation} is not supported for "
                f"non-square panels ({self._width}x{self._height}).  "
                "Falling back to rotation=0.  "
                "Set 'rotation: 0' or 'rotation: 180' in your config to silence this warning.",
                file=sys.stderr,
            )
            rotation = 0

        if rst is not None:
            rst = int(rst)  # type: ignore[arg-type]
        if backlight is not None:
            backlight = int(backlight)  # type: ignore[arg-type]

        self._lcd = st7789_mod.ST7789(  # type: ignore[union-attr]
            port=port,
            cs=cs,
            dc=dc,
            rst=rst,
            backlight=backlight,
            width=self._width,
            height=self._height,
            rotation=rotation,
            invert=invert,
            spi_speed_hz=spi_speed,
            offset_left=offset_left,
            offset_top=offset_top,
        )
        return self

    # ------------------------------------------------------------------
    def display(self, image: Image.Image) -> None:
        """Push a PIL Image frame to the physical display."""
        self._lcd.display(image)

    def close(self) -> None:
        """Close the internal SPI bus opened by the Pimoroni library."""
        if self._lcd is not None and hasattr(self._lcd, "_spi"):
            try:
                self._lcd._spi.close()
            except Exception:
                pass
            self._lcd = None

    # ------------------------------------------------------------------
    @property
    def dimensions(self) -> tuple[int, int]:
        """Return ``(width, height)`` as the logical canvas dimensions."""
        if self._lcd is not None:
            return (self._lcd.width, self._lcd.height)
        return (self._width, self._height)

    @property
    def is_landscape(self) -> bool:
        w, h = self.dimensions
        return w > h
