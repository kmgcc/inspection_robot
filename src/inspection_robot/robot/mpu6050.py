from __future__ import annotations

import importlib
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from types import ModuleType
from typing import Protocol

from .sensors import RobotHardwareError


DEFAULT_I2C_BUS = int(os.environ.get("MPU6050_I2C_BUS", "1"))
DEFAULT_ADDRESS = int(os.environ.get("MPU6050_ADDRESS", "0x68"), 0)
PWR_MGMT_1 = 0x6B
ACCEL_CONFIG = 0x1C
ACCEL_XOUT_H = 0x3B
TEMP_OUT_H = 0x41
GYRO_XOUT_H = 0x43
GYRO_CONFIG = 0x1B
GYRO_ZOUT_H = 0x47
WHO_AM_I = 0x75
GRAVITY_MPS2 = 9.80665
ACCEL_2G_SCALE = 16384.0
GYRO_250DPS_SCALE = 131.0
_GYRO_BIAS_LOCK = threading.Lock()
_GYRO_BIAS_DPS: dict[str, float] | None = None


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
    max_correction_attempts: int = 5
    min_pulse_seconds: float = 0.04
    max_pulse_seconds: float = 0.45
    settle_seconds: float = 0.05
    correction_gain: float = 0.9
    min_measured_degrees: float = 0.5


@dataclass(slots=True)
class MPU6050Gyro:
    bus: I2CBus
    address: int = DEFAULT_ADDRESS

    def initialize(self) -> None:
        self.bus.write_byte_data(self.address, PWR_MGMT_1, 0x03)
        self.bus.write_byte_data(self.address, ACCEL_CONFIG, 0x00)
        self.bus.write_byte_data(self.address, GYRO_CONFIG, 0x00)

    def who_am_i(self) -> int:
        return self.bus.read_byte_data(self.address, WHO_AM_I)

    def read_gyro_z_dps(self) -> float:
        return self._read_signed_register_pair(GYRO_ZOUT_H) / GYRO_250DPS_SCALE

    def read_accel_mps2(self) -> dict[str, float]:
        return {
            "x": self._read_signed_register_pair(ACCEL_XOUT_H) / ACCEL_2G_SCALE * GRAVITY_MPS2,
            "y": self._read_signed_register_pair(ACCEL_XOUT_H + 2) / ACCEL_2G_SCALE * GRAVITY_MPS2,
            "z": self._read_signed_register_pair(ACCEL_XOUT_H + 4) / ACCEL_2G_SCALE * GRAVITY_MPS2,
        }

    def read_gyro_dps(self) -> dict[str, float]:
        return {
            "x": self._read_signed_register_pair(GYRO_XOUT_H) / GYRO_250DPS_SCALE,
            "y": self._read_signed_register_pair(GYRO_XOUT_H + 2) / GYRO_250DPS_SCALE,
            "z": self._read_signed_register_pair(GYRO_XOUT_H + 4) / GYRO_250DPS_SCALE,
        }

    def read_temperature_c(self) -> float:
        return self._read_signed_register_pair(TEMP_OUT_H) / 340.0 + 36.53

    def calibrate_z_bias(self, *, samples: int, sample_seconds: float) -> float:
        return self.calibrate_gyro_bias(samples=samples, sample_seconds=sample_seconds)["z"]

    def calibrate_gyro_bias(self, *, samples: int, sample_seconds: float) -> dict[str, float]:
        count = max(1, int(samples))
        total = {"x": 0.0, "y": 0.0, "z": 0.0}
        for _ in range(count):
            gyro = self.read_gyro_dps()
            for axis in total:
                total[axis] += gyro[axis]
            if sample_seconds > 0:
                time.sleep(sample_seconds)
        return {axis: total[axis] / count for axis in total}

    def _read_signed_register_pair(self, high_register: int) -> int:
        high = self.bus.read_byte_data(self.address, high_register)
        low = self.bus.read_byte_data(self.address, high_register + 1)
        return _signed_word(high, low)


