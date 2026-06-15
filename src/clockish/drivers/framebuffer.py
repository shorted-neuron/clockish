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

Console / cursor suppression
-----------------------------
The driver automatically puts the active virtual terminal (VT) into
``KD_GRAPHICS`` mode when ``begin()`` is called.  This is the same
mechanism used by X11, Wayland, SDL, and pygame — it instructs the
kernel's ``fbcon`` driver to stop rendering VT text and the blinking
cursor on top of the framebuffer.  The VT is restored to ``KD_TEXT``
mode when ``close()`` is called.

This means masking ``getty@tty1`` is sufficient for a clean display;
no manual cursor-hiding or ``chvt`` tricks are needed.

If you still want to suppress the boot splash/logo, add these to
``/boot/firmware/cmdline.txt``::

    quiet logo.nologo vt.global_cursor_default=0

The last parameter is a belt-and-suspenders fallback that disables the
hardware cursor even before ``KD_GRAPHICS`` takes effect.

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
import os
import struct
import sys

from PIL import Image

from clockish.drivers.base import DisplayDriver

# Linux ioctl codes for framebuffer
_FBIOGET_VSCREENINFO = 0x4600
_FBIOBLANK           = 0x4611

# struct fb_var_screeninfo field offsets (all __u32, so same on 32-bit and 64-bit)
_OFF_XRES       = 0
_OFF_YRES       = 4
_OFF_XRES_V     = 8
_OFF_YRES_V     = 12
_OFF_BPP        = 24
_OFF_RED_OFF    = 32   # red.offset   (bit position of red channel)
_OFF_GREEN_OFF  = 44   # green.offset
_OFF_BLUE_OFF   = 56   # blue.offset

# VT / console ioctl codes — suppress the kernel cursor
_KDSETMODE  = 0x4B3A   # set VT mode
_KD_TEXT    = 0x00     # normal text mode (restore on exit)
_KD_GRAPHICS = 0x01    # graphics mode — fbcon stops rendering cursor + text


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
        self._tty     = None   # handle to /dev/tty0 for KD_GRAPHICS mode
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

        # Put the active VT into graphics mode so fbcon stops drawing
        # the blinking cursor (and any text) on top of our framebuffer.
        self._enter_graphics_mode()

        return self

    # ------------------------------------------------------------------
    def _enter_graphics_mode(self) -> None:
        """Switch the active VT to KD_GRAPHICS mode.

        This instructs the kernel's fbcon driver to stop rendering the
        VT cursor and text on top of the framebuffer — the same trick
        used by X11, Wayland, SDL, and pygame.

        /dev/tty0 always refers to the foreground VT regardless of where
        the process was started (systemd service, SSH session, serial
        console, etc.).  The user must be in the ``video`` or ``tty``
        group — the same requirement as opening /dev/fb0 itself, so this
        should always succeed in a correctly configured install.

        If the open or ioctl fails (e.g. missing group membership), a
        warning is printed and the display continues without cursor
        suppression.
        """
        # Open /dev/tty0 in a separate try so we know unambiguously
        # whether the file handle was obtained before handling errors.
        #
        # /dev/tty0 permissions: crw--w---- root tty
        # Group 'tty' has write-only (-w-), NOT read+write.
        # Open O_WRONLY | O_NOCTTY — we only need to write escape sequences
        # and issue ioctls; O_NOCTTY prevents the OS from making this the
        # process's controlling terminal when run from an interactive shell.
        try:
            fd  = os.open('/dev/tty0', os.O_WRONLY | os.O_NOCTTY)
            tty = os.fdopen(fd, 'wb', buffering=0)
        except OSError as exc:
            print(
                f"WARNING: cannot open /dev/tty0 ({exc}) — cursor suppression unavailable.\n"
                f"  Ensure the clockish user is in the 'tty' or 'video' group.",
                file=sys.stderr,
            )
            self._tty = None
            return

        # tty is open; now configure it.  If anything here fails, close
        # the handle cleanly and carry on without cursor suppression.
        try:
            tty.write(b'\033[?25l')                      # ANSI hide cursor (belt-and-suspenders)
            fcntl.ioctl(tty, _KDSETMODE, _KD_GRAPHICS)  # fbcon hands off the framebuffer
            self._tty = tty
        except OSError as exc:
            print(
                f"WARNING: KDSETMODE KD_GRAPHICS failed ({exc}) — cursor suppression unavailable.",
                file=sys.stderr,
            )
            try:
                tty.close()
            except OSError:
                pass
            self._tty = None

    def _leave_graphics_mode(self) -> None:
        """Restore the VT to KD_TEXT mode on exit."""
        if self._tty is not None:
            try:
                fcntl.ioctl(self._tty, _KDSETMODE, _KD_TEXT)
                self._tty.write(b'\033[?25h')   # restore cursor
                self._tty.close()
            except OSError:
                pass
            self._tty = None

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
        try:
            fcntl.ioctl(self._fb, _FBIOBLANK, 1 if state else 0)
        except OSError:
            pass   # not all framebuffer drivers support FBIOBLANK

    def close(self) -> None:
        """Restore the VT, unmap the framebuffer, and close the device."""
        self._leave_graphics_mode()
        if self._mm is not None:
            self._mm.close()
            self._mm = None
        if self._fb is not None:
            self._fb.close()
            self._fb = None

    @property
    def dimensions(self) -> tuple[int, int]:
        return (self._width, self._height)

