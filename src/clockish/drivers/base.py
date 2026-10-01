"""
clockish.drivers.base
~~~~~~~~~~~~~~~~~~~~~
Abstract base class for display hardware drivers.

All display backends must subclass :class:`DisplayDriver` and implement the
abstract methods.  Optional hooks (``prepare``/``push``, ``idle``, ``close``)
have pass-through/no-op defaults so minimal drivers need only implement ``begin`` and
``display``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from PIL import Image


class DisplayDriver(ABC):
    """Abstract interface that every display backend must satisfy.

    Drivers MAY set the class attribute ``DEFAULT_CLEAR_ON_EXIT`` to True to
    indicate that, if the display's config does not explicitly set
    ``clear_on_exit:`` in the display profile, the driver should default to
    clearing the panel when clockish exits.  The base class default is False.
    """
    DEFAULT_CLEAR_ON_EXIT: bool = False
    """Abstract interface that every display backend must satisfy.

    Life-cycle
    ----------
    1. Instantiate with the ``display`` config dict:
           driver = MyDriver(display_cfg)
    2. Call :meth:`begin`  --  opens SPI/I2C/USB, sets up GPIO, returns *self*:
           driver = driver.begin()
    3. Call :meth:`display` once per frame to push a PIL image (or
       :meth:`prepare` + :meth:`push`, to convert ahead of a deadline).
    4. Call :meth:`close` when done to release hardware resources.

    Implementations are encouraged (but not required) to honour ``idle`` for
    power saving.
    """

    # ------------------------------------------------------------------
    # Abstract interface  --  subclasses MUST override these
    # ------------------------------------------------------------------

    @abstractmethod
    def begin(self) -> "DisplayDriver":
        """Initialise the display hardware.  Returns *self* for chaining."""
        ...

    @abstractmethod
    def display(self, image: Image.Image) -> None:
        """Push a full PIL :class:`~PIL.Image.Image` frame to the display."""
        ...

    @property
    @abstractmethod
    def dimensions(self) -> tuple[int, int]:
        """Return ``(width, height)`` in pixels as reported by the hardware.

        Values reflect any rotation applied during :meth:`begin`.
        """
        ...

    # ------------------------------------------------------------------
    # Optional hooks  --  subclasses MAY override these
    # ------------------------------------------------------------------

    def prepare(self, image: Image.Image) -> Any:
        """Convert *image* to the panel's wire format; hand the result to :meth:`push`.

        Split from :meth:`push` so the main loop can do the CPU-bound
        conversion BEFORE sleeping to the second boundary, leaving only the
        transfer -- the part during which pixels actually change -- to be
        timed onto the tick.  Default: no conversion, return *image* as-is.
        Drivers overriding this must override :meth:`push` too, and should
        implement :meth:`display` as ``self.push(self.prepare(image))``.
        """
        return image

    def push(self, frame: Any) -> None:
        """Send a :meth:`prepare` result to the panel.  Default: :meth:`display`."""
        self.display(frame)

    def idle(self, state: bool = True) -> None:
        """Enter (``True``) or exit (``False``) low-power idle mode.

        No-op by default; override if the hardware supports it.
        """

    def close(self) -> None:
        """Release hardware resources (SPI bus, GPIO lines, file handles, ...).

        Called once on clean shutdown.  No-op by default.
        """
        return

    def clear(self) -> None:
        """Clear the display's contents (driver-specific).  No-op by default.

        Drivers that support an efficient hardware clear (e.g., SSD1306's
        ``fill(0); show()``) should override this to implement a proper blank
        screen on exit.
        """
        return

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def is_landscape(self) -> bool:
        """``True`` when the display width exceeds its height."""
        w, h = self.dimensions
        return w > h
