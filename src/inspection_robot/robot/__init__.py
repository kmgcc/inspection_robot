"""Hardware adapters for the RASPBOT V2 car.

The package is intentionally import-safe on a normal development computer.
Adapters import the vendor hardware libraries only inside the functions that
actually touch motors, I2C, buzzer, or RGB LEDs.
"""

from .sensors import RobotHardwareError

__all__ = ["RobotHardwareError"]
