from __future__ import annotations

import os
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
from inspection_robot.runtime import (
    RobotRuntime,
    RobotRuntimeConfig,
    _cycle_from_turn_count,
    _format_tape_state,
    flatten_route,
    heading_for_delta,
    load_calibration_into_config,
)
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

    def test_runtime_config_defaults_match_demo_calibration(self) -> None:
        with temporary_env_cleared(
            "ROBOT_PATROL_SPEED",
            "ROBOT_SLOW_SPEED",
            "ROBOT_PATROL_STEP_SECONDS",
            "ROBOT_STEP_SECONDS",
            "ROBOT_ACTION_SETTLE_SECONDS",
            "AVOIDANCE_SPEED",
            "AVOIDANCE_BODY_SECONDS",
            "AVOIDANCE_SIDE_CLEARANCE_BODIES",
            "AVOIDANCE_PARALLEL_BODIES",
            "AVOIDANCE_RETURN_BODIES",
            "BOUNDARY_MIN_BLACK_SENSORS",
            "BOUNDARY_COOLDOWN_SECONDS",
            "MOTION_GUARD_POLL_SECONDS",
            "LINE_FOLLOW_SPEED",
            "LINE_FOLLOW_TURN_SPEED",
        ):
            config = RobotRuntimeConfig()

        self.assertEqual(config.patrol_speed, 30)
        self.assertEqual(config.step_seconds, 0.25)
        self.assertEqual(config.action_settle_seconds, 0.45)
        self.assertEqual(config.avoidance_speed, 20)
        self.assertEqual(config.avoidance_body_seconds, 0.35)
        self.assertEqual(config.avoidance_return_bodies, 0.8)
        self.assertEqual(config.boundary_min_black_sensors, 4)
        self.assertEqual(config.boundary_cooldown_seconds, 0.05)
        self.assertEqual(config.motion_guard_poll_seconds, 0.02)
        self.assertEqual(config.line_follow_speed, 30)
        self.assertEqual(config.line_follow_turn_speed, 30)

    def test_runtime_config_reads_environment_for_each_new_instance(self) -> None:
        with temporary_env({"ROBOT_PATROL_SPEED": "33", "ROBOT_PATROL_STEP_SECONDS": "0.31"}):
            first = RobotRuntimeConfig()
        with temporary_env({"ROBOT_PATROL_SPEED": "27", "ROBOT_PATROL_STEP_SECONDS": "0.22"}):
            second = RobotRuntimeConfig()

        self.assertEqual(first.patrol_speed, 33)
        self.assertEqual(first.step_seconds, 0.31)
        self.assertEqual(second.patrol_speed, 27)
        self.assertEqual(second.step_seconds, 0.22)

    def test_route_helpers_flatten_and_report_heading(self) -> None:
        route = plan_patrol_route(DEFAULT_WAREHOUSE_MAP, ["A1"])

        self.assertEqual(flatten_route(route)[0], (0, 0))
        self.assertEqual(heading_for_delta((0, 0), (1, 0)), "E")
        self.assertEqual(heading_for_delta((1, 0), (1, 1)), "S")

    def test_calibration_straight_duration_does_not_override_patrol_step(self) -> None:
        config_dir = self.root / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "calibration.json").write_text(
            '{"straight_speed": 6, "straight_step_seconds": 2.0, "line_follow_speed": 7, "line_follow_step_seconds": 0.11}',
            encoding="utf-8",
        )
        config = RobotRuntimeConfig(patrol_speed=5, step_seconds=0.16, line_follow_speed=5, line_follow_step_seconds=0.14)

        load_calibration_into_config(config, self.root)

        self.assertEqual(config.patrol_speed, 6)
        self.assertEqual(config.step_seconds, 0.16)
        self.assertEqual(config.line_follow_speed, 7)
        self.assertEqual(config.line_follow_step_seconds, 0.11)

    def test_calibration_patrol_step_seconds_overrides_patrol_step(self) -> None:
        config_dir = self.root / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "calibration.json").write_text(
            '{"straight_speed": 30, "patrol_step_seconds": 0.25}',
            encoding="utf-8",
        )
        config = RobotRuntimeConfig(patrol_speed=5, step_seconds=0.16)

        load_calibration_into_config(config, self.root)

        self.assertEqual(config.patrol_speed, 30)
        self.assertEqual(config.step_seconds, 0.25)

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

    def test_continuous_patrol_ignores_partial_tape_outside_line_follow_phase(self) -> None:
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
        self.assertIn("move_forward", fake_motion.calls)
        self.assertNotIn("rotate_right", fake_motion.calls)
        self.assertNotIn("rotate_left", fake_motion.calls)

    def test_continuous_patrol_motion_debug_mode_does_not_scan_by_default(self) -> None:
        store = self.make_store()
        fake_motion = FakeMotion()
        fake_sensors = FakeSensors(distances=[400] * 6, tapes=[(1, 1, 1, 1)] * 4)
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            {"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            config=RobotRuntimeConfig(
                step_seconds=0,
                poll_seconds=0,
                scan_interval_seconds=0,
                scan_timeout_seconds=0,
                action_settle_seconds=0,
            ),
            motion_adapter=fake_motion,
            sensor_adapter=fake_sensors,
            alarm_adapter=FakeAlarm(),
            gimbal_adapter=FakeGimbal(),
            detection_provider=fake_detection_provider,
        )

        runtime.run_continuous_patrol(max_iterations=1)
        event_types = [event["type"] for event in store.snapshot()["events"]]

        self.assertIn("motion_debug", event_types)
        self.assertNotIn("shelf_scanned", event_types)

    def test_continuous_patrol_ignores_three_black_boundary_by_default(self) -> None:
        store = self.make_store()
        fake_motion = FakeMotion()
        fake_sensors = FakeSensors(distances=[400] * 6, tapes=[(0, 0, 0, 1)])
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            {"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            config=RobotRuntimeConfig(
                step_seconds=0,
                line_follow_step_seconds=0,
                poll_seconds=0,
                scan_timeout_seconds=0,
                action_settle_seconds=0,
                boundary_cooldown_seconds=0,
            ),
            motion_adapter=fake_motion,
            sensor_adapter=fake_sensors,
            alarm_adapter=FakeAlarm(),
            gimbal_adapter=FakeGimbal(),
            detection_provider=fake_detection_provider,
        )

        runtime.run_continuous_patrol(max_iterations=1)

        self.assertIn("move_forward", fake_motion.calls)
        self.assertNotIn("rotate_right", fake_motion.calls)

    def test_motion_guard_latches_fast_full_black_boundary_during_line_follow(self) -> None:
        store = self.make_store()
        fake_motion = FakeMotion()
        fake_sensors = FakeSensors(
            distances=[400] * 12,
            tapes=[
                (0, 0, 0, 0),
                (1, 0, 0, 1),
                (0, 0, 0, 0),
                (1, 1, 1, 1),
                (1, 1, 1, 1),
            ],
        )
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            {"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            config=RobotRuntimeConfig(
                step_seconds=0,
                line_follow_enabled=True,
                line_follow_step_seconds=0.06,
                motion_guard_poll_seconds=0.02,
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
        event_stages = [
            event["evidence"].get("stage")
            for event in store.snapshot()["events"]
            if event["type"] == "motion_debug" and isinstance(event.get("evidence"), dict)
        ]

        self.assertGreaterEqual(fake_motion.calls.count("rotate_right"), 2)
        self.assertIn("motion_guard_boundary_latched", event_stages)

    def test_continuous_patrol_safety_wrapper_records_unexpected_exception(self) -> None:
        store = self.make_store()
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            {"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            config=RobotRuntimeConfig(action_settle_seconds=0),
            motion_adapter=FakeMotion(),
            sensor_adapter=FakeSensors(distances=[], tapes=[]),
            alarm_adapter=FakeAlarm(),
            gimbal_adapter=FakeGimbal(),
            detection_provider=fake_detection_provider,
        )

        def explode() -> None:
            raise ValueError("camera boom")

        runtime.run_continuous_patrol = explode  # type: ignore[method-assign]
        runtime._run_continuous_patrol_safely()
        snapshot = store.snapshot()

        self.assertFalse(snapshot["hardware_connected"])
        self.assertEqual(snapshot["task_status"], "ERROR")
        self.assertIn("runtime fatal error", snapshot["last_message"])

    def test_failed_closed_loop_turn_returns_result_instead_of_raising(self) -> None:
        store = self.make_store()
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            {"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            config=RobotRuntimeConfig(action_settle_seconds=0),
            motion_adapter=FakeMotion(),
            sensor_adapter=FakeSensors(distances=[], tapes=[]),
            alarm_adapter=FakeAlarm(),
            imu_adapter=FakeImuTurn(ok=False, final_degrees=43.0),
            detection_provider=fake_detection_provider,
        )

        result = runtime.turn_90_closed_loop("right")

        self.assertIsNotNone(result)
        self.assertFalse(result["ok"])  # type: ignore[index]
        self.assertEqual(result["final_degrees"], 43.0)  # type: ignore[index]
        self.assertEqual(store.snapshot()["task_status"], "ERROR")

    def test_invalid_avoidance_direction_falls_back_to_right(self) -> None:
        store = self.make_store()
        fake_motion = FakeMotion()
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            {"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            config=RobotRuntimeConfig(
                avoidance_turn_direction="sideways",
                avoidance_body_seconds=0,
                action_settle_seconds=0,
            ),
            motion_adapter=fake_motion,
            sensor_adapter=FakeSensors(distances=[400] * 8, tapes=[]),
            alarm_adapter=FakeAlarm(),
            detection_provider=fake_detection_provider,
        )

        with self.assertLogs("inspection_robot.runtime", level="WARNING"):
            self.assertTrue(runtime._avoid_to_safe_side(None))
        self.assertEqual(fake_motion.calls[0:2], ["stop", "rotate_right"])

    def test_cycle_count_advances_after_full_turn_window(self) -> None:
        self.assertEqual(_cycle_from_turn_count(0, 2), 1)
        self.assertEqual(_cycle_from_turn_count(1, 2), 1)
        self.assertEqual(_cycle_from_turn_count(2, 2), 1)
        self.assertEqual(_cycle_from_turn_count(3, 2), 2)

    def test_format_tape_state_uses_readable_missing_value(self) -> None:
        self.assertEqual(_format_tape_state(None), "无读数")
        self.assertEqual(_format_tape_state((1, 0, 0, 1)), "1001")

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
                scan_enabled=True,
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

    def test_scan_visible_shelf_maps_shelf_tag_to_shelf_id(self) -> None:
        store = self.make_store()
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            {"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            config=RobotRuntimeConfig(scan_timeout_seconds=0, action_settle_seconds=0),
            motion_adapter=FakeMotion(),
            sensor_adapter=FakeSensors(distances=[400] * 4, tapes=[(1, 1, 1, 1)] * 4),
            alarm_adapter=FakeAlarm(),
            gimbal_adapter=FakeGimbal(),
            detection_provider=shelf_tag_only_detection_provider,
        )

        runtime._scan_visible_shelf()
        snapshot = store.snapshot()

        self.assertEqual(snapshot["current_shelf"], "A1")
        self.assertTrue(any(event["type"] == "shelf_scanned" for event in snapshot["events"]))

    def test_perceptual_cycle_advances_with_one_missed_shelf(self) -> None:
        store = InspectionStore(
            DEFAULT_TAG_MAP,
            warehouse_map=DEFAULT_WAREHOUSE_MAP,
            shelf_manifest=DEFAULT_SHELF_MANIFEST,
            root=self.root,
        )
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            DEFAULT_SHELF_MANIFEST,
            config=RobotRuntimeConfig(scan_timeout_seconds=0, action_settle_seconds=0),
            motion_adapter=FakeMotion(),
            sensor_adapter=FakeSensors(distances=[400] * 4, tapes=[(1, 1, 1, 1)] * 4),
            alarm_adapter=FakeAlarm(),
            gimbal_adapter=FakeGimbal(),
            detection_provider=fake_detection_provider,
        )

        for shelf_id in ["A1", "A2", "A3", "A4", "B4", "B3", "B1"]:
            runtime._perform_scan(shelf_id, f"{shelf_id}_SCAN", detections=[])
        snapshot = store.snapshot()

        self.assertEqual(snapshot["patrol_cycle"], 2)
        self.assertFalse(snapshot["skip_shortage_detection"])
        cycle_event = next(event for event in snapshot["events"] if event["type"] == "cycle_completed")
        self.assertEqual(cycle_event["evidence"], {"observed_shelves": ["A1", "A2", "A3", "A4", "B4", "B3", "B1"], "missed_shelves": ["B2"]})

    def test_perceptual_cycle_does_not_advance_when_two_shelves_are_missed(self) -> None:
        store = InspectionStore(
            DEFAULT_TAG_MAP,
            warehouse_map=DEFAULT_WAREHOUSE_MAP,
            shelf_manifest=DEFAULT_SHELF_MANIFEST,
            root=self.root,
        )
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            DEFAULT_SHELF_MANIFEST,
            config=RobotRuntimeConfig(scan_timeout_seconds=0, action_settle_seconds=0),
            motion_adapter=FakeMotion(),
            sensor_adapter=FakeSensors(distances=[400] * 4, tapes=[(1, 1, 1, 1)] * 4),
            alarm_adapter=FakeAlarm(),
            gimbal_adapter=FakeGimbal(),
            detection_provider=fake_detection_provider,
        )

        for shelf_id in ["A1", "A2", "A3", "A4", "B4", "B1"]:
            runtime._perform_scan(shelf_id, f"{shelf_id}_SCAN", detections=[])

        self.assertEqual(store.snapshot()["patrol_cycle"], 1)

    def test_second_cycle_missing_item_triggers_high_priority_alarm(self) -> None:
        store = self.make_store()
        fake_alarm = FakeAlarm()
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            {"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            config=RobotRuntimeConfig(scan_timeout_seconds=0, action_settle_seconds=0),
            motion_adapter=FakeMotion(),
            sensor_adapter=FakeSensors(distances=[400] * 4, tapes=[(1, 1, 1, 1)] * 4),
            alarm_adapter=fake_alarm,
            gimbal_adapter=FakeGimbal(),
            detection_provider=fake_detection_provider,
        )
        store.record_cycle(2, False)

        runtime._perform_scan(
            "A1",
            "A1_SCAN",
            detections=[
                {
                    "tag_id": "1",
                    "kind": "item",
                    "item_id": "item_01",
                    "marker_family": "TAG36H11",
                    "color": "RED",
                    "ocr_text": "ITEM-01",
                    "image_class": "BOTTLE",
                }
            ],
        )

        self.assertIn("high_priority_alarm", fake_alarm.calls)

    def test_camera_failure_requests_manual_cycle_fallback_confirmation(self) -> None:
        store = InspectionStore(
            DEFAULT_TAG_MAP,
            warehouse_map=DEFAULT_WAREHOUSE_MAP,
            shelf_manifest=DEFAULT_SHELF_MANIFEST,
            root=self.root,
        )
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            DEFAULT_SHELF_MANIFEST,
            config=RobotRuntimeConfig(camera_failure_scan_threshold=2, camera_failure_request_cooldown_seconds=0),
            motion_adapter=FakeMotion(),
            sensor_adapter=FakeSensors(distances=[400] * 4, tapes=[(1, 1, 1, 1)] * 4),
            alarm_adapter=FakeAlarm(),
            gimbal_adapter=FakeGimbal(),
            detection_provider=empty_detection_provider,
        )

        runtime._record_empty_vision_scan()
        runtime._record_empty_vision_scan()
        waiting = [event for event in store.snapshot()["events"] if event["status"] == "waiting_confirm"]

        self.assertEqual(waiting[-1]["type"], "scan_failed")
        self.assertEqual(waiting[-1]["evidence"]["reason"], "camera_cycle_fallback_required")
        self.assertEqual(runtime.confirm_camera_cycle_fallback(), 2)
        self.assertEqual(store.snapshot()["patrol_cycle"], 2)


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

    def show_high_priority_alarm(self) -> None:
        self.calls.append("high_priority_alarm")

    def clear_alarm(self) -> None:
        self.calls.append("clear")