def turn_90(direction: str, motion_adapter: MotionAdapter, speed: int, fallback_seconds: float) -> bool | None:
    if os.environ.get("MPU6050_TURN_ENABLED", "1").strip().lower() in {"0", "false", "no", "off"}:
        return None
    config = Turn90Config(
        speed=speed,
        fallback_seconds=fallback_seconds,
        target_degrees=float(os.environ.get("MPU6050_TURN_TARGET_DEGREES", "90.0")),
        tolerance_degrees=float(os.environ.get("MPU6050_TURN_TOLERANCE_DEGREES", "3.0")),
        sample_seconds=float(os.environ.get("MPU6050_TURN_SAMPLE_SECONDS", "0.01")),
        bias_samples=int(os.environ.get("MPU6050_TURN_BIAS_SAMPLES", "20")),
        max_correction_attempts=int(os.environ.get("MPU6050_TURN_MAX_CORRECTIONS", "5")),
        min_pulse_seconds=float(os.environ.get("MPU6050_TURN_MIN_PULSE_SECONDS", "0.04")),
        max_pulse_seconds=float(os.environ.get("MPU6050_TURN_MAX_PULSE_SECONDS", "0.45")),
        settle_seconds=float(os.environ.get("MPU6050_TURN_SETTLE_SECONDS", "0.05")),
        correction_gain=float(os.environ.get("MPU6050_TURN_CORRECTION_GAIN", "0.9")),
        min_measured_degrees=float(os.environ.get("MPU6050_TURN_MIN_MEASURED_DEGREES", "0.5")),
    )
    try:
        gyro = open_default_gyro()
        return turn_90_with_gyro(direction, motion_adapter, gyro, config)
    except (ImportError, OSError, AttributeError, RobotHardwareError):
        return None


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
    target = max(1.0, float(config.target_degrees))
    tolerance = max(0.0, float(config.tolerance_degrees))
    accumulated = 0.0
    rate_hint: float | None = None
    max_attempts = max(0, int(config.max_correction_attempts)) + 1

    for attempt in range(max_attempts):
        error = target - accumulated
        if abs(error) <= tolerance:
            return True
        pulse_direction = normalized if error > 0 else _opposite_direction(normalized)
        if attempt == 0:
            pulse_seconds = max(0.0, float(config.fallback_seconds))
        else:
            pulse_seconds = _correction_duration(abs(error), target, rate_hint, config)
        measured_degrees = _measure_turn_pulse(
            pulse_direction,
            motion_adapter,
            gyro,
            bias,
            config,
            pulse_seconds,
        )
        if pulse_seconds > 0 and measured_degrees > 0:
            rate_hint = measured_degrees / pulse_seconds
        accumulated += measured_degrees if pulse_direction == normalized else -measured_degrees
        if config.settle_seconds > 0:
            time.sleep(config.settle_seconds)

    if abs(target - accumulated) <= tolerance:
        return True
    return False


def open_default_gyro() -> MPU6050Gyro:
    return MPU6050Gyro(bus=_open_bus(DEFAULT_I2C_BUS), address=DEFAULT_ADDRESS)


def read_motion_sample() -> dict[str, object]:
    try:
        gyro = open_default_gyro()
        gyro.initialize()
        identity = gyro.who_am_i()
        if identity not in {0x68, 0x69, 0x34}:
            raise RobotHardwareError(f"unexpected MPU6050 WHO_AM_I value: 0x{identity:02x}")
        bias = _cached_gyro_bias(gyro)
        raw_gyro = gyro.read_gyro_dps()
        corrected_gyro = {axis: raw_gyro[axis] - bias[axis] for axis in raw_gyro}
        return {
            "ok": True,
            "source": "mpu6050",
            "accel_mps2": _rounded_vector(gyro.read_accel_mps2()),
            "gyro_dps": _rounded_vector(corrected_gyro),
            "gyro_raw_dps": _rounded_vector(raw_gyro),
            "gyro_bias_dps": _rounded_vector(bias),
            "temperature_c": round(gyro.read_temperature_c(), 2),
            "zero_drift_compensated": True,
            "sample_time": datetime.now().isoformat(timespec="seconds"),
            "last_error": None,
        }
    except (ImportError, OSError, AttributeError, TypeError, ValueError, RobotHardwareError) as exc:
        return {
            "ok": False,
            "source": "mpu6050",
            "accel_mps2": {"x": None, "y": None, "z": None},
            "gyro_dps": {"x": None, "y": None, "z": None},
            "gyro_raw_dps": {"x": None, "y": None, "z": None},
            "gyro_bias_dps": {"x": None, "y": None, "z": None},
            "temperature_c": None,
            "zero_drift_compensated": False,
            "sample_time": datetime.now().isoformat(timespec="seconds"),
            "last_error": str(exc),
        }


