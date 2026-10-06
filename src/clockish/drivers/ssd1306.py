"""
clockish.drivers.ssd1306
~~~~~~~~~~~~~~~~~~~~~~~~
Display driver for small monochrome SSD1306 OLED panels over I2C.

The driver talks to the kernel's I2C device (``/dev/i2c-N``) directly: an ``I2C_SLAVE`` ioctl and
plain writes, the same way the framebuffer driver uses ioctls.  Pillow and the standard library are
the only dependencies.  It replaces the Adafruit CircuitPython stack, whose Blinka layer pulled in
``RPi.GPIO`` (which ``rpi-lgpio`` shadows, and which rejects the old-style board revision of a
Pi 1) for what is only an I2C bus.

The init sequence, command framing and page layout are the ones ``adafruit_ssd1306`` uses (checked
byte for byte against it), so a panel behaves as before.  The one difference: the library sends
display-on *before* its init stream, which flashes whatever is in the panel's RAM; this driver
does not.  A frame is converted to 1-bit, packed into the controller's page format by Pillow (no
per-pixel Python loop), and written as one I2C transfer.

Config keys under ``display:``
------------------------------

.. code-block:: yaml

    display:
      driver:       ssd1306
      width:        128        # 128 (64 and other narrow panels are centred in the 128 columns)
      height:       64         # 64 or 32 (any multiple of 8)
      rotation:     0          # optional software rotation: 0, 90, 180, 270
      i2c_addr:     0x3C       # optional, default 0x3C
      i2c_bus:      1          # optional, /dev/i2c-N, default 1

``scl_pin`` / ``sda_pin`` (Blinka pin overrides) are ignored with a warning: the bus is chosen with
``i2c_bus``, and the kernel owns the pins.
"""

from __future__ import annotations

import os

from PIL import Image

from clockish.drivers.base import DisplayDriver

# linux/i2c-dev.h
_I2C_SLAVE = 0x0703

# SSD1306 commands (datasheet; the same values adafruit_ssd1306 uses).
SET_CONTRAST = 0x81
SET_ENTIRE_ON = 0xA4
SET_NORM_INV = 0xA6
SET_DISP = 0xAE
SET_MEM_ADDR = 0x20
SET_COL_ADDR = 0x21
SET_PAGE_ADDR = 0x22
SET_DISP_START_LINE = 0x40
SET_SEG_REMAP = 0xA0
SET_MUX_RATIO = 0xA8
SET_IREF_SELECT = 0xAD
SET_COM_OUT_DIR = 0xC0
SET_DISP_OFFSET = 0xD3
SET_COM_PIN_CFG = 0xDA
SET_DISP_CLK_DIV = 0xD5
SET_PRECHARGE = 0xD9
SET_VCOM_DESEL = 0xDB
SET_CHARGE_PUMP = 0x8D

# I2C control bytes: Co=1 D/C#=0 (one command follows), Co=0 D/C#=1 (display data follows).
_CTRL_COMMAND = 0x80
_CTRL_DATA = 0x40


def init_commands(width: int, height: int) -> list[int]:
    """The command stream that brings the panel up (adafruit_ssd1306.init_display()'s, verbatim)."""
    return [
        SET_DISP,                                           # off
        SET_MEM_ADDR, 0x00,                                 # horizontal addressing
        SET_DISP_START_LINE,
        SET_SEG_REMAP | 0x01,                               # column 127 -> SEG0
        SET_MUX_RATIO, height - 1,
        SET_COM_OUT_DIR | 0x08,                             # scan COM[N] to COM0
        SET_DISP_OFFSET, 0x00,
        SET_COM_PIN_CFG, 0x02 if width > 2 * height else 0x12,
        SET_DISP_CLK_DIV, 0x80,
        SET_PRECHARGE, 0xF1,                                # internal VCC
        SET_VCOM_DESEL, 0x30,                               # 0.83 * Vcc
        SET_CONTRAST, 0xFF,
        SET_ENTIRE_ON,                                      # output follows RAM
        SET_NORM_INV,                                       # not inverted
        SET_IREF_SELECT, 0x30,
        SET_CHARGE_PUMP, 0x14,                              # internal VCC
        SET_DISP | 0x01,                                    # on
    ]


def pack_pages(image: Image.Image) -> bytes:
    """Pack a 1-bit image into SSD1306 page format: page by page, one byte per 8 vertical pixels.

    Bit 0 of a byte is the topmost pixel of its page, a lit pixel is a set bit.  Transposing makes
    every original column a row of ``height`` pixels, which Pillow packs LSB-first into
    ``height / 8`` bytes; slicing out every ``pages``-th byte then regroups column-major into
    page-major, with no per-pixel loop.
    """
    pages = image.height // 8
    columns = image.transpose(Image.Transpose.TRANSPOSE).tobytes("raw", "1;R")
    return b"".join(columns[p::pages] for p in range(pages))


