from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Iterator


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inspection_robot.config import DEFAULT_TAG_MAP
from inspection_robot.config_defaults import DEFAULT_SHELF_MANIFEST, DEFAULT_WAREHOUSE_MAP
from inspection_robot.core.store import InspectionStore
from inspection_robot.robot import sensors
from inspection_robot.runtime import RobotRuntime, RobotRuntimeConfig, flatten_route, heading_for_delta
from inspection_robot.core.planner import plan_patrol_route


class RuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def make_store(self) -> InspectionStore:
        return InspectionStore(
            DEFAULT_TAG_MAP,
            warehouse_map=DEFAULT_WAREHOUSE_MAP,
            shelf_manifest={"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            root=self.root,
        )

    def test_route_helpers_flatten_and_report_heading(self) -> None:
        route = plan_patrol_route(DEFAULT_WAREHOUSE_MAP, ["A1"])

        self.assertEqual(flatten_route(route)[0], (0, 0))
        self.assertEqual(heading_for_delta((0, 0), (1, 0)), "E")
        self.assertEqual(heading_for_delta((1, 0), (1, 1)), "S")

    def test_runtime_records_path_pose_scan_and_finish_with_fakes(self) -> None:
        store = self.make_store()
        fake_motion = FakeMotion()
        fake_sensors = FakeSensors(distances=[400] * 20, tapes=[(1, 1, 1, 1)] * 20)
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            {"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            config=RobotRuntimeConfig(step_seconds=0, poll_seconds=0, scan_timeout_seconds=0, action_settle_seconds=0),
            motion_adapter=fake_motion,
            sensor_adapter=fake_sensors,
            alarm_adapter=FakeAlarm(),
            detection_provider=fake_detection_provider,
        )

        runtime.run_patrol(shelf_order=["A1"])
        snapshot = store.snapshot()

        self.assertEqual(snapshot["task_status"], "FINISHED")
        self.assertEqual(snapshot["current_shelf"], "A1")
        self.assertGreater(len(snapshot["path"]["waypoints"]), 0)
        self.assertTrue(any(event["type"] == "shelf_scanned" for event in snapshot["events"]))
        self.assertIn("move_forward", fake_motion.calls)
        self.assertIn("strafe_right", fake_motion.calls)
        self.assertNotIn("move_backward", fake_motion.calls)
        self.assertGreaterEqual(fake_motion.calls.count("rotate_right"), 2)

    def test_runtime_uses_configured_start_heading(self) -> None:
        warehouse_map = {
            "grid_size": [3, 3],
            "start": [0, 0],
            "start_heading": "N",
            "home": [0, 0],
            "forbidden_cells": [],
            "shelf_points": {"A1": {"scan_pose": [1, 0, "E"], "safe_side": "W"}},
        }
        store = InspectionStore(
            DEFAULT_TAG_MAP,
            warehouse_map=warehouse_map,
            shelf_manifest={"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            root=self.root,
        )
        fake_motion = FakeMotion()
        runtime = RobotRuntime(
            store,
            warehouse_map,
            {"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            config=RobotRuntimeConfig(step_seconds=0, poll_seconds=0, scan_timeout_seconds=0, action_settle_seconds=0),
            motion_adapter=fake_motion,
            sensor_adapter=FakeSensors(distances=[400] * 4, tapes=[(1, 1, 1, 1)] * 4),
            alarm_adapter=FakeAlarm(),
            detection_provider=fake_detection_provider,
        )

        runtime.run_patrol(shelf_order=["A1"], max_steps=1)

        self.assertIn("strafe_right", fake_motion.calls)
        self.assertNotIn("move_forward", fake_motion.calls)

    def test_runtime_reports_obstacle_wait_and_clear(self) -> None:
        store = self.make_store()
        fake_motion = FakeMotion()
        fake_alarm = FakeAlarm()
        fake_sensors = FakeSensors(distances=[150, 150, 150, 320, 400, 400], tapes=[(1, 1, 1, 1)] * 10)
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            {"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            config=RobotRuntimeConfig(step_seconds=0, poll_seconds=0, scan_timeout_seconds=0, action_settle_seconds=0),
            motion_adapter=fake_motion,
            sensor_adapter=fake_sensors,
            alarm_adapter=fake_alarm,
            detection_provider=fake_detection_provider,
        )

        runtime.run_patrol(shelf_order=["A1"], max_steps=3)
        event_types = [event["type"] for event in store.snapshot()["events"]]

        self.assertIn("obstacle_wait", event_types)
        self.assertIn("obstacle_clear", event_types)
        self.assertIn("obstacle_wait", fake_alarm.calls)

    def test_runtime_turns_right_when_all_tape_sensors_hit_end_boundary(self) -> None:
        store = self.make_store()
        fake_motion = FakeMotion()
        fake_sensors = FakeSensors(distances=[400, 400], tapes=[(0, 0, 0, 0), (1, 1, 1, 1)])
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            {"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            config=RobotRuntimeConfig(step_seconds=0, poll_seconds=0, scan_timeout_seconds=0, action_settle_seconds=0, boundary_cooldown_seconds=0, boundary_confirm_samples=1),
            motion_adapter=fake_motion,
            sensor_adapter=fake_sensors,
            alarm_adapter=FakeAlarm(),
            detection_provider=fake_detection_provider,
        )

        runtime.run_patrol(shelf_order=["A1"], max_steps=1)
        snapshot = store.snapshot()

        self.assertTrue(any(event["type"] == "forbidden_zone_detected" for event in snapshot["events"]))
        self.assertIn("rotate_right", fake_motion.calls)
        self.assertNotIn("move_backward", fake_motion.calls)
        self.assertEqual(snapshot["pose"]["heading"], "S")

    def test_continuous_patrol_treats_center_tape_as_line_follow_forward(self) -> None:
        store = self.make_store()
        fake_motion = FakeMotion()
        fake_sensors = FakeSensors(distances=[400] * 6, tapes=[(1, 0, 0, 1)] * 4)
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            {"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            config=RobotRuntimeConfig(
                step_seconds=0,
                line_follow_step_seconds=0,
                scan_timeout_seconds=0,
                action_settle_seconds=0,
                boundary_cooldown_seconds=0,
                boundary_confirm_samples=1,
                boundary_min_black_sensors=4,
            ),
            motion_adapter=fake_motion,
            sensor_adapter=fake_sensors,
            alarm_adapter=FakeAlarm(),
            gimbal_adapter=FakeGimbal(),
            detection_provider=fake_detection_provider,
        )

        runtime.run_continuous_patrol(max_iterations=2)

        self.assertIn("move_forward", fake_motion.calls)
        self.assertNotIn("rotate_right", fake_motion.calls)
        self.assertNotIn("rotate_left", fake_motion.calls)

    def test_continuous_patrol_stops_when_line_is_not_centered(self) -> None:
        store = self.make_store()
        fake_motion = FakeMotion()
        fake_sensors = FakeSensors(distances=[400] * 4, tapes=[(0, 1, 1, 1), (0, 1, 1, 1)])
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            {"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            config=RobotRuntimeConfig(
                step_seconds=0,
                line_follow_step_seconds=0,
                scan_timeout_seconds=0,
                action_settle_seconds=0,
                boundary_cooldown_seconds=0,
                boundary_confirm_samples=1,
                boundary_min_black_sensors=4,
            ),
            motion_adapter=fake_motion,
            sensor_adapter=fake_sensors,
            alarm_adapter=FakeAlarm(),
            gimbal_adapter=FakeGimbal(),
            detection_provider=fake_detection_provider,
        )

        runtime.run_continuous_patrol(max_iterations=1)

        self.assertIn("stop", fake_motion.calls)
        self.assertNotIn("move_forward", fake_motion.calls)
        self.assertNotIn("rotate_right", fake_motion.calls)
        self.assertNotIn("rotate_left", fake_motion.calls)

    def test_runtime_avoids_obstacle_to_right_with_full_body_steps(self) -> None:
        store = self.make_store()
        fake_motion = FakeMotion()
        fake_sensors = FakeSensors(
            distances=[120, 400, 400, 400, 400, 400, 400, 400],
            tapes=[(1, 1, 1, 1)] * 4,
        )
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            {"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            config=RobotRuntimeConfig(
                blocked_distance_mm=160,
                clear_distance_mm=240,
                blocked_samples=1,
                obstacle_wait_seconds=0,
                step_seconds=0,
                avoidance_body_seconds=0,
                scan_timeout_seconds=0,
                action_settle_seconds=0,
            ),
            motion_adapter=fake_motion,
            sensor_adapter=fake_sensors,
            alarm_adapter=FakeAlarm(),
            detection_provider=fake_detection_provider,
        )

        runtime.run_patrol(shelf_order=["A1"], max_steps=1)
        event_types = [event["type"] for event in store.snapshot()["events"]]

        self.assertIn("obstacle_wait", event_types)
        self.assertIn("obstacle_avoidance_step", event_types)
        self.assertIn("obstacle_clear", event_types)
        self.assertLess(fake_motion.calls.index("rotate_right"), fake_motion.calls.index("rotate_left"))
        self.assertGreaterEqual(fake_motion.calls.count("move_forward"), 4)

    def test_continuous_patrol_skips_first_cycle_then_scans_visible_shelf(self) -> None:
        store = self.make_store()
        fake_motion = FakeMotion()
        fake_sensors = FakeSensors(
            distances=[400] * 12,
            tapes=[(0, 0, 0, 0), (0, 0, 0, 0), (1, 0, 0, 1), (1, 0, 0, 1)],
        )
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            {"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            config=RobotRuntimeConfig(
                step_seconds=0,
                poll_seconds=0,
                scan_timeout_seconds=0,
                scan_interval_seconds=0,
                action_settle_seconds=0,
                boundary_cooldown_seconds=0,
                turns_per_cycle=2,
                skip_scan_cycles=1,
            ),
            motion_adapter=fake_motion,
            sensor_adapter=fake_sensors,
            alarm_adapter=FakeAlarm(),
            gimbal_adapter=FakeGimbal(),
            detection_provider=fake_detection_provider,
        )

        runtime.run_continuous_patrol(max_iterations=2)
        snapshot = store.snapshot()

        self.assertIn("rotate_right", fake_motion.calls)
        self.assertIn("move_forward", fake_motion.calls)
        self.assertEqual(snapshot["current_shelf"], "A1")
        self.assertTrue(any(event["type"] == "shelf_scanned" for event in snapshot["events"]))


