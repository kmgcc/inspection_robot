from __future__ import annotations

import importlib
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from math import atan2, degrees, sqrt
from types import ModuleType
from typing import Callable, Protocol

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
_LAST_TURN_LOCK = threading.Lock()
_LAST_TURN_RESULT: dict[str, object] | None = None
_ORIENTATION_LOCK = threading.Lock()
_YAW_DEGREES = 0.0
_LAST_YAW_SAMPLE_MONOTONIC: float | None = None


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
    tolerance_degrees: float = 2.0
    sample_seconds: float = 0.01
    bias_samples: int = 20
    max_correction_attempts: int = 7
    min_pulse_seconds: float = 0.025
    max_pulse_seconds: float = 0.25
    settle_seconds: float = 0.25
    correction_gain: float = 0.55
    min_measured_degrees: float = 0.5
    correction_speed: int | None = None
    turn_axis: str = "auto"
    post_stop_sample_seconds: float = 0.18


@dataclass(frozen=True, slots=True)
class TurnPulse:
    attempt: int
    direction: str
    axis: str
    speed: int
    duration_seconds: float
    measured_degrees: float
    accumulated_degrees: float
    error_degrees: float
    axis_degrees: dict[str, float]


@dataclass(frozen=True, slots=True)
class TurnPulseMeasurement:
    measured_degrees: float
    axis: str
    axis_degrees: dict[str, float]


@dataclass(frozen=True, slots=True)
class Turn90Result:
    ok: bool
    source: str
    direction: str
    turn_axis: str
    target_degrees: float
    tolerance_degrees: float
    final_degrees: float
    error_degrees: float
    attempts: int
    pulses: tuple[TurnPulse, ...]
    message: str
    sample_time: str

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "source": self.source,
            "direction": self.direction,
            "turn_axis": self.turn_axis,
            "target_degrees": round(self.target_degrees, 3),
            "tolerance_degrees": round(self.tolerance_degrees, 3),
            "final_degrees": round(self.final_degrees, 3),
            "error_degrees": round(self.error_degrees, 3),
            "attempts": self.attempts,
            "pulses": [
                {
                    "attempt": pulse.attempt,
                    "direction": pulse.direction,
                    "axis": pulse.axis,
                    "speed": pulse.speed,
                    "duration_seconds": round(pulse.duration_seconds, 3),
                    "measured_degrees": round(pulse.measured_degrees, 3),
                    "accumulated_degrees": round(pulse.accumulated_degrees, 3),
                    "error_degrees": round(pulse.error_degrees, 3),
                    "axis_degrees": dict(pulse.axis_degrees),
                }
                for pulse in self.pulses
            ],
            "message": self.message,
            "sample_time": self.sample_time,
        }


@dataclass(slots=True)
class StraightHeadingGuard:
    gyro: "MPU6050Gyro"
    bias_dps: dict[str, float]
    yaw_axis: str
    yaw_sign: float
    deadband_dps: float
    leak_per_second: float = 0.0
    heading_degrees: float = 0.0
    last_sample_at: float | None = None
    last_rate_dps: float = 0.0

    def reset(self) -> None:
        self.heading_degrees = 0.0
        self.last_sample_at = None
        self.last_rate_dps = 0.0

    def update(self) -> float:
        now = time.monotonic()
        rates = _read_corrected_gyro_dps(self.gyro, self.bias_dps)
        rate = float(rates.get(self.yaw_axis, 0.0)) * self.yaw_sign
        if abs(rate) < self.deadband_dps:
            rate = 0.0
        self.last_rate_dps = rate
        if self.last_sample_at is None:
            self.last_sample_at = now
            return self.heading_degrees
        elapsed = max(0.0, min(now - self.last_sample_at, 0.25))
        self.last_sample_at = now
        # Leaky integrator: a residual gyro bias (zero-drift) would otherwise ramp
        # the estimated heading forever and make the controller chase a phantom
        # error (越纠越弯). The leak pulls the estimate back toward zero so a
        # constant bias only produces a small, bounded offset instead of a ramp.
        leak = max(0.0, float(self.leak_per_second)) * elapsed
        decay = max(0.0, 1.0 - leak)
        self.heading_degrees = _wrap_degrees(self.heading_degrees * decay + rate * elapsed)
        return self.heading_degrees

    def recalibrate_bias(self, *, samples: int = 15, sample_seconds: float = 0.005) -> bool:
        """Zero-velocity update: re-estimate gyro bias while the car is stopped.

        Call this only when the robot is physically stationary (e.g. parked at a
        boundary/obstacle). Any yaw rate measured then is bias, so we fold it into
        the running bias estimate. Rejects the update when the readings are too
        noisy/large to be a genuine at-rest sample, which guards against silently
        learning a bad bias if the car is actually moving.
        """

        candidate = _stable_gyro_bias_candidate(
            self.gyro,
            samples=samples,
            sample_seconds=sample_seconds,
        )
        if candidate is None:
            return False
        alpha = _zupt_alpha()
        self.bias_dps = {
            axis: (1.0 - alpha) * float(self.bias_dps.get(axis, 0.0)) + alpha * float(candidate.get(axis, 0.0))
            for axis in ("x", "y", "z")
        }
        self.reset()
        return True


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


