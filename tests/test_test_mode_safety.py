from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inspection_robot.test_mode import CalibrationStore, TestSessionManager


class TestModeSafetyTest(unittest.TestCase):
    def test_calibration_load_missing_file_has_no_write_side_effect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = CalibrationStore(root)

            calibration = store.load()

            self.assertEqual(calibration["straight_speed"], 20)
            self.assertEqual(calibration["patrol_step_seconds"], 0.18)
            self.assertEqual(calibration["patrol_settle_seconds"], 0.05)
            self.assertEqual(calibration["action_settle_seconds"], 0.7)
            self.assertFalse((root / "config" / "calibration.json").exists())

    def test_line_follow_test_uses_bounded_strafe_not_in_place_rotate(self) -> None:
        motion = FakeMotion()
        sensors = FakeSensors(
            [
                (1, 0, 0, 1),
                (0, 1, 1, 1),
                (1, 1, 1, 0),
                *((1, 1, 1, 1) for _ in range(15)),
            ]
        )
        manager = TestSessionManager(motion_adapter=motion, sensor_adapter=sensors)

        manager._line_follow_worker(6, 0.0)

        self.assertIn("move_forward", motion.calls)
        self.assertIn("strafe_left", motion.calls)
        self.assertIn("strafe_right", motion.calls)
        self.assertNotIn("rotate_left", motion.calls)
        self.assertNotIn("rotate_right", motion.calls)


class FakeMotion:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def move_forward_slow(self, *, speed: int, duration_seconds: float) -> None:
        self.calls.append("move_forward")

    def strafe_left_slow(self, *, speed: int, duration_seconds: float) -> None:
        self.calls.append("strafe_left")

    def strafe_right_slow(self, *, speed: int, duration_seconds: float) -> None:
        self.calls.append("strafe_right")

    def stop(self) -> None:
        self.calls.append("stop")


class FakeSensors:
    def __init__(self, tapes: list[tuple[int, int, int, int]]) -> None:
        self.tapes = list(tapes)

    def read_tape_boundary(self) -> tuple[int, int, int, int]:
        if self.tapes:
            return self.tapes.pop(0)
        return (1, 1, 1, 1)


if __name__ == "__main__":
    unittest.main()
