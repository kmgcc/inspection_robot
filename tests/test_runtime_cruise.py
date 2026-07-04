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
from inspection_robot.runtime import RobotRuntime, RobotRuntimeConfig, _CruiseVisionScanner


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
    def __init__(self, tape: tuple[int, int, int, int] = (1, 1, 1, 1)) -> None:
        self.tape = tape

    def read_distance_mm(self) -> int | None:
        return 400

    def read_tape_boundary(self) -> tuple[int, int, int, int] | None:
        return self.tape


class FakeAlarm:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def show_normal(self) -> None:
        self.calls.append("normal")

    def show_line_follow(self) -> None:
        self.calls.append("line_follow")

    def show_obstacle_wait(self) -> None:
        self.calls.append("obstacle_wait")

    def show_warning(self) -> None:
        self.calls.append("warning")

    def show_high_priority_alarm(self) -> None:
        self.calls.append("high_priority_alarm")

    def show_recognition(self) -> None:
        self.calls.append("recognition")

    def clear_alarm(self) -> None:
        self.calls.append("clear")


class FakeGuard:
    def __init__(self, deviation: float, rate: float = 0.0) -> None:
        self.deviation = deviation
        self.last_rate_dps = rate
        self.reset_count = 0

    def update(self) -> float:
        return self.deviation

    def reset(self) -> None:
        self.reset_count += 1


class FakeImu:
    def __init__(self, guard: FakeGuard | None) -> None:
        self.guard = guard

    def open_straight_heading_guard(self) -> FakeGuard | None:
        return self.guard


def no_detection_provider(**_: object) -> Iterator[dict[str, object]]:
    return
    yield {}


class CruiseRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def make_runtime(
        self,
        *,
        config: RobotRuntimeConfig,
        motion: FakeMotion | None = None,
        alarm: FakeAlarm | None = None,
        imu: FakeImu | None = None,
    ) -> tuple[RobotRuntime, FakeMotion, FakeAlarm]:
        store = InspectionStore(
            DEFAULT_TAG_MAP,
            warehouse_map=DEFAULT_WAREHOUSE_MAP,
            shelf_manifest={"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            root=self.root,
        )
        fake_motion = motion or FakeMotion()
        fake_alarm = alarm or FakeAlarm()
        runtime = RobotRuntime(
            store,
            DEFAULT_WAREHOUSE_MAP,
            {"A1": DEFAULT_SHELF_MANIFEST["A1"]},
            config=config,
            motion_adapter=fake_motion,
            sensor_adapter=FakeSensors(),
            alarm_adapter=fake_alarm,
            imu_adapter=imu or FakeImu(None),
            detection_provider=no_detection_provider,
        )
        return runtime, fake_motion, fake_alarm

    # --- constant-velocity cruise ---------------------------------------- #

    def test_cruise_step_keeps_motor_running_without_per_step_stop(self) -> None:
        runtime, motion, _ = self.make_runtime(
            config=RobotRuntimeConfig(
                smooth_cruise_enabled=True,
                cruise_tick_seconds=0,
                action_settle_seconds=0,
            ),
            imu=FakeImu(None),  # no heading guard -> no correction pulse
        )

        runtime._drive_cruise_step((1, 1, 1, 1))

        self.assertIn("move_forward", motion.calls)
        # The whole point of cruise: no stop between ticks (that is the jerk).
        self.assertNotIn("stop", motion.calls)

    def test_continuous_patrol_runs_cruise_without_parked_scan(self) -> None:
        runtime, motion, _ = self.make_runtime(
            config=RobotRuntimeConfig(
                smooth_cruise_enabled=True,
                cruise_vision_enabled=False,  # keep the scanner thread out of the test
                cruise_tick_seconds=0,
                cruise_log_interval_seconds=0,
                scan_timeout_seconds=0,
                action_settle_seconds=0,
                boundary_cooldown_seconds=0,
            ),
            imu=FakeImu(None),
        )

        runtime.run_continuous_patrol(max_iterations=2)
        stages = {
            event["evidence"].get("stage")
            for event in runtime.store.snapshot()["events"]
            if event["type"] == "motion_debug" and isinstance(event.get("evidence"), dict)
        }

        self.assertGreaterEqual(motion.calls.count("move_forward"), 2)
        self.assertIn("cruise_step", stages)
        # Cruise never parks to scan, so the parked-scan pipeline must not run.
        self.assertNotIn("vision_scan_start", stages)

    # --- anti-oscillation PD heading hold -------------------------------- #

    def test_heading_hold_skips_pulse_when_already_recovering(self) -> None:
        # Deviation is past tolerance, but the yaw rate is strongly returning to
        # straight; damping flips the sign so no correction is issued (this is the
        # guard against 越纠越弯 / over-correction).
        guard = FakeGuard(deviation=6.0, rate=-10.0)
        runtime, motion, _ = self.make_runtime(
            config=RobotRuntimeConfig(
                heading_hold_enabled=True,
                heading_hold_tolerance_deg=3.0,
                heading_hold_rate_damping=1.0,
                action_settle_seconds=0,
            ),
            imu=FakeImu(guard),
        )

        runtime._apply_heading_hold()

        self.assertNotIn("rotate_right", motion.calls)
        self.assertNotIn("rotate_left", motion.calls)

    def test_heading_hold_pulses_when_deviation_worsening(self) -> None:
        guard = FakeGuard(deviation=6.0, rate=10.0)
        runtime, motion, _ = self.make_runtime(
            config=RobotRuntimeConfig(
                heading_hold_enabled=True,
                heading_hold_tolerance_deg=3.0,
                heading_hold_rate_damping=0.5,
                action_settle_seconds=0,
            ),
            imu=FakeImu(guard),
        )

        runtime._apply_heading_hold()

        self.assertIn("rotate_right", motion.calls)

    # --- moving recognition + orange flash ------------------------------- #

    def test_moving_recognition_flashes_orange_records_and_cues(self) -> None:
        runtime, _, alarm = self.make_runtime(
            config=RobotRuntimeConfig(
                smooth_cruise_enabled=True,
                cruise_recognition_flash_seconds=5.0,
                cruise_recognition_cooldown_seconds=10.0,
                action_settle_seconds=0,
            ),
        )

        runtime._handle_moving_recognition({"shelf_id": "A1", "tag_id": "101"})
        # Same target still in frame on the next tick -> cooldown suppresses it.
        runtime._handle_moving_recognition({"shelf_id": "A1", "tag_id": "101"})

        snapshot = runtime.store.snapshot()
        self.assertEqual(alarm.calls.count("recognition"), 1)
        self.assertTrue(
            any(
                event["type"] == "motion_debug" and event.get("evidence", {}).get("stage") == "cruise_recognition"
                for event in snapshot["events"]
            )
        )
        self.assertEqual(snapshot["audio"]["last_cue"], "first")

    def test_item_recognition_uses_following_cue(self) -> None:
        runtime, _, alarm = self.make_runtime(
            config=RobotRuntimeConfig(
                smooth_cruise_enabled=True,
                cruise_recognition_flash_seconds=5.0,
                action_settle_seconds=0,
            ),
        )

        runtime._handle_moving_recognition({"item_id": "item_07", "tag_id": "7"})

        self.assertIn("recognition", alarm.calls)
        self.assertEqual(runtime.store.snapshot()["audio"]["last_cue"], "following")

    def test_indicator_light_holds_orange_during_flash_window(self) -> None:
        runtime, _, alarm = self.make_runtime(
            config=RobotRuntimeConfig(
                smooth_cruise_enabled=True,
                cruise_recognition_flash_seconds=5.0,
                action_settle_seconds=0,
            ),
        )

        runtime._trigger_orange_flash()
        alarm.calls.clear()
        runtime._update_indicator_light()  # inside flash window -> keep orange
        self.assertNotIn("normal", alarm.calls)

        runtime._orange_flash_until = 0.0
        runtime._update_indicator_light()  # window elapsed -> restore base colour
        self.assertIn("normal", alarm.calls)

    # --- background scanner --------------------------------------------- #

    def test_cruise_vision_scanner_queues_resolved_recognitions(self) -> None:
        detections = [
            {"tag_id": "101", "kind": "shelf", "shelf_id": "A1"},
            {"tag_id": "999"},  # unresolved -> ignored
            {"tag_id": "7", "kind": "item", "item_id": "item_07"},
        ]

        def provider(**_: object) -> Iterator[dict[str, object]]:
            yield from detections

        runtime, _, _ = self.make_runtime(config=RobotRuntimeConfig(smooth_cruise_enabled=True))
        scanner = _CruiseVisionScanner(
            provider=provider,
            config=runtime.config,
            enrich=runtime._enrich_detection,
            stop_event=runtime._stop_event,
        )

        scanner._ingest(provider())
        pending = scanner.poll_new()

        keys = {(rec["shelf_id"], rec["item_id"]) for rec in pending}
        self.assertIn(("A1", None), keys)
        self.assertIn((None, "item_07"), keys)
        self.assertEqual(len(pending), 2)
        self.assertEqual(scanner.poll_new(), [])  # drained


if __name__ == "__main__":
    unittest.main()
