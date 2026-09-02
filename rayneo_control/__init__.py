"""rayneo_control — a reverse-engineered runtime control library for the
RayNeo Air 4 Pro (codename ``taurus4p0``) AR glasses.

Everything here talks to the glasses' STM32 over its existing USB-HID
control endpoint. Nothing in this package writes firmware, touches DFU,
or persists anything outside the device's own NVM settings blob (which
the glasses themselves write when you change display mode — same as the
official app would). See README.md for the full protocol writeup and an
honest confidence rating for every command.
"""

from .device import RayNeoDevice, RayNeoNotFound
from .commands import DisplayMode, Command

__all__ = ["RayNeoDevice", "RayNeoNotFound", "DisplayMode", "Command"]
__version__ = "0.1.0"
