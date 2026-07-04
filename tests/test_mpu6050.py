from __future__ import annotations

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
        bus = FakeBus({mpu6050.WHO_AM_I: 0x68, mpu6050.GYRO_ZOUT_H: 0x00, mpu6050.GYRO_ZOUT_H + 1: 0x83})
        gyro = mpu6050.MPU6050Gyro(bus=bus)

        gyro.initialize()

        self.assertEqual(
            bus.writes,
            [
                (0x68, mpu6050.PWR_MGMT_1, 0x03),
                (0x68, mpu6050.GYRO_CONFIG, 0x00),
            ],
        )
        self.assertEqual(gyro.who_am_i(), 0x68)
        self.assertAlmostEqual(gyro.read_gyro_z_dps(), 1.0)

    def test_turn_90_with_gyro_stops_when_integrated_angle_reaches_target(self) -> None:
        gyro = FakeGyro()
        motion = FakeMotion()
        config = mpu6050.Turn90Config(
            speed=12,
            fallback_seconds=0.05,
            target_degrees=12.0,
            tolerance_degrees=0.0,
            sample_seconds=0.001,
            bias_samples=1,
        )

        completed = mpu6050.turn_90_with_gyro("right", motion, gyro, config)

        self.assertTrue(completed)
        self.assertEqual(motion.calls[0], ("rotate_right", 12, 0.0))
        self.assertEqual(motion.calls[-1], ("stop", None, None))


class FakeBus:
    def __init__(self, reads: dict[int, int]) -> None:
        self.reads = reads
        self.writes: list[tuple[int, int, int]] = []

    def write_byte_data(self, address: int, register: int, value: int) -> None:
        self.writes.append((address, register, value))

    def read_byte_data(self, address: int, register: int) -> int:
        return self.reads[register]


class FakeGyro:
    def initialize(self) -> None:
        pass

    def who_am_i(self) -> int:
        return 0x68

    def calibrate_z_bias(self, *, samples: int, sample_seconds: float) -> float:
        return 0.0

    def read_gyro_z_dps(self) -> float:
        return 12000.0


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
