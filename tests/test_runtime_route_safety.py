from __future__ import annotations

import sys
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inspection_robot.config import DEFAULT_TAG_MAP
from inspection_robot.config_defaults import DEFAULT_SHELF_MANIFEST, DEFAULT_WAREHOUSE_MAP
from inspection_robot.core.store import InspectionStore
from inspection_robot.robot import sensors
from inspection_robot.runtime import RobotRuntime, RobotRuntimeConfig


class RuntimeRouteSafetyTest(unittest.TestCase):
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

    def make_config(
        self,
        *,
        line_follow_enabled: bool = False,
        transfer_line_enabled: bool = False,
    ) -> RobotRuntimeConfig:
        return RobotRuntimeConfig(
            patrol_speed=5,
            step_seconds=0,
            poll_seconds=0,
            scan_timeout_seconds=0,
            scan_interval_seconds=999,
            action_settle_seconds=0,
            boundary_cooldown_seconds=0,
            boundary_confirm_samples=1,
            boundary_min_black_sensors=4,
            line_follow_speed=7,
            line_follow_turn_speed=7,
            line_follow_step_seconds=0,
            line_follow_turn_seconds=0,
            line_follow_search_seconds=0,
            obstacle_wait_seconds=0,
            avoidance_body_seconds=0,
            line_follow_enabled=line_follow_enabled,
            line_follow_auto_enter=line_follow_enabled,
            transfer_line_enabled=transfer_line_enabled,
            transfer_line_speed=8,
            transfer_line_min_speed=5,
            transfer_line_tick_seconds=0,
            transfer_line_exit_confirm_frames=1,
        )

    def test_partial_tape_auto_enters_line_follow_correction(self) -> None:
        store = self.make_store()
        fake_motion = FakeMotion()
        fake_sensors = FakeSensors(distances=[400] * 4, tapes=[(0, 1, 1, 1)] * 3)
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            {"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            config=self.make_config(line_follow_enabled=True),
            motion_adapter=fake_motion,
            sensor_adapter=fake_sensors,
            alarm_adapter=FakeAlarm(),
            gimbal_adapter=FakeGimbal(),
            detection_provider=fake_detection_provider,
        )

        runtime.run_continuous_patrol(max_iterations=1)

        self.assertIn(("strafe_left", 7, 0), fake_motion.calls)
        self.assertNotIn(("move_forward", 5, 0), fake_motion.calls)

    def test_a4_anchor_boundary_turn_does_not_enter_line_follow(self) -> None:
        store = self.make_store()
        fake_motion = FakeMotion()
        fake_sensors = FakeSensors(
            distances=[400] * 8,
            tapes=[(1, 1, 1, 1), (1, 1, 1, 1)],
        )
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            {"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            config=self.make_config(line_follow_enabled=True),
            motion_adapter=fake_motion,
            sensor_adapter=fake_sensors,
            alarm_adapter=FakeAlarm(),
            gimbal_adapter=FakeGimbal(),
            detection_provider=fake_detection_provider,
        )
        runtime._record_observed_shelf("A4")

        self.assertEqual(runtime._handle_tape_boundary((0, 0, 0, 0)), "turn")
        runtime._drive_patrol_step((1, 0, 0, 1))

        self.assertEqual(fake_motion.names().count("rotate_right"), 1)
        self.assertIn(("move_forward", 5, 0), fake_motion.calls)
        self.assertNotIn(("move_forward", 7, 0), fake_motion.calls)

    def test_boundary_pattern_keeps_pure_patrol_when_line_follow_disabled(self) -> None:
        store = self.make_store()
        fake_motion = FakeMotion()
        fake_sensors = FakeSensors(
            distances=[400] * 6,
            tapes=[(0, 0, 0, 0), (1, 0, 0, 1)],
        )
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            {"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            config=self.make_config(),
            motion_adapter=fake_motion,
            sensor_adapter=fake_sensors,
            alarm_adapter=FakeAlarm(),
            gimbal_adapter=FakeGimbal(),
            detection_provider=fake_detection_provider,
        )

        runtime.run_continuous_patrol(max_iterations=1)

        self.assertEqual(fake_motion.names().count("rotate_right"), 1)
        self.assertIn(("move_forward", 5, 0), fake_motion.calls)
        self.assertNotIn(("move_forward", 7, 0), fake_motion.calls)

    def test_line_follow_searches_by_last_correction_when_line_is_temporarily_lost(self) -> None:
        store = self.make_store()
        fake_motion = FakeMotion()
        fake_sensors = FakeSensors(
            distances=[400] * 10,
            tapes=[(0, 0, 0, 0), (0, 1, 1, 1), (1, 1, 1, 1), (1, 0, 0, 1)],
        )
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            {"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            config=self.make_config(line_follow_enabled=True),
            motion_adapter=fake_motion,
            sensor_adapter=fake_sensors,
            alarm_adapter=FakeAlarm(),
            gimbal_adapter=FakeGimbal(),
            detection_provider=fake_detection_provider,
        )

        runtime.run_continuous_patrol(max_iterations=3)

        self.assertGreaterEqual(fake_motion.calls.count(("strafe_left", 7, 0)), 2)
        self.assertIn(("move_forward", 7, 0), fake_motion.calls)

    def test_line_follow_uses_short_turn_for_sharp_bend(self) -> None:
        store = self.make_store()
        fake_motion = FakeMotion()
        fake_sensors = FakeSensors(
            distances=[400] * 6,
            tapes=[(0, 0, 0, 0), (1, 0, 1, 0)],
        )
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            {"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            config=self.make_config(line_follow_enabled=True),
            motion_adapter=fake_motion,
            sensor_adapter=fake_sensors,
            alarm_adapter=FakeAlarm(),
            gimbal_adapter=FakeGimbal(),
            detection_provider=fake_detection_provider,
        )

        runtime.run_continuous_patrol(max_iterations=1)

        self.assertIn(("rotate_right", 7, 0), fake_motion.calls)

    def test_b3_anchor_forbidden_zone_is_bypassed_not_counted(self) -> None:
        store = self.make_store()
        fake_motion = FakeMotion()
        fake_sensors = FakeSensors(
            distances=[400] * 24,
            tapes=[(0, 0, 0, 0), (1, 1, 1, 1)],
        )
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            {"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            config=self.make_config(),
            motion_adapter=fake_motion,
            sensor_adapter=fake_sensors,
            alarm_adapter=FakeAlarm(),
            gimbal_adapter=FakeGimbal(),
            detection_provider=fake_detection_provider,
        )
        runtime._record_observed_shelf("B3")

        self.assertEqual(runtime._handle_tape_boundary((0, 0, 0, 0)), "bypass")
        event_types = [event["type"] for event in store.snapshot()["events"]]

        self.assertIn("obstacle_avoidance_step", event_types)
        self.assertIn(("move_forward", 20, 0.0), fake_motion.calls)

    def test_forbidden_bypass_uses_shorter_default_body_distances(self) -> None:
        store = self.make_store()
        fake_motion = FakeMotion()
        fake_sensors = FakeSensors(
            distances=[120, 400, 400, 400, 400, 400, 400, 400, 400],
            tapes=[(1, 1, 1, 1)] * 4,
        )
        config = self.make_config()
        config.blocked_distance_mm = 160
        config.clear_distance_mm = 240
        config.blocked_samples = 1
        config.avoidance_body_seconds = 1.25
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            {"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            config=config,
            motion_adapter=fake_motion,
            sensor_adapter=fake_sensors,
            alarm_adapter=FakeAlarm(),
            gimbal_adapter=FakeGimbal(),
            detection_provider=fake_detection_provider,
        )

        runtime.run_patrol(shelf_order=["A1"], max_steps=1)

        self.assertEqual(fake_motion.calls.count(("move_forward", 20, 1.5)), 2)
        self.assertEqual(fake_motion.calls.count(("move_forward", 20, 1.25)), 1)

    def test_b3_forbidden_bypass_uses_extended_clearance_distances(self) -> None:
        store = self.make_store()
        fake_motion = FakeMotion()
        fake_sensors = FakeSensors(
            distances=[400] * 24,
            tapes=[(0, 0, 0, 0), (1, 1, 1, 1)],
        )
        config = self.make_config()
        config.avoidance_body_seconds = 1.0
        config.forbidden_avoidance_side_clearance_bodies = 1.5
        config.forbidden_avoidance_parallel_bodies = 1.2
        config.forbidden_avoidance_return_bodies = 1.5
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            {"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            config=config,
            motion_adapter=fake_motion,
            sensor_adapter=fake_sensors,
            alarm_adapter=FakeAlarm(),
            gimbal_adapter=FakeGimbal(),
            detection_provider=fake_detection_provider,
        )
        runtime._record_observed_shelf("B3")

        self.assertEqual(runtime._handle_tape_boundary((0, 0, 0, 0)), "bypass")

        self.assertEqual(fake_motion.calls.count(("move_forward", 20, 1.5)), 2)
        self.assertEqual(fake_motion.calls.count(("move_forward", 20, 1.2)), 1)

    def test_planned_boundary_turn_delegates_to_mpu6050_adapter_when_available(self) -> None:
        store = self.make_store()
        fake_motion = FakeMotion()
        fake_imu = FakeImuAdapter()
        fake_sensors = FakeSensors(distances=[400] * 4, tapes=[(0, 0, 0, 0), (1, 0, 0, 1)])
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            {"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            config=self.make_config(),
            motion_adapter=fake_motion,
            sensor_adapter=fake_sensors,
            alarm_adapter=FakeAlarm(),
            gimbal_adapter=FakeGimbal(),
            detection_provider=fake_detection_provider,
            imu_adapter=fake_imu,
        )

        runtime.run_continuous_patrol(max_iterations=1)

        self.assertEqual(fake_imu.calls, [("right", 30, 0.72)])
        self.assertNotIn("rotate_right", fake_motion.names())

    def test_failed_mpu6050_turn_does_not_fall_back_to_open_loop(self) -> None:
        store = self.make_store()
        fake_motion = FakeMotion()
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            {"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            config=self.make_config(),
            motion_adapter=fake_motion,
            sensor_adapter=FakeSensors(distances=[400] * 4, tapes=[(1, 1, 1, 1)] * 3),
            alarm_adapter=FakeAlarm(),
            gimbal_adapter=FakeGimbal(),
            detection_provider=fake_detection_provider,
            imu_adapter=FakeFailedImuAdapter(),
        )

        result = runtime.turn_90_closed_loop("right")

        self.assertIsNotNone(result)
        self.assertFalse(result["ok"])  # type: ignore[index]
        self.assertIn("failed to converge", result["message"])  # type: ignore[index]
        self.assertNotIn("rotate_right", fake_motion.names())
        self.assertEqual(store.snapshot()["task_status"], "ERROR")

    def test_motion_sensor_refresh_updates_state(self) -> None:
        store = self.make_store()
        sample = {
            "ok": True,
            "orientation_deg": {"roll": 0.0, "pitch": 0.0, "yaw": 12.5},
        }
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            {"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            config=self.make_config(),
            motion_adapter=FakeMotion(),
            sensor_adapter=FakeSensors(distances=[400], tapes=[(1, 1, 1, 1)]),
            alarm_adapter=FakeAlarm(),
            gimbal_adapter=FakeGimbal(),
            detection_provider=fake_detection_provider,
            imu_adapter=FakeMotionSensorImu(sample),
        )

        runtime.refresh_motion_sensor(force=True)

        self.assertEqual(store.snapshot()["motion_sensor"], sample)

    def test_runtime_refreshes_normal_led_during_patrol(self) -> None:
        store = self.make_store()
        fake_alarm = FakeAlarm()
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            {"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            config=self.make_config(),
            motion_adapter=FakeMotion(),
            sensor_adapter=FakeSensors(distances=[400] * 4, tapes=[(1, 1, 1, 1)] * 3),
            alarm_adapter=fake_alarm,
            gimbal_adapter=FakeGimbal(),
            detection_provider=fake_detection_provider,
        )

        runtime.run_continuous_patrol(max_iterations=1)

        self.assertIn("normal", fake_alarm.calls)

    def test_request_manual_override_clears_stop_event_for_manual_turn(self) -> None:
        """R1 fix: request_manual_override clears _stop_event so the IMU
        closed-loop 90° turn's should_abort callback returns False. Without
        this, manual turn_left_90 / turn_right_90 abort before motion."""

        store = self.make_store()
        fake_motion = FakeMotion()
        fake_imu = FakeImuTurnAbortTracker()
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            {"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            config=self.make_config(),
            motion_adapter=fake_motion,
            sensor_adapter=FakeSensors(distances=[], tapes=[]),
            alarm_adapter=FakeAlarm(),
            gimbal_adapter=FakeGimbal(),
            detection_provider=fake_detection_provider,
            imu_adapter=fake_imu,
        )
        # Simulate the old broken state: stop() set _stop_event.
        runtime._stop_event.set()

        runtime.request_manual_override()
        try:
            self.assertFalse(runtime._stop_event.is_set())
            runtime.turn_90_closed_loop("left")
        finally:
            runtime.release_manual_override()

        # The IMU turn's should_abort callback must have been polled and every
        # poll must be False — that is the R1 invariant.
        self.assertTrue(len(fake_imu.abort_traces) >= 1)
        for trace in fake_imu.abort_traces:
            self.assertTrue(trace, "should_abort was never polled")
            self.assertTrue(
                all(v is False for v in trace),
                f"should_abort returned True during manual turn: {trace}",
            )