class _I2CDevice:
    """One slave on a /dev/i2c-N bus; ``write`` is a single I2C transfer."""

    def __init__(self, bus: int, addr: int) -> None:
        import fcntl  # Linux only; imported here so the module loads on a dev machine

        path = f"/dev/i2c-{bus}"
        try:
            self._fd = os.open(path, os.O_RDWR)
        except OSError as exc:
            raise RuntimeError(
                f"cannot open {path}: {exc.strerror}. Enable I2C (sudo raspi-config nonint do_i2c 0) "
                "and make sure your user is in the 'i2c' group."
            ) from exc
        try:
            fcntl.ioctl(self._fd, _I2C_SLAVE, addr)
        except OSError as exc:
            os.close(self._fd)
            raise RuntimeError(f"cannot address I2C device 0x{addr:02X} on {path}: {exc.strerror}") from exc

    def write(self, data: bytes) -> None:
        written = os.write(self._fd, data)
        if written != len(data):
            raise OSError(f"short I2C write: {written} of {len(data)} bytes")

    def close(self) -> None:
        os.close(self._fd)


class SSD1306Driver(DisplayDriver):
    """Concrete :class:`~clockish.drivers.base.DisplayDriver` for SSD1306 OLED panels."""

    # Default to clearing the OLED on process exit unless the user explicitly
    # sets `display.clear_on_exit: false` in their profile.
    DEFAULT_CLEAR_ON_EXIT: bool = True

    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg
        self._dev: _I2CDevice | None = None
        self._width = int(cfg.get("width", 128))
        self._height = int(cfg.get("height", 64))
        if self._height <= 0 or self._height % 8:
            raise ValueError(f"SSD1306 height must be a positive multiple of 8, got {self._height}")
        if not 0 < self._width <= 128:
            raise ValueError(f"SSD1306 width must be 1..128, got {self._width}")
        self._pages = self._height // 8
        # Narrow panels sit in the middle of the controller's 128 columns.
        self._col_offset = (128 - self._width) // 2

    def begin(self) -> "SSD1306Driver":
        cfg = self._cfg
        raw_addr = cfg.get("i2c_addr", 0x3C)
        addr = int(raw_addr, 0) if isinstance(raw_addr, str) else int(raw_addr)
        bus = int(cfg.get("i2c_bus", 1))

        for key in ("scl_pin", "sda_pin"):
            if cfg.get(key) is not None:
                print(f"WARNING: SSD1306: '{key}' is ignored (it was a Blinka pin override); "
                      "the I2C bus is chosen with 'i2c_bus'.")

        self._dev = _I2CDevice(bus, addr)
        try:
            self._commands(init_commands(self._width, self._height))
            self.push(bytes(self._width * self._pages))                  # start from a black panel
        except OSError as exc:
            self.close()
            raise RuntimeError(
                f"no response from the SSD1306 at 0x{addr:02X} on /dev/i2c-{bus} ({exc.strerror or exc}); "
                f"check the wiring and run 'i2cdetect -y {bus}'."
            ) from exc
        return self

    def _commands(self, commands) -> None:
        """Send commands as one transfer: a Co=1 control byte in front of each."""
        assert self._dev is not None
        self._dev.write(b"".join(bytes((_CTRL_COMMAND, c)) for c in commands))

    def display(self, image: Image.Image) -> None:
        self.push(self.prepare(image))

    # prepare() is all the CPU work (1-bit conversion, rotation, page packing); push() is only the
    # two I2C transfers, so the tick-aligned lead measures just the bus time.
    def prepare(self, image: Image.Image) -> bytes:
        """Convert a frame to the panel's page bytes."""
        img = image if image.mode == "1" else image.convert("1")

        rotation = int(self._cfg.get("rotation", 0)) % 360
        if rotation:
            img = img.rotate(rotation, expand=True)

        if img.size != (self._width, self._height):
            img = img.resize((self._width, self._height), Image.Resampling.NEAREST)

        return pack_pages(img)

    def push(self, frame: bytes) -> None:
        """Write a :meth:`prepare` frame to the whole panel."""
        if self._dev is None:
            return
        first = self._col_offset
        self._commands((SET_COL_ADDR, first, first + self._width - 1, SET_PAGE_ADDR, 0, self._pages - 1))
        self._dev.write(bytes((_CTRL_DATA,)) + frame)

    def close(self) -> None:
        if self._dev is not None:
            try:
                self._dev.close()
            except OSError:
                pass
        self._dev = None

    def clear(self) -> None:
        """Clear the OLED display (black)."""
        if self._dev is None:
            return
        try:
            self.push(bytes(self._width * self._pages))
        except OSError:
            # Best-effort: ignore any errors during shutdown
            pass

    @property
    def dimensions(self) -> tuple[int, int]:
        return (self._width, self._height)