def reset_gyro_bias_cache() -> None:
    global _GYRO_BIAS_DPS
    with _GYRO_BIAS_LOCK:
        _GYRO_BIAS_DPS = None


def _measure_turn_pulse(
    direction: str,
    motion_adapter: MotionAdapter,
    gyro: MPU6050Gyro,
    bias: float,
    config: Turn90Config,
    pulse_seconds: float,
) -> float:
    duration = max(0.0, float(pulse_seconds))
    if duration <= 0:
        return 0.0

    if config.sample_seconds <= 0:
        rate = abs(gyro.read_gyro_z_dps() - bias)
        try:
            _run_turn_motion(direction, motion_adapter, config.speed, duration)
        finally:
            motion_adapter.stop()
        return rate * duration

    stop_sampling = threading.Event()
    errors: list[BaseException] = []
    accumulated = 0.0

    def sample_loop() -> None:
        nonlocal accumulated
        previous = time.monotonic()
        try:
            while not stop_sampling.is_set():
                time.sleep(config.sample_seconds)
                now = time.monotonic()
                elapsed = max(0.0, now - previous)
                previous = now
                rate = gyro.read_gyro_z_dps() - bias
                accumulated += abs(rate) * elapsed
        except BaseException as exc:  # pragma: no cover - defensive against I2C thread failures
            errors.append(exc)
            stop_sampling.set()

    thread = threading.Thread(target=sample_loop, daemon=True)
    thread.start()
    try:
        _run_turn_motion(direction, motion_adapter, config.speed, duration)
    finally:
        stop_sampling.set()
        thread.join(timeout=max(config.sample_seconds * 4.0, 0.2))
        motion_adapter.stop()
    if errors:
        raise RobotHardwareError(f"MPU6050 gyro read failed during turn: {errors[0]}")
    return accumulated


def _run_turn_motion(direction: str, motion_adapter: MotionAdapter, speed: int, duration_seconds: float) -> None:
    if direction == "left":
        motion_adapter.rotate_left_slow(speed=speed, duration_seconds=duration_seconds)
    elif direction == "right":
        motion_adapter.rotate_right_slow(speed=speed, duration_seconds=duration_seconds)
    else:
        raise RobotHardwareError(f"unsupported MPU6050 pulse direction: {direction}")


def _correction_duration(
    error_degrees: float,
    target_degrees: float,
    rate_hint_dps: float | None,
    config: Turn90Config,
) -> float:
    if rate_hint_dps is not None and rate_hint_dps > 1.0:
        raw = error_degrees / rate_hint_dps
    else:
        raw = config.fallback_seconds * (error_degrees / max(1.0, target_degrees))
    raw *= max(0.1, float(config.correction_gain))
    return min(max(raw, config.min_pulse_seconds), config.max_pulse_seconds)


def _opposite_direction(direction: str) -> str:
    return "left" if direction == "right" else "right"


def _open_bus(bus_id: int) -> I2CBus:
    module = _load_smbus_module()
    bus_class = getattr(module, "SMBus")
    return bus_class(bus_id)


def _load_smbus_module() -> ModuleType:
    try:
        return importlib.import_module("smbus2")
    except ImportError:
        return importlib.import_module("smbus")


def _cached_gyro_bias(gyro: MPU6050Gyro) -> dict[str, float]:
    global _GYRO_BIAS_DPS
    with _GYRO_BIAS_LOCK:
        if _GYRO_BIAS_DPS is None:
            _GYRO_BIAS_DPS = gyro.calibrate_gyro_bias(
                samples=int(os.environ.get("MPU6050_STATUS_BIAS_SAMPLES", "20")),
                sample_seconds=float(os.environ.get("MPU6050_STATUS_BIAS_SAMPLE_SECONDS", "0.01")),
            )
        return dict(_GYRO_BIAS_DPS)


def _rounded_vector(values: dict[str, float]) -> dict[str, float]:
    return {axis: round(float(value), 3) for axis, value in values.items()}


def _signed_word(high: int, low: int) -> int:
    value = ((int(high) & 0xFF) << 8) | (int(low) & 0xFF)
    if value & 0x8000:
        return value - 0x10000
    return value