class FakeMotion:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None, float | None]] = []

    def move_forward_slow(self, *, speed: int, duration_seconds: float) -> None:
        self.calls.append(("move_forward", speed, duration_seconds))

    def move_forward_corrected_slow(self, *, speed: int, correction: int, direction: str, duration_seconds: float) -> None:
        self.calls.append((f"move_forward_corrected:{direction}:{correction}", speed, duration_seconds))

    def move_backward_slow(self, *, speed: int, duration_seconds: float) -> None:
        self.calls.append(("move_backward", speed, duration_seconds))

    def strafe_left_slow(self, *, speed: int, duration_seconds: float) -> None:
        self.calls.append(("strafe_left", speed, duration_seconds))

    def strafe_right_slow(self, *, speed: int, duration_seconds: float) -> None:
        self.calls.append(("strafe_right", speed, duration_seconds))

    def rotate_left_slow(self, *, speed: int, duration_seconds: float) -> None:
        self.calls.append(("rotate_left", speed, duration_seconds))

    def rotate_right_slow(self, *, speed: int, duration_seconds: float) -> None:
        self.calls.append(("rotate_right", speed, duration_seconds))

    def stop(self) -> None:
        self.calls.append(("stop", None, None))

    def names(self) -> list[str]:
        return [name for name, _, _ in self.calls]


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
    def full_tape_boundary_detected(state: tuple[int, int, int, int] | None) -> bool:
        return sensors.full_tape_boundary_detected(state)

    @staticmethod
    def tape_boundary_count_detected(state: tuple[int, int, int, int] | None, min_black: int = 2) -> bool:
        return sensors.tape_boundary_count_detected(state, min_black=min_black)


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


class FakeImuAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, float]] = []

    def turn_90(self, direction: str, motion_adapter: FakeMotion, speed: int, fallback_seconds: float) -> bool:
        self.calls.append((direction, speed, fallback_seconds))
        motion_adapter.stop()
        return True


class FakeFailedImuAdapter:
    def turn_90_with_result(self, direction: str, motion_adapter: FakeMotion, speed: int, fallback_seconds: float) -> dict[str, object]:
        motion_adapter.stop()
        return {
            "ok": False,
            "source": "mpu6050",
            "direction": direction,
            "target_degrees": 90.0,
            "final_degrees": 82.0,
            "error_degrees": 8.0,
            "attempts": 6,
            "message": "MPU6050 turn failed to converge",
        }


class FakeMotionSensorImu:
    def __init__(self, sample: dict[str, object]) -> None:
        self.sample = sample

    def read_motion_sample(self) -> dict[str, object]:
        return dict(self.sample)


class FakeImuTurnAbortTracker:
    """Records the should_abort callback's value at every poll so tests can
    assert the manual turn did not self-abort (R1 regression check)."""

    def __init__(self) -> None:
        self.abort_traces: list[list[bool]] = []

    def turn_90_with_result(
        self,
        direction: str,
        motion_adapter: FakeMotion,
        speed: int,
        fallback_seconds: float,
        *,
        should_abort=None,
    ) -> dict[str, object]:
        trace: list[bool] = []
        if callable(should_abort):
            for _ in range(3):
                trace.append(bool(should_abort()))
        self.abort_traces.append(trace)
        motion_adapter.stop()
        return {
            "ok": True,
            "source": "fake_imu",
            "direction": direction,
            "target_degrees": 90.0,
            "final_degrees": 90.0,
            "error_degrees": 0.0,
            "attempts": 1,
            "message": "converged",
        }


def fake_detection_provider(**_: object) -> Iterator[dict[str, object]]:
    yield {"tag_id": "118", "kind": "shelf", "shelf_id": "A1", "marker_family": "TAG36H11", "ocr_text": "A1"}


if __name__ == "__main__":
    unittest.main()
