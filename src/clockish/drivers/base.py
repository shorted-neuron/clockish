"""
clockish.drivers.base
~~~~~~~~~~~~~~~~~~~~~
Abstract base class for display hardware drivers.

All display backends must subclass :class:`DisplayDriver` and implement the
abstract methods.  Optional lifecycle hooks (``idle``, ``close``) have
no-op defaults so minimal drivers need only implement ``begin`` and
``display``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from PIL import Image


class DisplayDriver(ABC):
    """Abstract interface that every display backend must satisfy.

    Life-cycle
    ----------
    1. Instantiate with the ``display`` config dict:
           driver = MyDriver(display_cfg)
    2. Call :meth:`begin`  --  opens SPI/I2C/USB, sets up GPIO, returns *self*:
           driver = driver.begin()
    3. Call :meth:`display` once per frame to push a PIL image.
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

    def idle(self, state: bool = True) -> None:
        """Enter (``True``) or exit (``False``) low-power idle mode.

        No-op by default; override if the hardware supports it.
        """

    def close(self) -> None:
        """Release hardware resources (SPI bus, GPIO lines, file handles, ...).

        Called once on clean shutdown.  No-op by default.
        """

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def is_landscape(self) -> bool:
        """``True`` when the display width exceeds its height."""
        w, h = self.dimensions
        return w > h

