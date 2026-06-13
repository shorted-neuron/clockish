"""
clockish.drivers.framebuffer
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Display driver for any Linux framebuffer device (``/dev/fb0``).

Works with:
  * Raspberry Pi Official 7" DSI Touch Display (800×480)
  * Raspberry Pi Touch Display 2 (800×480)
  * HDMI monitors (any resolution the Pi initialises at boot)
  * Any display that the Pi kernel exposes as ``/dev/fb*``

No extra pip packages are required — the driver uses only Python's
standard library (``fcntl``, ``mmap``, ``struct``) plus ``numpy``,
which is already installed as a clockish dependency.

Framebuffer access requires the user to be in the ``video`` group:
    sudo usermod -aG video $USER   (then log out and back in)

Console / terminal overlap
--------------------------
By default the Pi boots into a text console on ``/dev/tty1`` which is
displayed on top of anything written to the framebuffer.  To give
clockish a clean framebuffer run it over SSH, or suppress the console
overlay before starting::

    # Hide the cursor and switch to an empty VT
    sudo sh -c 'echo -n "\\033[?25l" > /dev/tty1'
    sudo chvt 2          # switch to virtual terminal 2 (blank)
    clockish             # now clockish owns /dev/fb0 cleanly
    sudo chvt 1          # restore when done

Alternatively, add ``logo.nologo quiet`` to the kernel command line in
``/boot/firmware/cmdline.txt`` and mask ``getty@tty1`` so no console
text appears at all::

    sudo systemctl mask getty@tty1

Config keys (all optional — defaults listed below) under ``display:``
in your display profile::

    display:
      driver:   framebuffer
      device:   /dev/fb0   # framebuffer device node
      width:    800        # must match the actual display width
      height:   480        # must match the actual display height
      rotation: 0          # software rotation: 0 / 90 / 180 / 270

``width`` and ``height`` must match your physical display.
Check yours with::

    fbset -i              # shows geometry line, e.g. "geometry 800 480 ..."
    # or:
    cat /sys/class/graphics/fb0/virtual_size   # e.g. "800,480"
"""

from __future__ import annotations

import fcntl
import mmap
import struct
import sys

from PIL import Image

from clockish.drivers.base import DisplayDriver

# Linux ioctl codes for framebuffer
_FBIOGET_VSCREENINFO = 0x4600

# struct fb_var_screeninfo field offsets (all __u32, so same on 32-bit and 64-bit)
_OFF_XRES       = 0
_OFF_YRES       = 4
_OFF_XRES_V     = 8
_OFF_YRES_V     = 12
_OFF_BPP        = 24
_OFF_RED_OFF    = 32   # red.offset   (bit position of red channel)
_OFF_GREEN_OFF  = 44   # green.offset
_OFF_BLUE_OFF   = 56   # blue.offset


