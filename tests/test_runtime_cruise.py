from __future__ import annotations

import sys
import tempfile
import time
import unittest
from unittest import mock
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

    def move_forward_corrected_slow(self, **kwargs: object) -> None:
        self.calls.append(f"move_forward_corrected:{kwargs.get('direction')}")

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
        self.update_count = 0
        self.last_sample_at: float | None = None

    def update(self) -> float:
        self.update_count += 1
        # Mirror the real StraightHeadingGuard: every successful update stamps
        # last_sample_at so the freshness gate in _apply_heading_hold can tell
        # whether a new I2C sample is available.
        self.last_sample_at = time.monotonic()
        return self.deviation

    def reset(self) -> None:
        self.reset_count += 1
        self.last_sample_at = None


class FakeImu:
    def __init__(self, guard: FakeGuard | None) -> None:
        self.guard = guard
        self.turn_calls: list[tuple[str, int, float, list[bool]]] = []

    def open_straight_heading_guard(self) -> FakeGuard | None:
        return self.guard

    def turn_90_with_result(
        self,
        direction: str,
        motion_adapter: FakeMotion,
        speed: int,
        fallback_seconds: float,
        *,
        should_abort=None,
    ) -> dict[str, object]:
        # Record the should_abort callback's value at every poll so tests can
        # assert the manual turn did not self-abort (R1 regression check).
        abort_trace: list[bool] = []
        if callable(should_abort):
            for _ in range(3):
                abort_trace.append(bool(should_abort()))
        self.turn_calls.append((direction, speed, fallback_seconds, abort_trace))
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
                object_trigger_enabled=False,
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

    def test_cruise_stops_for_opencv_object_presence_scan(self) -> None:
        runtime, motion, _ = self.make_runtime(
            config=RobotRuntimeConfig(
                smooth_cruise_enabled=True,
                cruise_vision_enabled=False,
                object_trigger_enabled=True,
                cruise_tick_seconds=0,
                cruise_log_interval_seconds=0,
                action_settle_seconds=0,
            ),
            imu=FakeImu(None),
        )

        with mock.patch(
            "inspection_robot.runtime.tag_detector.detect_object_presence_from_camera",
            return_value=True,
        ):
            runtime.run_continuous_patrol(max_iterations=1)

        self.assertIn("stop", motion.calls)
        self.assertNotIn("move_forward", motion.calls)
        self.assertIn("vision_scan_start", {
            event.get("evidence", {}).get("stage")
            for event in runtime.store.snapshot()["events"]
            if event["type"] == "motion_debug" and isinstance(event.get("evidence"), dict)
        })

    def test_object_presence_cooldown_starts_after_scan_finishes(self) -> None:
        runtime, _, _ = self.make_runtime(
            config=RobotRuntimeConfig(
                smooth_cruise_enabled=True,
                object_trigger_enabled=True,
                object_presence_cooldown_seconds=1.5,
                object_settle_seconds=0,
                action_settle_seconds=0,
            ),
            imu=FakeImu(None),
        )

        with (
            mock.patch("inspection_robot.runtime.time.monotonic", side_effect=[100.0, 103.0, 103.0, 103.1, 103.1]),
            mock.patch("inspection_robot.runtime.tag_detector.detect_object_presence_from_camera", return_value=True) as detector,
            mock.patch.object(runtime, "_scan_visible_shelf") as scan,
        ):
            self.assertTrue(runtime._maybe_scan_for_object_presence())
            self.assertFalse(runtime._maybe_scan_for_object_presence())

        self.assertEqual(detector.call_count, 1)
        self.assertEqual(scan.call_count, 1)

    def test_cruise_recognition_stops_for_parked_scan(self) -> None:
        runtime, motion, _ = self.make_runtime(
            config=RobotRuntimeConfig(
                smooth_cruise_enabled=True,
                cruise_vision_enabled=True,
                object_trigger_enabled=True,
                object_settle_seconds=0,
                action_settle_seconds=0,
            ),
            imu=FakeImu(None),
        )
        scanner = _CruiseVisionScanner(
            provider=lambda **_: iter(()),
            config=runtime.config,
            enrich=runtime._enrich_detection,
            stop_event=runtime._stop_event,
        )
        scanner._pending = [{"tag_id": "118", "kind": "shelf", "shelf_id": "A1"}]
        runtime._cruise_scanner = scanner

        with mock.patch.object(runtime, "_perform_scan") as scan:
            self.assertTrue(runtime._handle_cruise_recognitions())

        self.assertIn("stop", motion.calls)
        scan.assert_called_once_with("A1", "A1_SCAN")

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
                heading_hold_confirm_samples=1,
                action_settle_seconds=0,
            ),
            imu=FakeImu(guard),
        )

        runtime._apply_heading_hold()

        self.assertNotIn("rotate_right", motion.calls)
        self.assertNotIn("rotate_left", motion.calls)

    def test_heading_hold_corrects_right_when_positive_deviation_worsens(self) -> None:
        guard = FakeGuard(deviation=6.0, rate=10.0)
        runtime, motion, _ = self.make_runtime(
            config=RobotRuntimeConfig(
                heading_hold_enabled=True,
                heading_hold_tolerance_deg=3.0,
                heading_hold_rate_damping=0.5,
                heading_hold_confirm_samples=1,
                action_settle_seconds=0,
            ),
            imu=FakeImu(guard),
        )

        runtime._apply_heading_hold()

        self.assertIn("move_forward_corrected:right", motion.calls)
        self.assertNotIn("rotate_left", motion.calls)
        self.assertNotIn("rotate_right", motion.calls)

    def test_heading_hold_caps_correction_to_forward_speed_fraction(self) -> None:
        guard = FakeGuard(deviation=50.0, rate=0.0)
        runtime, motion, _ = self.make_runtime(
            config=RobotRuntimeConfig(
                heading_hold_enabled=True,
                heading_hold_tolerance_deg=3.0,
                heading_hold_correction_speed=35,
                heading_hold_speed_gain=5.0,
                heading_hold_min_interval_seconds=0.0,
                heading_hold_confirm_samples=1,
                cruise_speed=20,
                action_settle_seconds=0,
            ),
            imu=FakeImu(guard),
        )

        runtime._apply_heading_hold()

        self.assertIn("move_forward_corrected:right", motion.calls)
        correction_events = [
            event
            for event in runtime.store.snapshot()["events"]
            if event["type"] == "motion_debug" and event.get("evidence", {}).get("stage") == "heading_hold_correction"
        ]
        self.assertEqual(correction_events[-1]["evidence"]["correction_speed"], 12)

    def test_heading_hold_throttles_back_to_back_corrections(self) -> None:
        guard = FakeGuard(deviation=8.0, rate=0.0)
        runtime, motion, _ = self.make_runtime(
            config=RobotRuntimeConfig(
                heading_hold_enabled=True,
                heading_hold_tolerance_deg=3.0,
                heading_hold_min_interval_seconds=1.0,
                heading_hold_confirm_samples=1,
                heading_hold_trace_interval_seconds=0,
                action_settle_seconds=0,
            ),
            imu=FakeImu(guard),
        )

        runtime._apply_heading_hold()
        motion.calls.clear()
        guard.last_sample_at = None
        runtime._apply_heading_hold()

        self.assertNotIn("move_forward_corrected:right", motion.calls)
        self.assertTrue(
            any(
                event["type"] == "motion_debug"
                and event.get("evidence", {}).get("stage") == "heading_hold_sample"
                and event.get("evidence", {}).get("reason") == "correction_throttled"
                for event in runtime.store.snapshot()["events"]
            )
        )

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

        runtime._handle_moving_recognition({"shelf_id": "A1", "tag_id": "118"})
        # Same target still in frame on the next tick -> cooldown suppresses it.
        runtime._handle_moving_recognition({"shelf_id": "A1", "tag_id": "118"})

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

    def test_moving_items_are_committed_to_previous_shelf_when_next_shelf_is_seen(self) -> None:
        runtime, _, _ = self.make_runtime(
            config=RobotRuntimeConfig(
                smooth_cruise_enabled=True,
                cruise_recognition_cooldown_seconds=0,
                action_settle_seconds=0,
            ),
        )

        runtime._handle_moving_recognition({"shelf_id": "A1", "tag_id": "118"})
        runtime._handle_moving_recognition({"item_id": "item_07", "tag_id": "7"})
        runtime._handle_moving_recognition({"item_id": "item_08", "tag_id": "8"})
        runtime._handle_moving_recognition({"shelf_id": "A2", "tag_id": "110"})

        snapshot = runtime.store.snapshot()
        shelf = next(item for item in snapshot["shelves"] if item["shelf_id"] == "A1")
        item_ids = {item["item_id"] for item in shelf["items"]}

        self.assertEqual(snapshot["scan"]["shelf_id"], "A1")
        self.assertEqual(item_ids, {"item_07", "item_08"})
        self.assertFalse(any(event["shelf_id"] == "A2" and event["item"] in {"书本", "衣服"} for event in snapshot["events"]))

    def test_moving_shelf_tag_identity_overrides_stale_item_payload(self) -> None:
        runtime, _, _ = self.make_runtime(
            config=RobotRuntimeConfig(
                smooth_cruise_enabled=True,
                cruise_recognition_cooldown_seconds=0,
                action_settle_seconds=0,
            ),
        )

        runtime._handle_moving_recognition({"tag_id": "118"})
        runtime._handle_moving_recognition({"item_id": "item_07", "tag_id": "7"})
        runtime._handle_moving_recognition({"tag_id": "110", "item_id": "item_07"})

        snapshot = runtime.store.snapshot()
        shelf_a1 = next(item for item in snapshot["shelves"] if item["shelf_id"] == "A1")
        shelf_a2 = next(item for item in snapshot["shelves"] if item["shelf_id"] == "A2")

        self.assertEqual(snapshot["scan"]["shelf_id"], "A1")
        self.assertEqual([item["item_id"] for item in shelf_a1["items"]], ["item_07"])
        self.assertEqual(shelf_a2["items"], [])
        self.assertEqual(snapshot["current_shelf"], "A2")

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
            {"tag_id": "118", "kind": "shelf", "shelf_id": "A1"},
            {"tag_id": "999"},  # unresolved -> ignored
            {"tag_id": "7", "kind": "item", "item_id": "item_07"},
        ]

        def provider(**_: object) -> Iterator[dict[str, object]]:
            yield from detections

        runtime, _, _ = self.make_runtime(config=RobotRuntimeConfig(smooth_cruise_enabled=True, object_trigger_enabled=False))
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

    def test_cruise_scanner_pauses_during_row_transfer(self) -> None:
        runtime, _, _ = self.make_runtime(
            config=RobotRuntimeConfig(smooth_cruise_enabled=True, cruise_vision_enabled=True, object_trigger_enabled=False)
        )

        runtime._record_observed_shelf("A4")
        self.assertFalse(runtime._cruise_scanner_allowed_for_phase())

        runtime._record_observed_shelf("B3")
        self.assertTrue(runtime._cruise_scanner_allowed_for_phase())

    def test_row_transfer_suppresses_vision_until_next_boundary_turn(self) -> None:
        runtime, _, _ = self.make_runtime(
            config=RobotRuntimeConfig(
                smooth_cruise_enabled=True,
                cruise_vision_enabled=True,
                object_trigger_enabled=False,
                action_settle_seconds=0,
            )
        )

        runtime._record_observed_shelf("A4")
        runtime._handle_moving_recognition({"shelf_id": "B3", "tag_id": "103"})
        self.assertEqual(runtime._last_shelf_anchor, "A4")
        self.assertFalse(runtime._cruise_scanner_allowed_for_phase())

        runtime._handle_planned_boundary_turn((0, 1, 1, 1), "turn_patrol")

        self.assertTrue(runtime._cruise_scanner_allowed_for_phase())

    def test_heading_hold_runs_even_when_boundary_watch_is_disabled(self) -> None:
        guard = FakeGuard(deviation=2.0, rate=0.0)
        runtime, motion, _ = self.make_runtime(
            config=RobotRuntimeConfig(
                heading_hold_enabled=True,
                heading_hold_tolerance_deg=0.25,
                heading_hold_trace_interval_seconds=0,
                action_settle_seconds=0,
            ),
            imu=FakeImu(guard),
        )

        runtime._run_timed_motion(
            motion.move_forward_slow,
            speed=30,
            duration_seconds=0,
            watch_boundary=False,
            heading_hold=True,
        )

        self.assertIn("move_forward_corrected:right", motion.calls)

    def test_heading_hold_logs_sample_when_inside_deadband(self) -> None:
        guard = FakeGuard(deviation=0.0, rate=0.0)
        runtime, _, _ = self.make_runtime(
            config=RobotRuntimeConfig(
                smooth_cruise_enabled=True,
                heading_hold_enabled=True,
                heading_hold_tolerance_deg=0.25,
                heading_hold_trace_interval_seconds=0,
                cruise_tick_seconds=0,
                action_settle_seconds=0,
            ),
            imu=FakeImu(guard),
        )

        runtime._drive_cruise_step((1, 1, 1, 1))

        snapshot = runtime.store.snapshot()
        self.assertTrue(
            any(
                event["type"] == "motion_debug"
                and event.get("evidence", {}).get("stage") == "heading_hold_sample"
                and event.get("evidence", {}).get("reason") == "within_deadband"
                for event in snapshot["events"]
            )
        )

    # --- R1/R3/R4/R5 regression tests ------------------------------------ #

    def test_cruise_deadband_pure_forward(self) -> None:
        """Inside the heading-hold deadband the cruise tick must issue only
        move_forward — never rotate_* and never an unexpected stop. R3 root
        cause was that heading hold fired on every tick and its rotate_*
        blended with the still-energised forward axis into an arc."""

        guard = FakeGuard(deviation=0.0)
        runtime, motion, _ = self.make_runtime(
            config=RobotRuntimeConfig(
                smooth_cruise_enabled=True,
                heading_hold_enabled=True,
                heading_hold_tolerance_deg=3.0,
                cruise_tick_seconds=0,
                cruise_log_interval_seconds=0,
                action_settle_seconds=0,
            ),
            imu=FakeImu(guard),
        )

        for _ in range(5):
            runtime._drive_cruise_step((1, 1, 1, 1))

        self.assertTrue(motion.calls.count("move_forward") >= 5)
        self.assertNotIn("rotate_left", motion.calls)
        self.assertNotIn("rotate_right", motion.calls)
        # keep_running=True ⇒ no trailing stop between ticks.
        self.assertNotIn("stop", motion.calls)

    def test_cruise_no_new_imu_sample_no_repeat_correction(self) -> None:
        """If the guard's last_sample_at is too recent (no new I2C sample),
        _apply_heading_hold must skip entirely — R4 freshness gate."""

        guard = FakeGuard(deviation=6.0, rate=0.0)
        runtime, motion, _ = self.make_runtime(
            config=RobotRuntimeConfig(
                heading_hold_enabled=True,
                heading_hold_tolerance_deg=3.0,
                heading_hold_min_sample_interval_seconds=0.5,
                heading_hold_min_interval_seconds=0.0,
                heading_hold_confirm_samples=1,
                action_settle_seconds=0,
            ),
            imu=FakeImu(guard),
        )
        # Simulate: the guard just produced a sample, so it's too fresh.
        guard.last_sample_at = time.monotonic()

        runtime._apply_heading_hold()

        self.assertNotIn("rotate_left", motion.calls)
        self.assertNotIn("rotate_right", motion.calls)
        self.assertEqual(guard.update_count, 0)

    def test_cruise_correction_then_straight_uses_plain_forward(self) -> None:
        """After one heading-hold correction, returning to the deadband makes the
        next tick a plain move_forward with no rotate_* residue."""

        guard = FakeGuard(deviation=6.0, rate=0.0)
        runtime, motion, _ = self.make_runtime(
            config=RobotRuntimeConfig(
                smooth_cruise_enabled=True,
                heading_hold_enabled=True,
                heading_hold_tolerance_deg=3.0,
                heading_hold_min_interval_seconds=0.0,
                heading_hold_max_consecutive=5,
                heading_hold_confirm_samples=1,
                cruise_tick_seconds=0,
                cruise_log_interval_seconds=0,
                action_settle_seconds=0,
            ),
            imu=FakeImu(guard),
        )

        runtime._drive_cruise_step((1, 1, 1, 1))
        self.assertIn("move_forward_corrected:right", motion.calls)
        self.assertEqual(guard.reset_count, 0)

        motion.calls.clear()
        guard.deviation = 0.0  # back inside the deadband
        runtime._drive_cruise_step((1, 1, 1, 1))

        self.assertIn("move_forward", motion.calls)
        self.assertNotIn("rotate_left", motion.calls)
        self.assertNotIn("rotate_right", motion.calls)

    def test_cruise_correction_sign_convention(self) -> None:
        """Positive deviation ⇒ right correction by default; invert flips it left.
        Positive rate (worsening) with damping keeps the same sign, so the
        correction still fires."""

        # Positive deviation, no rate → right correction with chip-up MPU6050.
        guard = FakeGuard(deviation=6.0, rate=0.0)
        runtime, motion, _ = self.make_runtime(
            config=RobotRuntimeConfig(
                heading_hold_enabled=True,
                heading_hold_tolerance_deg=3.0,
                heading_hold_rate_damping=0.0,
                heading_hold_min_interval_seconds=0.0,
                heading_hold_max_consecutive=5,
                heading_hold_confirm_samples=1,
                action_settle_seconds=0,
            ),
            imu=FakeImu(guard),
        )
        runtime._apply_heading_hold()
        self.assertIn("move_forward_corrected:right", motion.calls)
        self.assertNotIn("move_forward_corrected:left", motion.calls)

        # Enabling invert flips direction for field override/calibration.
        guard2 = FakeGuard(deviation=6.0, rate=0.0)
        runtime2, motion2, _ = self.make_runtime(
            config=RobotRuntimeConfig(
                heading_hold_enabled=True,
                heading_hold_tolerance_deg=3.0,
                heading_hold_rate_damping=0.0,
                heading_hold_invert=True,
                heading_hold_min_interval_seconds=0.0,
                heading_hold_max_consecutive=5,
                heading_hold_confirm_samples=1,
                action_settle_seconds=0,
            ),
            imu=FakeImu(guard2),
        )
        runtime2._apply_heading_hold()
        self.assertIn("move_forward_corrected:left", motion2.calls)
        self.assertNotIn("move_forward_corrected:right", motion2.calls)

        # Negative deviation → left correction by default.
        guard3 = FakeGuard(deviation=-6.0, rate=0.0)
        runtime3, motion3, _ = self.make_runtime(
            config=RobotRuntimeConfig(
                heading_hold_enabled=True,
                heading_hold_tolerance_deg=3.0,
                heading_hold_rate_damping=0.0,
                heading_hold_min_interval_seconds=0.0,
                heading_hold_max_consecutive=5,
                heading_hold_confirm_samples=1,
                action_settle_seconds=0,
            ),
            imu=FakeImu(guard3),
        )
        runtime3._apply_heading_hold()
        self.assertIn("move_forward_corrected:left", motion3.calls)
        self.assertNotIn("move_forward_corrected:right", motion3.calls)

    def test_heading_hold_default_waits_for_confirmed_deviation(self) -> None:
        guard = FakeGuard(deviation=7.0, rate=0.0)
        runtime, motion, _ = self.make_runtime(
            config=RobotRuntimeConfig(
                heading_hold_enabled=True,
                heading_hold_confirm_samples=2,
                heading_hold_min_interval_seconds=0.0,
                action_settle_seconds=0,
            ),
            imu=FakeImu(guard),
        )

        runtime._apply_heading_hold()
        self.assertNotIn("rotate_left", motion.calls)
        self.assertNotIn("rotate_right", motion.calls)

        guard.last_sample_at = None
        runtime._apply_heading_hold()
        self.assertIn("move_forward_corrected:right", motion.calls)

    def test_cruise_keep_running_rewrites_four_wheels(self) -> None:
        """keep_running=True skips the trailing stop but every chunk still
        calls move_forward so the vendor library rewrites all four wheels.
        This is the invariant that prevents the 'some wheels stop, others
        keep going' failure mode."""

        runtime, motion, _ = self.make_runtime(
            config=RobotRuntimeConfig(
                smooth_cruise_enabled=True,
                cruise_tick_seconds=0.1,
                motion_guard_poll_seconds=0.03,
                action_settle_seconds=0,
            ),
            imu=FakeImu(None),  # no heading guard → pure forward
        )

        runtime._run_timed_motion(
            motion.move_forward_slow,
            speed=runtime.config.cruise_speed,
            duration_seconds=runtime.config.cruise_tick_seconds,
            watch_boundary=True,  # required so the chunk loop runs
            heading_hold=False,
            keep_running=True,
        )

        # 0.1s / 0.03s ≈ 4 chunks → ≥3 move_forward calls.
        self.assertGreaterEqual(motion.calls.count("move_forward"), 3)
        self.assertNotIn("stop", motion.calls)

    # --- R1 manual override regression ---------------------------------- #

    def test_manual_override_takes_over_immediately(self) -> None:
        """request_manual_override clears _stop_event so the IMU closed-loop
        turn's should_abort callback returns False — R1 regression. Also the
        heading-hold consecutive counter is reset so a stale correction can't
        bleed into the manual command."""

        guard = FakeGuard(deviation=0.0)
        runtime, motion, _ = self.make_runtime(
            config=RobotRuntimeConfig(
                smooth_cruise_enabled=True,
                heading_hold_enabled=True,
                heading_hold_tolerance_deg=3.0,
                cruise_tick_seconds=0,
                action_settle_seconds=0,
            ),
            imu=FakeImu(guard),
        )
        # Simulate the old broken path: stop() set _stop_event and it was
        # never cleared.
        runtime._stop_event.set()

        runtime.request_manual_override()

        self.assertFalse(runtime._stop_event.is_set())
        # _manual_override stays armed during the manual command so the patrol
        # loop (if it were still alive) would skip cruise ticks; release clears it.
        self.assertTrue(runtime._manual_override.is_set())
        # The motion module's stop was called to park the chassis.
        self.assertIn("stop", motion.calls)

        motion.calls.clear()
        # Now the manual turn must not self-abort.
        result = runtime.turn_90_closed_loop("left")

        self.assertIsNotNone(result)
        self.assertTrue(result["ok"])  # type: ignore[index]
        imu = runtime.imu  # type: ignore[attr-defined]
        for _direction, _speed, _dur, abort_trace in imu.turn_calls:
            self.assertTrue(all(v is False for v in abort_trace),
                            f"should_abort returned True during manual turn: {abort_trace}")
        # And release clears the override flag.
        runtime.release_manual_override()
        self.assertFalse(runtime._manual_override.is_set())

    def test_cruise_resume_after_boundary_turn_clean(self) -> None:
        """After a boundary 90° turn the heading guard is reset (R2) and the
        first cruise tick afterwards is pure move_forward — no rotate_*
        residue from the turn."""

        guard = FakeGuard(deviation=0.0)
        runtime, motion, _ = self.make_runtime(
            config=RobotRuntimeConfig(
                smooth_cruise_enabled=True,
                heading_hold_enabled=True,
                heading_hold_tolerance_deg=3.0,
                cruise_tick_seconds=0,
                cruise_log_interval_seconds=0,
                action_settle_seconds=0,
                boundary_cooldown_seconds=0,
            ),
            imu=FakeImu(guard),
        )

        # Simulate the boundary turn: _reset_heading_guard + _zupt_recalibrate.
        runtime._reset_heading_guard()
        runtime._drive_cruise_step((1, 1, 1, 1))

        self.assertIn("move_forward", motion.calls)
        self.assertNotIn("rotate_left", motion.calls)
        self.assertNotIn("rotate_right", motion.calls)

    def test_manual_turn_left_right_regression(self) -> None:
        """Regression for ba52c92: manual turn_left_90 / turn_right_90 must
        actually pulse the IMU turn — should_abort must be False throughout.
        This is the test that R1 broke: the old runtime.stop() left
        _stop_event set, so imu_turn aborted before motion."""

        guard = FakeGuard(deviation=0.0)
        runtime, _, _ = self.make_runtime(
            config=RobotRuntimeConfig(
                heading_hold_enabled=True,
                heading_hold_tolerance_deg=3.0,
                turn_speed=22,
                turn_90_seconds=0.85,
                action_settle_seconds=0,
            ),
            imu=FakeImu(guard),
        )

        for direction in ("left", "right"):
            runtime.request_manual_override()
            try:
                result = runtime.turn_90_closed_loop(direction)
            finally:
                runtime.release_manual_override()

            self.assertIsNotNone(result)
            self.assertTrue(result["ok"])  # type: ignore[index]
            self.assertEqual(result["direction"], direction)  # type: ignore[index]

        imu = runtime.imu  # type: ignore[attr-defined]
        self.assertEqual(len(imu.turn_calls), 2)
        for direction, _speed, _dur, abort_trace in imu.turn_calls:
            self.assertTrue(abort_trace, "should_abort was never polled")
            self.assertTrue(
                all(v is False for v in abort_trace),
                f"should_abort returned True during {direction} turn: {abort_trace}",
            )


    # --- heading polarity self-check ------------------------------------ #

    def test_heading_polarity_selfcheck_passes_and_logs(self) -> None:
        runtime, _, _ = self.make_runtime(
            config=RobotRuntimeConfig(heading_hold_enabled=True, action_settle_seconds=0),
            imu=FakeImu(FakeGuard(deviation=0.0)),
        )
        # Left rotation → +12° (gyro ok), right differential → -9° (corrective).
        with mock.patch.object(runtime, "_integrate_guard_yaw", side_effect=[12.0, -9.0]):
            result = runtime.run_heading_polarity_selfcheck(seconds=0)

        self.assertTrue(result["ok"])
        self.assertEqual(result["recommended_yaw_sign"], 1)
        self.assertFalse(result["recommended_invert"])
        self.assertTrue(
            any(
                event["type"] == "motion_debug"
                and event.get("evidence", {}).get("stage") == "heading_polarity_selfcheck"
                for event in runtime.store.snapshot()["events"]
            )
        )

    def test_heading_polarity_selfcheck_flags_inverted_differential(self) -> None:
        runtime, _, _ = self.make_runtime(
            config=RobotRuntimeConfig(heading_hold_enabled=True, action_settle_seconds=0),
            imu=FakeImu(FakeGuard(deviation=0.0)),
        )
        # Left rotation → +12° (gyro ok), but right differential → +9° (car went
        # left when told right) ⇒ divergent ⇒ recommend HEADING_HOLD_INVERT.
        with mock.patch.object(runtime, "_integrate_guard_yaw", side_effect=[12.0, 9.0]):
            result = runtime.run_heading_polarity_selfcheck(seconds=0)

        self.assertFalse(result["ok"])
        self.assertTrue(result["recommended_invert"])

    def test_heading_polarity_selfcheck_requires_heading_hold(self) -> None:
        runtime, _, _ = self.make_runtime(
            config=RobotRuntimeConfig(heading_hold_enabled=False),
            imu=FakeImu(FakeGuard(deviation=0.0)),
        )
        result = runtime.run_heading_polarity_selfcheck()
        self.assertFalse(result["ok"])
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