class FakeMotion:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def move_forward_slow(self, **_: object) -> None:
        self.calls.append("move_forward")

    def move_backward_slow(self, **_: object) -> None:
        self.calls.append("move_backward")

    def strafe_left_slow(self, **_: object) -> None:
        self.calls.append("strafe_left")

    def strafe_right_slow(self, **_: object) -> None:
        self.calls.append("strafe_right")

    def rotate_left_slow(self, **_: object) -> None:
        self.calls.append("rotate_left")

    def rotate_right_slow(self, **_: object) -> None:
        self.calls.append("rotate_right")

    def stop(self) -> None:
        self.calls.append("stop")


class FakeSensors:
    def __init__(self, distances: list[int | None], tapes: list[tuple[int, int, int, int] | None]) -> None:
        self.distances = list(distances)
        self.tapes = list(tapes)

    def read_distance_mm(self) -> int | None:
        if self.distances:
            return self.distances.pop(0)
        return 400

    def read_tape_boundary(self) -> tuple[int, int, int, int] | None:
        if self.tapes:
            return self.tapes.pop(0)
        return (1, 1, 1, 1)

    @staticmethod
    def tape_boundary_detected(state: tuple[int, int, int, int] | None) -> bool:
        return sensors.tape_boundary_detected(state)

    @staticmethod
    def full_tape_boundary_detected(state: tuple[int, int, int, int] | None) -> bool:
        return sensors.full_tape_boundary_detected(state)


class FakeAlarm:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def show_normal(self) -> None:
        self.calls.append("normal")

    def show_obstacle_wait(self) -> None:
        self.calls.append("obstacle_wait")

    def show_warning(self) -> None:
        self.calls.append("warning")

    def clear_alarm(self) -> None:
        self.calls.append("clear")


class FakeGimbal:
    def initialize_side_camera(self) -> None:
        pass


def fake_detection_provider(**_: object) -> Iterator[dict[str, object]]:
    yield {"tag_id": "101", "kind": "shelf", "shelf_id": "A1", "marker_family": "TAG36H11", "ocr_text": "A1"}
    yield {
        "tag_id": "1",
        "kind": "item",
        "item_id": "item_01",
        "marker_family": "TAG36H11",
        "color": "RED",
        "ocr_text": "ITEM-01",
        "image_class": "BOTTLE",
    }
    yield {
        "tag_id": "2",
        "kind": "item",
        "item_id": "item_02",
        "marker_family": "TAG36H11",
        "color": "BLUE",
        "ocr_text": "ITEM-02",
        "image_class": "BOX",
    }


if __name__ == "__main__":
    unittest.main()