class FramebufferDriver(DisplayDriver):
    """Concrete :class:`~clockish.drivers.base.DisplayDriver` for Linux
    framebuffer devices.

    Reads pixel format and geometry directly from the kernel via
    ``FBIOGET_VSCREENINFO``, then ``mmap``\\s the device for fast frame
    writes.  Supports 16-bpp (RGB565) and 32-bpp (XRGB8888 / ARGB8888)
    framebuffers.
    """

    def __init__(self, cfg: dict) -> None:
        self._cfg     = cfg
        self._fb      = None
        self._mm      = None
        # These will be overwritten by begin() from the actual framebuffer.
        self._width   = int(cfg.get('width',  800))
        self._height  = int(cfg.get('height', 480))
        self._bpp     = 32
        self._red_off   = 16
        self._green_off = 8
        self._blue_off  = 0
        self._line_bytes = self._width * 4
        self._rotation = int(cfg.get('rotation', 0))

    # ------------------------------------------------------------------
    def begin(self) -> 'FramebufferDriver':
        """Open and memory-map the framebuffer device."""
        device = self._cfg.get('device', '/dev/fb0')

        try:
            self._fb = open(device, 'rb+')
        except PermissionError:
            sys.exit(
                f"ERROR: cannot open {device} — permission denied.\n"
                f"  Add yourself to the video group:  sudo usermod -aG video $USER\n"
                f"  Then log out and back in (or reboot)."
            )
        except FileNotFoundError:
            sys.exit(
                f"ERROR: framebuffer device not found: {device}\n"
                f"  Check that the display is connected and the Pi has booted with it enabled."
            )

        # --- Read hardware geometry and pixel format via ioctl ----------
        vinfo = bytearray(160)   # fb_var_screeninfo is ≤ 160 bytes
        try:
            fcntl.ioctl(self._fb, _FBIOGET_VSCREENINFO, vinfo)

            xres       = struct.unpack_from('I', vinfo, _OFF_XRES)[0]
            yres       = struct.unpack_from('I', vinfo, _OFF_YRES)[0]
            xres_v     = struct.unpack_from('I', vinfo, _OFF_XRES_V)[0]
            bpp        = struct.unpack_from('I', vinfo, _OFF_BPP)[0]
            red_off    = struct.unpack_from('I', vinfo, _OFF_RED_OFF)[0]
            green_off  = struct.unpack_from('I', vinfo, _OFF_GREEN_OFF)[0]
            blue_off   = struct.unpack_from('I', vinfo, _OFF_BLUE_OFF)[0]

            self._bpp        = bpp
            self._red_off    = red_off
            self._green_off  = green_off
            self._blue_off   = blue_off
            self._line_bytes = xres_v * (bpp // 8)

            # Warn if config dimensions don't match reality
            cfg_w = int(self._cfg.get('width',  0))
            cfg_h = int(self._cfg.get('height', 0))
            if cfg_w and cfg_w != xres:
                print(
                    f"WARNING: display profile width={cfg_w} but {device} reports "
                    f"{xres}px wide — update your display profile.",
                    file=sys.stderr,
                )
            if cfg_h and cfg_h != yres:
                print(
                    f"WARNING: display profile height={cfg_h} but {device} reports "
                    f"{yres}px tall — update your display profile.",
                    file=sys.stderr,
                )
            self._width  = xres
            self._height = yres

        except OSError as exc:
            print(
                f"WARNING: FBIOGET_VSCREENINFO failed ({exc}); "
                "falling back to config dimensions and assuming 32bpp XRGB.",
                file=sys.stderr,
            )
            self._line_bytes = self._width * (self._bpp // 8)

        # --- Memory-map the framebuffer ---------------------------------
        fb_size = self._line_bytes * self._height
        self._mm = mmap.mmap(
            self._fb.fileno(), fb_size,
            mmap.MAP_SHARED,
            mmap.PROT_WRITE | mmap.PROT_READ,
        )

        print(
            f"Framebuffer: {device}  {self._width}×{self._height}  "
            f"{self._bpp}bpp  R<<{self._red_off} G<<{self._green_off} B<<{self._blue_off}"
        )
        return self

    # ------------------------------------------------------------------
    def display(self, image: Image.Image) -> None:
        """Convert a PIL Image to the framebuffer's native pixel format and write it."""
        import numpy as np

        # Apply software rotation if requested
        if self._rotation == 90:
            image = image.rotate(-90, expand=True)
        elif self._rotation == 180:
            image = image.rotate(180)
        elif self._rotation == 270:
            image = image.rotate(90, expand=True)

        arr = np.asarray(image.convert('RGB'), dtype=np.uint32)

        if self._bpp == 16:
            # Pack as RGB565
            r = ((arr[:, :, 0] >> 3) << 11).astype(np.uint16)
            g = ((arr[:, :, 1] >> 2) << 5).astype(np.uint16)
            b =  (arr[:, :, 2] >> 3).astype(np.uint16)
            pixels = (r | g | b).tobytes()
        else:
            # Pack as 32bpp using hardware-reported channel positions
            pixels = (
                (arr[:, :, 0] << self._red_off)   |
                (arr[:, :, 1] << self._green_off)  |
                (arr[:, :, 2] << self._blue_off)
            ).tobytes()

        self._mm.seek(0)
        self._mm.write(pixels)

    def idle(self, state: bool = True) -> None:
        """Blank (``True``) or unblank (``False``) the framebuffer console."""
        # FBIOBLANK = 0x4611; value 1 = blank, 0 = unblank
        _FBIOBLANK = 0x4611
        try:
            fcntl.ioctl(self._fb, _FBIOBLANK, 1 if state else 0)
        except OSError:
            pass   # not all framebuffer drivers support FBIOBLANK

    def close(self) -> None:
        """Unmap the framebuffer and close the device."""
        if self._mm is not None:
            self._mm.close()
            self._mm = None
        if self._fb is not None:
            self._fb.close()
            self._fb = None

    @property
    def dimensions(self) -> tuple[int, int]:
        return (self._width, self._height)

