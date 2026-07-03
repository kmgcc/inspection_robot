from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inspection_robot.robot import alarm, motion, sensors
from inspection_robot.vision import tag_detector


class RobotAdapterTest(unittest.TestCase):
    def test_motion_wraps_vendor_functions_and_leaves_stop_explicit(self) -> None:
        fake = FakeMotionModule()
        original = motion._motion_module
        motion._motion_module = lambda: fake  # type: ignore[assignment]
        try:
            motion.move_forward_slow(speed=42, duration_seconds=0)
            motion.strafe_right_slow(speed=20, duration_seconds=0)
            motion.stop()
        finally:
            motion._motion_module = original  # type: ignore[assignment]

        self.assertEqual(
            fake.calls,
            [
                ("move_forward", 42),
                ("move_forward", 42),
                ("move_right", 20),
                ("move_right", 20),
                ("stop", None),
            ],
        )

    def test_sensors_normalize_tape_state_and_direction_flags(self) -> None:
        self.assertEqual(sensors.normalize_tape_state([1, 0, 1, 0]), (1, 0, 1, 0))
        self.assertEqual(sensors.normalize_tape_state([0b1010]), (1, 0, 1, 0))

        description = sensors.describe_tape_boundary((1, 0, 1, 1))

        self.assertEqual(
            description,
            {
                "left_detected": True,
                "right_detected": False,
                "front_or_center_detected": True,
                "any_detected": True,
            },
        )
        self.assertFalse(sensors.tape_boundary_count_detected((1, 0, 1, 1), min_black=2))
        self.assertTrue(sensors.tape_boundary_count_detected((1, 0, 0, 1), min_black=2))

    def test_ultrasonic_distance_combines_high_and_low_bytes(self) -> None:
        fake = FakeBot({sensors.ULTRASONIC_HIGH_REGISTER: [1], sensors.ULTRASONIC_LOW_REGISTER: [44]})
        original = sensors._BOT
        sensors._BOT = fake
        try:
            self.assertEqual(sensors.read_distance_mm(), 300)
        finally:
            sensors._BOT = original

    def test_alarm_uses_buzzer_and_rgb_without_import_time_hardware(self) -> None:
        fake = FakeBot({})
        original = alarm.get_bot
        alarm.get_bot = lambda: fake  # type: ignore[assignment]
        try:
            alarm.show_warning()
            alarm.clear_alarm()
        finally:
            alarm.get_bot = original  # type: ignore[assignment]

        self.assertIn(("rgb", 1, alarm.COLOR_PURPLE), fake.calls)
        self.assertIn(("beep", 1), fake.calls)
        self.assertIn(("rgb", 0, 0), fake.calls)

    def test_iter_tag_ids_delegates_to_detection_iterator(self) -> None:
        original = tag_detector.iter_detections
        tag_detector.iter_detections = lambda **_: iter([{"tag_id": "101"}, {"tag_id": 1}])  # type: ignore[assignment]
        try:
            self.assertEqual(list(tag_detector.iter_tag_ids()), ["101", "1"])
        finally:
            tag_detector.iter_detections = original  # type: ignore[assignment]


class FakeMotionModule:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None]] = []

    def move_forward(self, speed: int) -> None:
        self.calls.append(("move_forward", speed))

    def move_right(self, speed: int) -> None:
        self.calls.append(("move_right", speed))

    def stop(self) -> None:
        self.calls.append(("stop", None))


class FakeBot:
    def __init__(self, reads: dict[int, list[int]]) -> None:
        self.reads = reads
        self.calls: list[tuple[object, ...]] = []

    def read_data_array(self, register: int, length: int) -> list[int]:
        self.calls.append(("read", register, length))
        return self.reads[register]

    def Ctrl_Ulatist_Switch(self, value: int) -> None:
        self.calls.append(("ultrasonic", value))

    def Ctrl_WQ2812_ALL(self, enabled: int, color: int) -> None:
        self.calls.append(("rgb", enabled, color))

    def Ctrl_BEEP_Switch(self, value: int) -> None:
        self.calls.append(("beep", value))


if __name__ == "__main__":
    unittest.main()
