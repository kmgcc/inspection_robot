from __future__ import annotations

import importlib
import os
import time
from dataclasses import dataclass
from types import ModuleType
from typing import Protocol

from .sensors import RobotHardwareError


DEFAULT_I2C_BUS = int(os.environ.get("MPU6050_I2C_BUS", "1"))
DEFAULT_ADDRESS = int(os.environ.get("MPU6050_ADDRESS", "0x68"), 0)
PWR_MGMT_1 = 0x6B
GYRO_CONFIG = 0x1B
GYRO_ZOUT_H = 0x47
WHO_AM_I = 0x75
GYRO_250DPS_SCALE = 131.0


class I2CBus(Protocol):
    def write_byte_data(self, address: int, register: int, value: int) -> None: ...

    def read_byte_data(self, address: int, register: int) -> int: ...


class MotionAdapter(Protocol):
    def rotate_left_slow(self, *, speed: int, duration_seconds: float) -> None: ...

    def rotate_right_slow(self, *, speed: int, duration_seconds: float) -> None: ...

    def stop(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Turn90Config:
    speed: int
    fallback_seconds: float
    target_degrees: float = 90.0
    tolerance_degrees: float = 3.0
    sample_seconds: float = 0.01
    bias_samples: int = 20


@dataclass(slots=True)
class MPU6050Gyro:
    bus: I2CBus
    address: int = DEFAULT_ADDRESS

    def initialize(self) -> None:
        self.bus.write_byte_data(self.address, PWR_MGMT_1, 0x03)
        self.bus.write_byte_data(self.address, GYRO_CONFIG, 0x00)

    def who_am_i(self) -> int:
        return self.bus.read_byte_data(self.address, WHO_AM_I)

    def read_gyro_z_dps(self) -> float:
        high = self.bus.read_byte_data(self.address, GYRO_ZOUT_H)
        low = self.bus.read_byte_data(self.address, GYRO_ZOUT_H + 1)
        raw = _signed_word(high, low)
        return raw / GYRO_250DPS_SCALE

    def calibrate_z_bias(self, *, samples: int, sample_seconds: float) -> float:
        count = max(1, int(samples))
        total = 0.0
        for _ in range(count):
            total += self.read_gyro_z_dps()
            if sample_seconds > 0:
                time.sleep(sample_seconds)
        return total / count


def turn_90(direction: str, motion_adapter: MotionAdapter, speed: int, fallback_seconds: float) -> bool:
    if os.environ.get("MPU6050_TURN_ENABLED", "1").strip().lower() in {"0", "false", "no", "off"}:
        return False
    config = Turn90Config(speed=speed, fallback_seconds=fallback_seconds)
    try:
        gyro = open_default_gyro()
        return turn_90_with_gyro(direction, motion_adapter, gyro, config)
    except (ImportError, OSError, AttributeError, RobotHardwareError):
        return False


def turn_90_with_gyro(
    direction: str,
    motion_adapter: MotionAdapter,
    gyro: MPU6050Gyro,
    config: Turn90Config,
) -> bool:
    normalized = direction.strip().lower()
    if normalized not in {"left", "right"}:
        raise RobotHardwareError(f"unsupported MPU6050 turn direction: {direction}")
    gyro.initialize()
    identity = gyro.who_am_i()
    if identity not in {0x68, 0x69, 0x34}:
        raise RobotHardwareError(f"unexpected MPU6050 WHO_AM_I value: 0x{identity:02x}")
    bias = gyro.calibrate_z_bias(samples=config.bias_samples, sample_seconds=config.sample_seconds)
    target = max(1.0, config.target_degrees - config.tolerance_degrees)
    timeout = max(config.fallback_seconds * 2.0, config.fallback_seconds + 0.2)
    accumulated = 0.0
    started_at = time.monotonic()
    previous = started_at
    if normalized == "left":
        motion_adapter.rotate_left_slow(speed=config.speed, duration_seconds=0.0)
    else:
        motion_adapter.rotate_right_slow(speed=config.speed, duration_seconds=0.0)
    try:
        while time.monotonic() - started_at <= timeout:
            if config.sample_seconds > 0:
                time.sleep(config.sample_seconds)
            now = time.monotonic()
            elapsed = max(0.0, now - previous)
            previous = now
            rate = gyro.read_gyro_z_dps() - bias
            accumulated += abs(rate) * elapsed
            if accumulated >= target:
                return True
        return False
    finally:
        motion_adapter.stop()


def open_default_gyro() -> MPU6050Gyro:
    return MPU6050Gyro(bus=_open_bus(DEFAULT_I2C_BUS), address=DEFAULT_ADDRESS)


def _open_bus(bus_id: int) -> I2CBus:
    module = _load_smbus_module()
    bus_class = getattr(module, "SMBus")
    return bus_class(bus_id)


def _load_smbus_module() -> ModuleType:
    try:
        return importlib.import_module("smbus2")
    except ImportError:
        return importlib.import_module("smbus")


def _signed_word(high: int, low: int) -> int:
    value = ((int(high) & 0xFF) << 8) | (int(low) & 0xFF)
    if value & 0x8000:
        return value - 0x10000
    return value
