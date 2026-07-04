from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inspection_robot.robot import mpu6050


class MPU6050Test(unittest.TestCase):
    def test_gyro_initialization_and_z_rate_use_documented_registers(self) -> None:
        bus = FakeBus({
            mpu6050.WHO_AM_I: 0x68,
            **_word(mpu6050.ACCEL_XOUT_H, 0),
            **_word(mpu6050.ACCEL_XOUT_H + 2, 0),
            **_word(mpu6050.ACCEL_XOUT_H + 4, 16384),
            **_word(mpu6050.GYRO_ZOUT_H, 0x0083),
        })
        gyro = mpu6050.MPU6050Gyro(bus=bus)

        gyro.initialize()

        self.assertEqual(
            bus.writes,
            [
                (0x68, mpu6050.PWR_MGMT_1, 0x03),
                (0x68, mpu6050.ACCEL_CONFIG, 0x00),
                (0x68, mpu6050.GYRO_CONFIG, 0x00),
            ],
        )
        self.assertEqual(gyro.who_am_i(), 0x68)
        self.assertAlmostEqual(gyro.read_gyro_z_dps(), 1.0)
        self.assertAlmostEqual(gyro.read_accel_mps2()["z"], mpu6050.GRAVITY_MPS2, places=3)

    def test_turn_90_with_gyro_stops_when_integrated_angle_reaches_target(self) -> None:
        gyro = FakeGyro(rate_dps=120.0)
        motion = FakeMotion()
        config = mpu6050.Turn90Config(
            speed=12,
            fallback_seconds=0.1,
            target_degrees=12.0,
            tolerance_degrees=0.2,
            sample_seconds=0.0,
            bias_samples=1,
            min_pulse_seconds=0.001,
            max_pulse_seconds=0.2,
            settle_seconds=0,
        )

        result = mpu6050.turn_90_with_gyro("right", motion, gyro, config)

        self.assertTrue(result.ok)
        self.assertLessEqual(abs(result.error_degrees), 0.2)
        self.assertEqual(motion.calls[0], ("rotate_right", 12, 0.1))
        self.assertEqual(motion.calls[-1], ("stop", None, None))

    def test_turn_90_with_gyro_adds_same_direction_correction_when_short(self) -> None:
        gyro = FakeGyro(rate_dps=100.0)
        motion = FakeMotion()
        config = mpu6050.Turn90Config(
            speed=10,
            fallback_seconds=0.08,
            target_degrees=12.0,
            tolerance_degrees=0.1,
            sample_seconds=0.0,
            bias_samples=1,
            min_pulse_seconds=0.001,
            max_pulse_seconds=0.2,
            settle_seconds=0,
        )

        result = mpu6050.turn_90_with_gyro("right", motion, gyro, config)

        self.assertTrue(result.ok)
        self.assertIn(("rotate_right", 10, 0.08), motion.calls)
        self.assertTrue(any(call[0] == "rotate_right" and call[2] > 0.03 for call in motion.calls[1:]))
        self.assertNotIn("rotate_left", [call[0] for call in motion.calls])

    def test_turn_90_with_gyro_reverses_when_overshot(self) -> None:
        gyro = FakeGyro(rate_dps=100.0)
        motion = FakeMotion()
        config = mpu6050.Turn90Config(
            speed=10,
            fallback_seconds=0.15,
            target_degrees=12.0,
            tolerance_degrees=0.1,
            sample_seconds=0.0,
            bias_samples=1,
            min_pulse_seconds=0.001,
            max_pulse_seconds=0.2,
            settle_seconds=0,
        )

        result = mpu6050.turn_90_with_gyro("right", motion, gyro, config)

        self.assertTrue(result.ok)
        self.assertEqual(motion.calls[0], ("rotate_right", 10, 0.15))
        self.assertIn("rotate_left", [call[0] for call in motion.calls])

    def test_turn_90_with_gyro_uses_slower_correction_speed(self) -> None:
        gyro = FakeGyro(rate_dps=100.0)
        motion = FakeMotion()
        config = mpu6050.Turn90Config(
            speed=20,
            correction_speed=6,
            fallback_seconds=0.08,
            target_degrees=12.0,
            tolerance_degrees=0.1,
            sample_seconds=0.0,
            bias_samples=1,
            min_pulse_seconds=0.001,
            max_pulse_seconds=0.2,
            settle_seconds=0,
        )

        result = mpu6050.turn_90_with_gyro("right", motion, gyro, config)

        self.assertTrue(result.ok)
        self.assertEqual(result.pulses[0].speed, 20)
        self.assertTrue(any(pulse.speed == 6 for pulse in result.pulses[1:]))

    def test_motion_sample_reports_accel_and_compensated_gyro_bias(self) -> None:
        reads = {
            mpu6050.WHO_AM_I: 0x68,
            **_word(mpu6050.ACCEL_XOUT_H, 0),
            **_word(mpu6050.ACCEL_XOUT_H + 2, 0),
            **_word(mpu6050.ACCEL_XOUT_H + 4, 16384),
            **_word(mpu6050.TEMP_OUT_H, 0),
            **_word(mpu6050.GYRO_XOUT_H, 131),
            **_word(mpu6050.GYRO_XOUT_H + 2, -262),
            **_word(mpu6050.GYRO_XOUT_H + 4, 393),
        }
        gyro = mpu6050.MPU6050Gyro(bus=FakeBus(reads))
        original = mpu6050.open_default_gyro
        mpu6050.reset_gyro_bias_cache()
        mpu6050.open_default_gyro = lambda: gyro  # type: ignore[assignment]
        try:
            sample = mpu6050.read_motion_sample()
        finally:
            mpu6050.open_default_gyro = original  # type: ignore[assignment]
            mpu6050.reset_gyro_bias_cache()

        self.assertTrue(sample["ok"])
        self.assertTrue(sample["zero_drift_compensated"])
        self.assertAlmostEqual(sample["accel_mps2"]["z"], mpu6050.GRAVITY_MPS2, places=2)
        self.assertEqual(sample["gyro_bias_dps"], {"x": 1.0, "y": -2.0, "z": 3.0})
        self.assertEqual(sample["gyro_dps"], {"x": 0.0, "y": 0.0, "z": 0.0})
        self.assertEqual(sample["orientation_deg"]["roll"], 0.0)
        self.assertEqual(sample["orientation_deg"]["pitch"], -0.0)

    def test_orientation_yaw_integrates_gyro_z_while_flat(self) -> None:
        accel = {"x": 0.0, "y": 0.0, "z": mpu6050.GRAVITY_MPS2}
        gyro = {"x": 0.0, "y": 0.0, "z": 45.0}
        original_axis = os.environ.get("MPU6050_YAW_AXIS")
        original_sign = os.environ.get("MPU6050_YAW_SIGN")
        os.environ["MPU6050_YAW_AXIS"] = "z"
        os.environ["MPU6050_YAW_SIGN"] = "1"
        mpu6050._reset_orientation_state()
        try:
            first = mpu6050._orientation_from_sample(accel, gyro, 10.0)
            second = mpu6050._orientation_from_sample(accel, gyro, 12.0)
        finally:
            _restore_env("MPU6050_YAW_AXIS", original_axis)
            _restore_env("MPU6050_YAW_SIGN", original_sign)
            mpu6050._reset_orientation_state()

        self.assertEqual(first["yaw"], 0.0)
        self.assertEqual(second["yaw"], 90.0)

    def test_status_bias_cache_rejects_large_rotation_rate(self) -> None:
        original_abs = os.environ.get("MPU6050_STATUS_BIAS_MAX_ABS_DPS")
        os.environ["MPU6050_STATUS_BIAS_MAX_ABS_DPS"] = "25"
        mpu6050.reset_gyro_bias_cache()
        try:
            bias = mpu6050._cached_gyro_bias(FakeSequenceGyro([{"x": 0.0, "y": 0.0, "z": 96.0}] * 4))
        finally:
            _restore_env("MPU6050_STATUS_BIAS_MAX_ABS_DPS", original_abs)
            mpu6050.reset_gyro_bias_cache()

        self.assertEqual(bias, {"x": 0.0, "y": 0.0, "z": 0.0})