class FakeGimbal:
    def initialize_side_camera(self) -> None:
        pass


class FakeImuTurn:
    def __init__(self, *, ok: bool, final_degrees: float) -> None:
        self.ok = ok
        self.final_degrees = final_degrees

    def turn_90_with_result(self, direction: str, *_: object) -> dict[str, object]:
        return {
            "ok": self.ok,
            "source": "fake_imu",
            "direction": direction,
            "target_degrees": 90.0,
            "final_degrees": self.final_degrees,
            "error_degrees": 90.0 - self.final_degrees,
            "attempts": 3,
            "message": "did not converge",
        }


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


def shelf_tag_only_detection_provider(**_: object) -> Iterator[dict[str, object]]:
    yield {"tag_id": "101", "marker_family": "TAG36H11", "ocr_text": "A1"}


def empty_detection_provider(**_: object) -> Iterator[dict[str, object]]:
    return
    yield {}


class temporary_env:
    def __init__(self, updates: dict[str, str]) -> None:
        self.updates = updates
        self.previous: dict[str, str | None] = {}

    def __enter__(self) -> None:
        self.previous = {key: os.environ.get(key) for key in self.updates}
        os.environ.update(self.updates)

    def __exit__(self, *_: object) -> None:
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class temporary_env_cleared:
    def __init__(self, *keys: str) -> None:
        self.keys = keys
        self.previous: dict[str, str | None] = {}

    def __enter__(self) -> None:
        self.previous = {key: os.environ.get(key) for key in self.keys}
        for key in self.keys:
            os.environ.pop(key, None)

    def __exit__(self, *_: object) -> None:
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
