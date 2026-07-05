from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inspection_robot.robot.line_following import (
    TransferLineController,
    TransferLineSettings,
    estimate_transfer_line_error,
    is_transfer_exit_line_frame,
    transfer_line_mask,
)


class TransferLineControllerTest(unittest.TestCase):
    def make_controller(self) -> TransferLineController:
        return TransferLineController(
            TransferLineSettings(
                base_speed=20,
                min_speed=8,
                kp=10.0,
                kd=0.0,
                turn_max=12,
                bridge_seconds=0.1,
                search_seconds=0.3,
                failsafe_seconds=0.6,
            )
        )

    def test_center_line_outputs_straight_or_tiny_correction(self) -> None:
        controller = self.make_controller()

        command = controller.update((1, 0, 0, 1), now=0.0)

        self.assertEqual(command.state, "TRACK")
        self.assertTrue(command.line_seen)
        self.assertFalse(command.stop)
        self.assertEqual(command.speed, 20)
        self.assertEqual(command.correction, 0)

    def test_left_of_center_outputs_left_correction(self) -> None:
        controller = self.make_controller()

        command = controller.update((1, 0, 1, 1), now=0.0)

        self.assertEqual(command.direction, "left")
        self.assertGreater(command.correction, 0)
        self.assertLess(command.speed, 20)

    def test_right_of_center_outputs_right_correction(self) -> None:
        controller = self.make_controller()

        command = controller.update((1, 1, 0, 1), now=0.0)

        self.assertEqual(command.direction, "right")
        self.assertGreater(command.correction, 0)
        self.assertLess(command.speed, 20)

    def test_lost_line_bridges_searches_then_failsafe_stops(self) -> None:
        controller = self.make_controller()
        controller.update((1, 1, 0, 1), now=0.0)

        bridge = controller.update((1, 1, 1, 1), now=0.05)
        search = controller.update((1, 1, 1, 1), now=0.25)
        failsafe = controller.update((1, 1, 1, 1), now=0.7)

        self.assertEqual(bridge.state, "BRIDGE")
        self.assertFalse(bridge.stop)
        self.assertEqual(bridge.direction, "right")
        self.assertEqual(search.state, "BIASED_SEARCH")
        self.assertFalse(search.stop)
        self.assertEqual(failsafe.state, "FAILSAFE")
        self.assertTrue(failsafe.stop)

    def test_transfer_line_helpers_keep_raw_sensor_polarity_separate(self) -> None:
        self.assertEqual(transfer_line_mask((1, 0, 0, 1)), (0, 1, 1, 0))
        self.assertEqual(estimate_transfer_line_error((0, 1, 1, 0)), 0.0)
        self.assertTrue(is_transfer_exit_line_frame((1, 0, 0, 1)))
        self.assertFalse(is_transfer_exit_line_frame((0, 0, 0, 0)))


if __name__ == "__main__":
    unittest.main()
