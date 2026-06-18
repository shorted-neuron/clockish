"""
clockish.drivers
~~~~~~~~~~~~~~~~
Display hardware abstraction layer.

Usage
-----
    from clockish.drivers import load_driver

    lcd = load_driver(config["display"]).begin()
    lcd.display(pil_image)
    lcd.close()

Adding a new driver
-------------------
1. Create ``src/clockish/drivers/mydriver.py`` with a class that inherits
   :class:`~clockish.drivers.base.DisplayDriver` and implements the required
   abstract methods.
2. Add an entry to :func:`load_driver` below.
3. Users select it with ``driver: mydriver`` in the ``display:`` YAML block.

Available drivers
-----------------
* ``ili9486``      — ILI9486-based SPI TFT (MPI3501 / MHS3528, Raspberry Pi)
* ``st7789``       — ST7789-based SPI TFT (Pimoroni library; Adafruit 240×135, 240×240, …)
* ``framebuffer``  — Linux framebuffer /dev/fb0 (DSI ribbon-cable displays, HDMI)
"""

from __future__ import annotations

from clockish.drivers.base import DisplayDriver

# Registry: lower-case driver name → dotted module + class name
# To add a new driver: create src/clockish/drivers/mydriver.py, subclass
# DisplayDriver, then add one line here.
_DRIVER_REGISTRY: dict[str, tuple[str, str]] = {
    "ili9486":     ("clockish.drivers.ili9486",     "ILI9486Driver"),
    "st7789":      ("clockish.drivers.st7789",      "ST7789Driver"),
    "framebuffer": ("clockish.drivers.framebuffer", "FramebufferDriver"),
}


def load_driver(display_cfg: dict) -> DisplayDriver:
    """Instantiate the requested display driver from the ``display:`` config dict.

    The ``driver`` key selects the backend (default: ``"ili9486"``).  All other
    keys in *display_cfg* are passed as-is to the driver constructor and may be
    used for hardware-specific settings (pins, SPI bus, SKU, etc.).

    Parameters
    ----------
    display_cfg:
        The ``display:`` section of the YAML config (already loaded as a dict).

    Returns
    -------
    DisplayDriver
        An *uninitialised* driver instance.  Call ``.begin()`` on it to open
        the hardware connection.

    Raises
    ------
    ValueError
        If the requested driver name is not in the registry.
    """
    driver_name = str(display_cfg.get("driver", "ili9486")).lower()

    entry = _DRIVER_REGISTRY.get(driver_name)
    if entry is None:
        available = ", ".join(sorted(_DRIVER_REGISTRY))
        raise ValueError(
            f"Unknown display driver '{driver_name}'. "
            f"Available drivers: {available}"
        )

    module_name, class_name = entry
    # Lazy import so other drivers don't impose import overhead / hard deps.
    import importlib
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    return cls(display_cfg)


__all__ = ["DisplayDriver", "load_driver"]