class FakeBus:
    def __init__(self, reads: dict[int, int]) -> None:
        self.reads = reads
        self.writes: list[tuple[int, int, int]] = []

    def write_byte_data(self, address: int, register: int, value: int) -> None:
        self.writes.append((address, register, value))

    def read_byte_data(self, address: int, register: int) -> int:
        return self.reads[register]


def _word(register: int, value: int) -> dict[int, int]:
    encoded = value & 0xFFFF
    return {register: (encoded >> 8) & 0xFF, register + 1: encoded & 0xFF}


def _restore_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


class FakeGyro:
    def __init__(self, rate_dps: float = 12000.0) -> None:
        self.rate_dps = rate_dps

    def initialize(self) -> None:
        pass

    def who_am_i(self) -> int:
        return 0x68

    def calibrate_z_bias(self, *, samples: int, sample_seconds: float) -> float:
        return 0.0

    def read_gyro_z_dps(self) -> float:
        return self.rate_dps


class FakeSequenceGyro:
    def __init__(self, readings: list[dict[str, float]]) -> None:
        self.readings = list(readings)

    def read_gyro_dps(self) -> dict[str, float]:
        if self.readings:
            return dict(self.readings.pop(0))
        return {"x": 0.0, "y": 0.0, "z": 0.0}


class FakeMotion:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None, float | None]] = []

    def rotate_left_slow(self, *, speed: int, duration_seconds: float) -> None:
        self.calls.append(("rotate_left", speed, duration_seconds))

    def rotate_right_slow(self, *, speed: int, duration_seconds: float) -> None:
        self.calls.append(("rotate_right", speed, duration_seconds))

    def stop(self) -> None:
        self.calls.append(("stop", None, None))


if __name__ == "__main__":
    unittest.main()