def turn_90(
    direction: str,
    motion_adapter: MotionAdapter,
    speed: int,
    fallback_seconds: float,
    *,
    should_abort: Callable[[], bool] | None = None,
) -> bool | None:
    result = turn_90_with_result(direction, motion_adapter, speed, fallback_seconds, should_abort=should_abort)
    if result is None:
        return None
    return bool(result.get("ok"))


def turn_90_with_result(
    direction: str,
    motion_adapter: MotionAdapter,
    speed: int,
    fallback_seconds: float,
    *,
    should_abort: Callable[[], bool] | None = None,
) -> dict[str, object] | None:
    if _abort_requested(should_abort):
        payload = _aborted_turn_result(direction)
        _set_last_turn_result(payload)
        return payload
    if os.environ.get("MPU6050_TURN_ENABLED", "1").strip().lower() in {"0", "false", "no", "off"}:
        _set_last_turn_result(_unavailable_turn_result(direction, "MPU6050 closed-loop turn disabled by MPU6050_TURN_ENABLED."))
        return None
    config = Turn90Config(
        speed=speed,
        fallback_seconds=fallback_seconds,
        target_degrees=float(os.environ.get("MPU6050_TURN_TARGET_DEGREES", "90.0")),
        tolerance_degrees=float(os.environ.get("MPU6050_TURN_TOLERANCE_DEGREES", "2.0")),
        sample_seconds=float(os.environ.get("MPU6050_TURN_SAMPLE_SECONDS", "0.01")),
        bias_samples=int(os.environ.get("MPU6050_TURN_BIAS_SAMPLES", "20")),
        max_correction_attempts=int(os.environ.get("MPU6050_TURN_MAX_CORRECTIONS", "9")),
        min_pulse_seconds=float(os.environ.get("MPU6050_TURN_MIN_PULSE_SECONDS", "0.025")),
        max_pulse_seconds=float(os.environ.get("MPU6050_TURN_MAX_PULSE_SECONDS", "0.35")),
        settle_seconds=float(os.environ.get("MPU6050_TURN_SETTLE_SECONDS", "0.25")),
        correction_gain=float(os.environ.get("MPU6050_TURN_CORRECTION_GAIN", "0.8")),
        min_measured_degrees=float(os.environ.get("MPU6050_TURN_MIN_MEASURED_DEGREES", "0.5")),
        correction_speed=int(os.environ.get("MPU6050_TURN_CORRECTION_SPEED", str(max(8, int(speed) // 2)))),
        turn_axis=os.environ.get("MPU6050_TURN_AXIS", "auto"),
        post_stop_sample_seconds=float(os.environ.get("MPU6050_TURN_POST_STOP_SAMPLE_SECONDS", "0.18")),
    )
    try:
        gyro = open_default_gyro()
        result = turn_90_with_gyro(direction, motion_adapter, gyro, config, should_abort=should_abort)
        payload = result.to_dict()
        _set_last_turn_result(payload)
        return payload
    except (ImportError, OSError, AttributeError, RobotHardwareError) as exc:
        _set_last_turn_result(_unavailable_turn_result(direction, str(exc)))
        return None


def turn_90_with_gyro(
    direction: str,
    motion_adapter: MotionAdapter,
    gyro: MPU6050Gyro,
    config: Turn90Config,
    *,
    should_abort: Callable[[], bool] | None = None,
) -> Turn90Result:
    normalized = direction.strip().lower()
    if normalized not in {"left", "right"}:
        raise RobotHardwareError(f"unsupported MPU6050 turn direction: {direction}")
    if _abort_requested(should_abort):
        motion_adapter.stop()
        return _turn_result(
            False,
            normalized,
            _turn_axis_label(_configured_turn_axis(config.turn_axis), config.turn_axis),
            max(1.0, float(config.target_degrees)),
            max(0.0, float(config.tolerance_degrees)),
            0.0,
            [],
            message="MPU6050 turn aborted before motion.",
        )
    gyro.initialize()
    identity = gyro.who_am_i()
    if identity not in {0x68, 0x69, 0x34}:
        raise RobotHardwareError(f"unexpected MPU6050 WHO_AM_I value: 0x{identity:02x}")
    bias = _calibrate_turn_bias(gyro, config)
    target = max(1.0, float(config.target_degrees))
    tolerance = max(0.0, float(config.tolerance_degrees))
    accumulated = 0.0
    rate_hint: float | None = None
    max_attempts = max(0, int(config.max_correction_attempts)) + 1
    pulses: list[TurnPulse] = []
    turn_axis = _configured_turn_axis(config.turn_axis)

    for attempt in range(max_attempts):
        if _abort_requested(should_abort):
            motion_adapter.stop()
            return _turn_result(False, normalized, _turn_axis_label(turn_axis, config.turn_axis), target, tolerance, accumulated, pulses, message="MPU6050 turn aborted.")
        error = target - accumulated
        if abs(error) <= tolerance:
            return _turn_result(True, normalized, _turn_axis_label(turn_axis, config.turn_axis), target, tolerance, accumulated, pulses)
        pulse_direction = normalized if error > 0 else _opposite_direction(normalized)
        if attempt == 0:
            pulse_seconds = max(0.0, float(config.fallback_seconds))
            pulse_speed = config.speed
        else:
            pulse_seconds = _correction_duration(abs(error), target, rate_hint, config)
            pulse_speed = _correction_speed(config)
        measurement = _measure_turn_pulse(
            pulse_direction,
            motion_adapter,
            gyro,
            bias,
            config,
            pulse_seconds,
            pulse_speed,
            turn_axis,
            should_abort=should_abort,
        )
        if turn_axis is None and measurement.axis in {"x", "y", "z"}:
            turn_axis = measurement.axis
        measured_degrees = measurement.measured_degrees
        if pulse_seconds > 0 and measured_degrees > 0:
            rate_hint = measured_degrees / pulse_seconds
        accumulated += measured_degrees if pulse_direction == normalized else -measured_degrees
        pulses.append(
            TurnPulse(
                attempt=attempt + 1,
                direction=pulse_direction,
                axis=measurement.axis,
                speed=pulse_speed,
                duration_seconds=pulse_seconds,
                measured_degrees=measured_degrees,
                accumulated_degrees=accumulated,
                error_degrees=target - accumulated,
                axis_degrees=measurement.axis_degrees,
            )
        )
        if config.settle_seconds > 0 and not _sleep_until_abort(config.settle_seconds, should_abort):
            motion_adapter.stop()
            return _turn_result(False, normalized, _turn_axis_label(turn_axis, config.turn_axis), target, tolerance, accumulated, pulses, message="MPU6050 turn aborted during settle.")

    return _turn_result(
        abs(target - accumulated) <= tolerance,
        normalized,
        _turn_axis_label(turn_axis, config.turn_axis),
        target,
        tolerance,
        accumulated,
        pulses,
    )


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
        accel = gyro.read_accel_mps2()
        orientation = _orientation_from_sample(accel, corrected_gyro, time.monotonic())
        return {
            "ok": True,
            "source": "mpu6050",
            "accel_mps2": _rounded_vector(accel),
            "gyro_dps": _rounded_vector(corrected_gyro),
            "gyro_raw_dps": _rounded_vector(raw_gyro),
            "gyro_bias_dps": _rounded_vector(bias),
            "orientation_deg": orientation,
            "yaw_axis": _yaw_axis(),
            "turn_axis": os.environ.get("MPU6050_TURN_AXIS", "auto").strip().lower() or "auto",
            "temperature_c": round(gyro.read_temperature_c(), 2),
            "zero_drift_compensated": True,
            "sample_time": datetime.now().isoformat(timespec="seconds"),
            "last_turn": _last_turn_result(),
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
            "orientation_deg": {"roll": None, "pitch": None, "yaw": None},
            "yaw_axis": _yaw_axis(),
            "turn_axis": os.environ.get("MPU6050_TURN_AXIS", "auto").strip().lower() or "auto",
            "temperature_c": None,
            "zero_drift_compensated": False,
            "sample_time": datetime.now().isoformat(timespec="seconds"),
            "last_turn": _last_turn_result(),
            "last_error": str(exc),
        }


def open_straight_heading_guard() -> StraightHeadingGuard | None:
    try:
        gyro = open_default_gyro()
        gyro.initialize()
        identity = gyro.who_am_i()
        if identity not in {0x68, 0x69, 0x34}:
            raise RobotHardwareError(f"unexpected MPU6050 WHO_AM_I value: 0x{identity:02x}")
        return StraightHeadingGuard(
            gyro=gyro,
            bias_dps=_cached_gyro_bias(gyro),
            yaw_axis=_yaw_axis(),
            yaw_sign=_yaw_sign(),
            deadband_dps=_yaw_deadband_dps(),
            leak_per_second=_yaw_leak_per_second(),
        )
    except (ImportError, OSError, AttributeError, TypeError, ValueError, RobotHardwareError):
        return None


def reset_gyro_bias_cache() -> None:
    global _GYRO_BIAS_DPS
    with _GYRO_BIAS_LOCK:
        _GYRO_BIAS_DPS = None
    _reset_orientation_state()


def _measure_turn_pulse(
    direction: str,
    motion_adapter: MotionAdapter,
    gyro: MPU6050Gyro,
    bias: dict[str, float],
    config: Turn90Config,
    pulse_seconds: float,
    speed: int,
    axis_hint: str | None,
    *,
    should_abort: Callable[[], bool] | None = None,
) -> TurnPulseMeasurement:
    duration = max(0.0, float(pulse_seconds))
    if duration <= 0:
        return TurnPulseMeasurement(0.0, _turn_axis_label(axis_hint, config.turn_axis), {"x": 0.0, "y": 0.0, "z": 0.0})
    if _abort_requested(should_abort):
        motion_adapter.stop()
        return TurnPulseMeasurement(0.0, _turn_axis_label(axis_hint, config.turn_axis), {"x": 0.0, "y": 0.0, "z": 0.0})

    if config.sample_seconds <= 0:
        rates = _read_corrected_gyro_dps(gyro, bias)
        axis_degrees = {axis: rate * duration for axis, rate in rates.items()}
        try:
            if not _abort_requested(should_abort):
                _run_turn_motion(direction, motion_adapter, speed, duration)
        finally:
            motion_adapter.stop()
        return _turn_measurement_from_axes(axis_degrees, config, axis_hint)

    stop_sampling = threading.Event()
    errors: list[BaseException] = []
    accumulated_axes = {"x": 0.0, "y": 0.0, "z": 0.0}

    def sample_loop() -> None:
        previous = time.monotonic()
        try:
            while not stop_sampling.is_set() and not _abort_requested(should_abort):
                if not _sleep_until_abort(config.sample_seconds, should_abort):
                    stop_sampling.set()
                    break
                now = time.monotonic()
                elapsed = max(0.0, now - previous)
                previous = now
                rates = _read_corrected_gyro_dps(gyro, bias)
                for axis in accumulated_axes:
                    accumulated_axes[axis] += rates[axis] * elapsed
        except BaseException as exc:  # pragma: no cover - defensive against I2C thread failures
            errors.append(exc)
            stop_sampling.set()

    thread = threading.Thread(target=sample_loop, daemon=True)
    thread.start()
    try:
        if not _abort_requested(should_abort):
            _run_turn_motion(direction, motion_adapter, speed, duration)
    finally:
        motion_adapter.stop()
        if config.post_stop_sample_seconds > 0:
            _sleep_until_abort(config.post_stop_sample_seconds, should_abort)
        stop_sampling.set()
        thread.join(timeout=max(config.sample_seconds * 4.0, 0.2))
    if errors:
        raise RobotHardwareError(f"MPU6050 gyro read failed during turn: {errors[0]}")
    return _turn_measurement_from_axes(accumulated_axes, config, axis_hint)


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
        speed_ratio = _correction_speed(config) / max(1.0, float(config.speed))
        effective_rate_hint = rate_hint_dps * max(0.1, speed_ratio)
        raw = error_degrees / effective_rate_hint
    else:
        raw = config.fallback_seconds * (error_degrees / max(1.0, target_degrees))
    raw *= max(0.1, float(config.correction_gain))
    return min(max(raw, config.min_pulse_seconds), config.max_pulse_seconds)


def _correction_speed(config: Turn90Config) -> int:
    if config.correction_speed is None:
        return max(8, min(100, int(config.speed) // 2))
    return max(1, min(100, int(config.correction_speed)))


def _turn_result(
    ok: bool,
    direction: str,
    turn_axis: str,
    target_degrees: float,
    tolerance_degrees: float,
    accumulated_degrees: float,
    pulses: list[TurnPulse],
    *,
    message: str | None = None,
) -> Turn90Result:
    error = target_degrees - accumulated_degrees
    attempts = len(pulses)
    result_message = message or (
        f"MPU6050 turn converged: {accumulated_degrees:.1f} deg, error={error:.1f} deg."
        if ok
        else f"MPU6050 turn failed to converge: {accumulated_degrees:.1f} deg, error={error:.1f} deg."
    )
    return Turn90Result(
        ok=ok,
        source="mpu6050",
        direction=direction,
        turn_axis=turn_axis,
        target_degrees=target_degrees,
        tolerance_degrees=tolerance_degrees,
        final_degrees=accumulated_degrees,
        error_degrees=error,
        attempts=attempts,
        pulses=tuple(pulses),
        message=result_message,
        sample_time=datetime.now().isoformat(timespec="seconds"),
    )


def _abort_requested(should_abort: Callable[[], bool] | None) -> bool:
    if should_abort is None:
        return False
    try:
        return bool(should_abort())
    except Exception:
        return False


def _sleep_until_abort(seconds: float, should_abort: Callable[[], bool] | None) -> bool:
    deadline = time.monotonic() + max(0.0, float(seconds))
    while True:
        if _abort_requested(should_abort):
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        time.sleep(min(remaining, 0.01))


def _opposite_direction(direction: str) -> str:
    return "left" if direction == "right" else "right"


def _calibrate_turn_bias(gyro: MPU6050Gyro, config: Turn90Config) -> dict[str, float]:
    calibrate_all = getattr(gyro, "calibrate_gyro_bias", None)
    if callable(calibrate_all):
        bias = calibrate_all(samples=config.bias_samples, sample_seconds=config.sample_seconds)
        return {axis: float(bias.get(axis, 0.0)) for axis in ("x", "y", "z")}
    return {
        "x": 0.0,
        "y": 0.0,
        "z": float(gyro.calibrate_z_bias(samples=config.bias_samples, sample_seconds=config.sample_seconds)),
    }


def _read_corrected_gyro_dps(gyro: MPU6050Gyro, bias: dict[str, float]) -> dict[str, float]:
    read_all = getattr(gyro, "read_gyro_dps", None)
    if callable(read_all):
        raw = read_all()
        return {axis: float(raw.get(axis, 0.0)) - float(bias.get(axis, 0.0)) for axis in ("x", "y", "z")}
    return {
        "x": 0.0,
        "y": 0.0,
        "z": float(gyro.read_gyro_z_dps()) - float(bias.get("z", 0.0)),
    }


def _turn_measurement_from_axes(
    axis_degrees: dict[str, float],
    config: Turn90Config,
    axis_hint: str | None,
) -> TurnPulseMeasurement:
    normalized = str(config.turn_axis).strip().lower()
    rounded_axes = {axis: round(float(axis_degrees.get(axis, 0.0)), 3) for axis in ("x", "y", "z")}
    if normalized == "vector":
        measured = sqrt(sum(float(axis_degrees.get(axis, 0.0)) ** 2 for axis in ("x", "y", "z")))
        return TurnPulseMeasurement(measured, "vector", rounded_axes)
    if normalized in {"x", "y", "z"}:
        axis = normalized
    elif axis_hint in {"x", "y", "z"}:
        axis = str(axis_hint)
    else:
        axis = max(("x", "y", "z"), key=lambda item: abs(float(axis_degrees.get(item, 0.0))))
    return TurnPulseMeasurement(abs(float(axis_degrees.get(axis, 0.0))), axis, rounded_axes)


def _configured_turn_axis(value: str) -> str | None:
    normalized = str(value).strip().lower()
    if normalized in {"x", "y", "z"}:
        return normalized
    return None


def _turn_axis_label(axis: str | None, configured: str) -> str:
    normalized = str(configured).strip().lower()
    if normalized == "vector":
        return "vector"
    if axis in {"x", "y", "z"}:
        return str(axis)
    return "auto"


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
            candidate = _stable_gyro_bias_candidate(
                gyro,
                samples=int(os.environ.get("MPU6050_STATUS_BIAS_SAMPLES", "20")),
                sample_seconds=float(os.environ.get("MPU6050_STATUS_BIAS_SAMPLE_SECONDS", "0.01")),
            )
            if candidate is not None:
                _GYRO_BIAS_DPS = candidate
        return dict(_GYRO_BIAS_DPS) if _GYRO_BIAS_DPS is not None else {"x": 0.0, "y": 0.0, "z": 0.0}


def _stable_gyro_bias_candidate(
    gyro: MPU6050Gyro,
    *,
    samples: int,
    sample_seconds: float,
) -> dict[str, float] | None:
    count = max(1, int(samples))
    readings: list[dict[str, float]] = []
    for _ in range(count):
        readings.append(gyro.read_gyro_dps())
        if sample_seconds > 0:
            time.sleep(sample_seconds)
    axes = ("x", "y", "z")
    average = {axis: sum(reading[axis] for reading in readings) / count for axis in axes}
    max_span = max(max(reading[axis] for reading in readings) - min(reading[axis] for reading in readings) for axis in axes)
    max_abs_average = max(abs(value) for value in average.values())
    if max_span > _status_bias_max_span_dps() or max_abs_average > _status_bias_max_abs_dps():
        return None
    return average


def _orientation_from_accel(accel: dict[str, float]) -> dict[str, float]:
    return _orientation_from_sample(accel, {"x": 0.0, "y": 0.0, "z": 0.0}, None)


def _orientation_from_sample(
    accel: dict[str, float],
    gyro_dps: dict[str, float],
    monotonic_time: float | None,
) -> dict[str, float]:
    ax = float(accel["x"])
    ay = float(accel["y"])
    az = float(accel["z"])
    roll = degrees(atan2(ay, az))
    pitch = degrees(atan2(-ax, sqrt(ay * ay + az * az)))
    yaw = _integrated_yaw_degrees(gyro_dps, monotonic_time)
    return {"roll": round(roll, 2), "pitch": round(pitch, 2), "yaw": round(yaw, 2)}


def _integrated_yaw_degrees(gyro_dps: dict[str, float], monotonic_time: float | None) -> float:
    global _LAST_YAW_SAMPLE_MONOTONIC, _YAW_DEGREES
    now = time.monotonic() if monotonic_time is None else monotonic_time
    axis = _yaw_axis()
    rate = float(gyro_dps.get(axis, 0.0)) * _yaw_sign()
    if abs(rate) < _yaw_deadband_dps():
        rate = 0.0
    with _ORIENTATION_LOCK:
        if _LAST_YAW_SAMPLE_MONOTONIC is None:
            _LAST_YAW_SAMPLE_MONOTONIC = now
            return _YAW_DEGREES
        elapsed = max(0.0, min(now - _LAST_YAW_SAMPLE_MONOTONIC, 2.0))
        _LAST_YAW_SAMPLE_MONOTONIC = now
        _YAW_DEGREES = _wrap_degrees(_YAW_DEGREES + rate * elapsed)
        return _YAW_DEGREES


def _set_last_turn_result(result: dict[str, object]) -> None:
    global _LAST_TURN_RESULT
    with _LAST_TURN_LOCK:
        _LAST_TURN_RESULT = dict(result)
    _sync_yaw_from_turn_result(result)


def _last_turn_result() -> dict[str, object] | None:
    with _LAST_TURN_LOCK:
        return dict(_LAST_TURN_RESULT) if _LAST_TURN_RESULT is not None else None


def _unavailable_turn_result(direction: str, message: str) -> dict[str, object]:
    return {
        "ok": False,
        "source": "mpu6050",
        "direction": direction,
        "turn_axis": os.environ.get("MPU6050_TURN_AXIS", "auto").strip().lower() or "auto",
        "target_degrees": 90.0,
        "tolerance_degrees": None,
        "final_degrees": None,
        "error_degrees": None,
        "attempts": 0,
        "pulses": [],
        "message": message,
        "sample_time": datetime.now().isoformat(timespec="seconds"),
    }


def _aborted_turn_result(direction: str) -> dict[str, object]:
    return {
        "ok": False,
        "source": "mpu6050",
        "direction": direction,
        "turn_axis": os.environ.get("MPU6050_TURN_AXIS", "auto").strip().lower() or "auto",
        "target_degrees": 90.0,
        "tolerance_degrees": None,
        "final_degrees": 0.0,
        "error_degrees": 90.0,
        "attempts": 0,
        "pulses": [],
        "message": "MPU6050 turn aborted.",
        "sample_time": datetime.now().isoformat(timespec="seconds"),
    }


def _sync_yaw_from_turn_result(result: dict[str, object]) -> None:
    global _LAST_YAW_SAMPLE_MONOTONIC, _YAW_DEGREES
    if not result.get("ok") or result.get("final_degrees") is None:
        return
    direction = str(result.get("direction") or "")
    sign = -1.0 if direction == "right" else 1.0
    with _ORIENTATION_LOCK:
        _YAW_DEGREES = _wrap_degrees(sign * float(result["final_degrees"]))
        _LAST_YAW_SAMPLE_MONOTONIC = time.monotonic()


def _reset_orientation_state() -> None:
    global _LAST_YAW_SAMPLE_MONOTONIC, _YAW_DEGREES
    with _ORIENTATION_LOCK:
        _YAW_DEGREES = 0.0
        _LAST_YAW_SAMPLE_MONOTONIC = None


def _yaw_axis() -> str:
    axis = os.environ.get("MPU6050_YAW_AXIS", "z").strip().lower()
    return axis if axis in {"x", "y", "z"} else "z"


def _yaw_sign() -> float:
    try:
        value = float(os.environ.get("MPU6050_YAW_SIGN", "-1"))
    except ValueError:
        return -1.0
    return -1.0 if value < 0 else 1.0


def _yaw_deadband_dps() -> float:
    try:
        return max(0.0, float(os.environ.get("MPU6050_YAW_DEADBAND_DPS", "0.25")))
    except ValueError:
        return 0.25


def _yaw_leak_per_second() -> float:
    """Leak rate (1/s) for the straight-heading integrator; 0 disables leaking."""
    try:
        return max(0.0, float(os.environ.get("MPU6050_YAW_LEAK_PER_SECOND", "0.08")))
    except ValueError:
        return 0.08


def _zupt_alpha() -> float:
    """EMA weight for zero-velocity bias updates; higher trusts new samples more."""
    try:
        return max(0.0, min(1.0, float(os.environ.get("MPU6050_ZUPT_ALPHA", "0.5"))))
    except ValueError:
        return 0.5


def _status_bias_max_span_dps() -> float:
    try:
        return max(0.0, float(os.environ.get("MPU6050_STATUS_BIAS_MAX_SPAN_DPS", "5.0")))
    except ValueError:
        return 5.0


def _status_bias_max_abs_dps() -> float:
    try:
        return max(0.0, float(os.environ.get("MPU6050_STATUS_BIAS_MAX_ABS_DPS", "25.0")))
    except ValueError:
        return 25.0


def _wrap_degrees(value: float) -> float:
    return ((float(value) + 180.0) % 360.0) - 180.0


def _rounded_vector(values: dict[str, float]) -> dict[str, float]:
    return {axis: round(float(value), 3) for axis, value in values.items()}


def _signed_word(high: int, low: int) -> int:
    value = ((int(high) & 0xFF) << 8) | (int(low) & 0xFF)
    if value & 0x8000:
        return value - 0x10000
    return value
