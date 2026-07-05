from __future__ import annotations

import itertools
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from .audio import start_audio_cue, start_spoken_message
from .config import ShelfManifest, WarehouseMap
from .core.planner import PlanningError, RouteStep, plan_patrol_route
from .core.events import EventRecord
from .core.store import InspectionStore
from .robot import alarm, gimbal, motion, mpu6050, sensors
from .robot.heading_hold import HeadingHoldCorrection, HeadingHoldSettings, compute_heading_hold_correction
from .robot.line_following import decide_line_follow_motion
from .robot.sensors import RobotHardwareError
from .vision import tag_detector
from .vision.tag_detector import VisionDependencyError


Cell = tuple[int, int]
DetectionProvider = Callable[..., Iterator[Mapping[str, object]]]
HEADINGS = ["N", "E", "S", "W"]
logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _env_value(names: tuple[str, ...], default: str) -> str:
    for name in names:
        raw = os.environ.get(name)
        if raw is not None:
            return raw
    return default


def _env_int(default: int, *names: str) -> int:
    return int(_env_value(names, str(default)))


DEFAULT_MIN_RUNNING_SPEED = max(0, _env_int(5, "ROBOT_MIN_SPEED"))


def _env_float(default: float, *names: str) -> float:
    return float(_env_value(names, str(default)))


def _env_optional_int(*names: str) -> int | None:
    raw = _env_value(names, "").strip()
    if not raw:
        return None
    return int(raw)


def _env_text(default: str, *names: str) -> str:
    return _env_value(names, default)


@dataclass(slots=True)
class RobotRuntimeConfig:
    blocked_distance_mm: int = field(default_factory=lambda: _env_int(100, "BLOCKED_DISTANCE_MM"))
    clear_distance_mm: int = field(default_factory=lambda: _env_int(160, "CLEAR_DISTANCE_MM"))
    blocked_samples: int = field(default_factory=lambda: _env_int(3, "BLOCKED_SAMPLES"))
    patrol_speed: int = field(default_factory=lambda: _env_int(16, "ROBOT_PATROL_SPEED", "ROBOT_SLOW_SPEED"))
    step_seconds: float = field(default_factory=lambda: _env_float(0.18, "ROBOT_PATROL_STEP_SECONDS", "ROBOT_STEP_SECONDS"))
    patrol_settle_seconds: float = field(default_factory=lambda: _env_float(0.05, "ROBOT_PATROL_SETTLE_SECONDS"))
    poll_seconds: float = field(default_factory=lambda: _env_float(0.12, "ROBOT_POLL_SECONDS"))
    scan_enabled: bool = field(default_factory=lambda: _env_bool("ROBOT_SCAN_ENABLED", True))
    scan_timeout_seconds: float = field(default_factory=lambda: _env_float(2.0, "ROBOT_SCAN_TIMEOUT_SECONDS", "SCAN_TIMEOUT_SECONDS"))
    scan_max_detections: int = field(default_factory=lambda: _env_int(3, "ROBOT_SCAN_MAX_DETECTIONS", "SCAN_MAX_DETECTIONS"))
    scan_interval_seconds: float = field(default_factory=lambda: _env_float(0.8, "SCAN_INTERVAL_SECONDS"))
    turn_90_seconds: float = field(default_factory=lambda: _env_float(0.72, "ROBOT_TURN_90_SECONDS"))
    turn_speed: int = field(default_factory=lambda: _env_int(30, "ROBOT_TURN_SPEED"))
    action_settle_seconds: float = field(default_factory=lambda: _env_float(0.12, "ROBOT_ACTION_SETTLE_SECONDS"))
    obstacle_wait_seconds: float = field(default_factory=lambda: _env_float(6.0, "OBSTACLE_WAIT_SECONDS"))
    avoidance_speed: int = field(default_factory=lambda: _env_int(20, "AVOIDANCE_SPEED"))
    avoidance_body_seconds: float = field(default_factory=lambda: _env_float(0.35, "AVOIDANCE_BODY_SECONDS"))
    avoidance_side_clearance_bodies: float = field(default_factory=lambda: _env_float(1.2, "AVOIDANCE_SIDE_CLEARANCE_BODIES"))
    avoidance_parallel_bodies: float = field(default_factory=lambda: _env_float(1.0, "AVOIDANCE_PARALLEL_BODIES"))
    avoidance_return_bodies: float = field(default_factory=lambda: _env_float(1.2, "AVOIDANCE_RETURN_BODIES"))
    forbidden_avoidance_side_clearance_bodies: float = field(default_factory=lambda: _env_float(1.5, "FORBIDDEN_AVOIDANCE_SIDE_CLEARANCE_BODIES"))
    forbidden_avoidance_parallel_bodies: float = field(default_factory=lambda: _env_float(1.2, "FORBIDDEN_AVOIDANCE_PARALLEL_BODIES"))
    forbidden_avoidance_return_bodies: float = field(default_factory=lambda: _env_float(1.5, "FORBIDDEN_AVOIDANCE_RETURN_BODIES"))
    avoidance_turn_direction: str = field(default_factory=lambda: _env_text("right", "AVOIDANCE_TURN_DIRECTION"))
    boundary_min_black_sensors: int = field(default_factory=lambda: _env_int(2, "BOUNDARY_MIN_BLACK_SENSORS"))
    boundary_confirm_samples: int = field(default_factory=lambda: _env_int(1, "BOUNDARY_CONFIRM_SAMPLES"))
    boundary_confirm_gap_seconds: float = field(default_factory=lambda: _env_float(0.02, "BOUNDARY_CONFIRM_GAP_SECONDS"))
    boundary_window_seconds: float = field(default_factory=lambda: _env_float(0.12, "BOUNDARY_WINDOW_SECONDS"))
    boundary_cooldown_seconds: float = field(default_factory=lambda: _env_float(0.0, "BOUNDARY_COOLDOWN_SECONDS"))
    boundary_retreat_speed: int = field(default_factory=lambda: _env_int(12, "BOUNDARY_RETREAT_SPEED"))
    boundary_retreat_seconds: float = field(default_factory=lambda: _env_float(0.14, "BOUNDARY_RETREAT_SECONDS"))
    boundary_retreat_command: str = field(default_factory=lambda: _env_text("backward", "BOUNDARY_RETREAT_COMMAND"))
    motion_guard_poll_seconds: float = field(default_factory=lambda: _env_float(0.01, "MOTION_GUARD_POLL_SECONDS"))
    motion_slice_seconds: float = field(default_factory=lambda: _env_float(0.03, "ROBOT_MOTION_SLICE_SECONDS"))
    object_trigger_enabled: bool = field(default_factory=lambda: _env_bool("OBJECT_TRIGGER_ENABLED", True))
    object_detector: str = field(default_factory=lambda: _env_text("opencv", "OBJECT_DETECTOR"))
    object_detector_model: str = field(default_factory=lambda: _env_text("", "OBJECT_DETECTOR_MODEL"))
    object_roi: str = field(default_factory=lambda: _env_text("", "OBJECT_ROI"))
    object_presence_min_area_ratio: float = field(default_factory=lambda: _env_float(0.008, "OBJECT_PRESENCE_MIN_AREA_RATIO"))
    object_presence_confirm_frames: int = field(default_factory=lambda: _env_int(1, "OBJECT_PRESENCE_CONFIRM_FRAMES"))
    object_presence_cooldown_seconds: float = field(default_factory=lambda: _env_float(1.5, "OBJECT_PRESENCE_COOLDOWN_SECONDS"))
    object_yolo_min_interval_seconds: float = field(default_factory=lambda: _env_float(0.5, "OBJECT_YOLO_MIN_INTERVAL_SECONDS"))
    object_slow_speed: int = field(default_factory=lambda: _env_int(12, "OBJECT_SLOW_SPEED"))
    object_settle_seconds: float = field(default_factory=lambda: _env_float(0.2, "OBJECT_SETTLE_SECONDS"))
    heading_hold_enabled: bool = field(default_factory=lambda: _env_bool("HEADING_HOLD_ENABLED", True))
    heading_hold_tolerance_deg: float = field(default_factory=lambda: _env_float(2.5, "HEADING_HOLD_TOLERANCE_DEG"))
    heading_hold_gain: float = field(default_factory=lambda: _env_float(0.012, "HEADING_HOLD_GAIN"))
    heading_hold_min_pulse_seconds: float = field(default_factory=lambda: _env_float(0.025, "HEADING_HOLD_MIN_PULSE_SECONDS"))
    heading_hold_max_pulse_seconds: float = field(default_factory=lambda: _env_float(0.10, "HEADING_HOLD_MAX_PULSE_SECONDS"))
    heading_hold_correction_speed: int = field(default_factory=lambda: _env_int(16, "HEADING_HOLD_CORRECTION_SPEED"))
    heading_hold_invert: bool = field(default_factory=lambda: _env_bool("HEADING_HOLD_INVERT", False))
    heading_hold_rate_damping: float = field(default_factory=lambda: _env_float(0.18, "HEADING_HOLD_KD", "HEADING_HOLD_RATE_DAMPING"))
    heading_hold_speed_gain: float = field(default_factory=lambda: _env_float(1.8, "HEADING_HOLD_SPEED_GAIN"))
    heading_hold_min_correction_speed: int = field(default_factory=lambda: _env_int(4, "HEADING_HOLD_MIN_CORRECTION_SPEED"))
    heading_hold_min_sample_interval_seconds: float = field(default_factory=lambda: _env_float(0.0, "HEADING_HOLD_MIN_SAMPLE_INTERVAL_SECONDS"))
    heading_hold_min_interval_seconds: float = field(default_factory=lambda: _env_float(0.05, "HEADING_HOLD_MIN_INTERVAL_SECONDS"))
    heading_hold_max_consecutive: int = field(default_factory=lambda: _env_int(5, "HEADING_HOLD_MAX_CONSECUTIVE"))
    heading_hold_confirm_samples: int = field(default_factory=lambda: _env_int(1, "HEADING_HOLD_CONFIRM_SAMPLES"))
    heading_hold_trace_interval_seconds: float = field(default_factory=lambda: _env_float(0.5, "HEADING_HOLD_TRACE_INTERVAL_SECONDS"))
    heading_zupt_enabled: bool = field(default_factory=lambda: _env_bool("HEADING_ZUPT_ENABLED", True))
    heading_zupt_samples: int = field(default_factory=lambda: _env_int(15, "HEADING_ZUPT_SAMPLES"))
    heading_zupt_sample_seconds: float = field(default_factory=lambda: _env_float(0.005, "HEADING_ZUPT_SAMPLE_SECONDS"))
    smooth_cruise_enabled: bool = field(default_factory=lambda: _env_bool("SMOOTH_CRUISE_ENABLED", False))
    cruise_speed: int = field(default_factory=lambda: _env_int(24, "CRUISE_SPEED", "SMOOTH_CRUISE_SPEED"))
    cruise_tick_seconds: float = field(default_factory=lambda: _env_float(0.03, "CRUISE_TICK_SECONDS"))
    cruise_log_interval_seconds: float = field(default_factory=lambda: _env_float(1.0, "CRUISE_LOG_INTERVAL_SECONDS"))
    cruise_vision_enabled: bool = field(default_factory=lambda: _env_bool("CRUISE_VISION_ENABLED", True))
    cruise_recognition_flash_seconds: float = field(default_factory=lambda: _env_float(0.12, "CRUISE_RECOGNITION_FLASH_SECONDS"))
    cruise_recognition_cooldown_seconds: float = field(default_factory=lambda: _env_float(1.5, "CRUISE_RECOGNITION_COOLDOWN_SECONDS"))
    cruise_vision_reopen_seconds: float = field(default_factory=lambda: _env_float(0.3, "CRUISE_VISION_REOPEN_SECONDS"))
    line_follow_enabled: bool = field(default_factory=lambda: _env_bool("LINE_FOLLOW_ENABLED", False))
    line_follow_auto_enter: bool = field(default_factory=lambda: _env_bool("LINE_FOLLOW_AUTO_ENTER", False))
    line_follow_speed: int = field(default_factory=lambda: _env_int(16, "LINE_FOLLOW_SPEED", "ROBOT_PATROL_SPEED", "ROBOT_SLOW_SPEED"))
    line_follow_step_seconds: float = field(default_factory=lambda: _env_float(0.14, "LINE_FOLLOW_STEP_SECONDS", "ROBOT_STEP_SECONDS"))
    line_follow_turn_speed: int = field(
        default_factory=lambda: _env_int(
            16,
            "LINE_FOLLOW_TURN_SPEED",
            "LINE_FOLLOW_SPEED",
            "ROBOT_PATROL_SPEED",
            "ROBOT_SLOW_SPEED",
        )
    )
    line_follow_turn_seconds: float = field(default_factory=lambda: _env_float(0.08, "LINE_FOLLOW_TURN_SECONDS"))
    line_follow_search_seconds: float = field(default_factory=lambda: _env_float(0.08, "LINE_FOLLOW_SEARCH_SECONDS"))
    line_follow_poll_seconds: float = field(default_factory=lambda: _env_float(0.01, "LINE_FOLLOW_POLL_SECONDS"))
    line_follow_max_lost_ticks: int = field(default_factory=lambda: _env_int(15, "LINE_FOLLOW_MAX_LOST_TICKS"))
    motion_sensor_interval_seconds: float = field(default_factory=lambda: _env_float(0.5, "MOTION_SENSOR_INTERVAL_SECONDS"))
    skip_scan_cycles: int = 1
    camera_device: int = 0
    patrol_order: tuple[str, ...] = field(default_factory=lambda: ("A1", "A2", "A3", "A4", "B3", "B2", "B1"))
    cycle_max_missed_shelves: int = 1
    video_width: int = 640
    video_height: int = 360
    video_fps: int = 8
    vision_stability_enabled: bool = field(default_factory=lambda: _env_bool("VISION_STABILITY_ENABLED", True))
    vision_min_stable_frames: int = field(default_factory=lambda: _env_int(3, "VISION_MIN_STABLE_FRAMES"))
    vision_max_center_shift_px: float = field(default_factory=lambda: _env_float(10.0, "VISION_MAX_CENTER_SHIFT_PX"))
    vision_max_corner_shift_px: float = field(default_factory=lambda: _env_float(14.0, "VISION_MAX_CORNER_SHIFT_PX"))
    vision_max_angle_delta_deg: float = field(default_factory=lambda: _env_float(8.0, "VISION_MAX_ANGLE_DELTA_DEG"))
    vision_state_machine_enabled: bool = field(default_factory=lambda: _env_bool("VISION_STATE_MACHINE_ENABLED", True))
    image_classifier_enabled: bool = field(default_factory=lambda: _env_bool("IMAGE_CLASSIFIER_ENABLED", True))
    camera_failure_scan_threshold: int = field(default_factory=lambda: _env_int(8, "CAMERA_FAILURE_SCAN_THRESHOLD"))
    camera_failure_request_cooldown_seconds: float = field(
        default_factory=lambda: _env_float(30.0, "CAMERA_FAILURE_REQUEST_COOLDOWN_SECONDS")
    )
    missing_alert_cooldown_seconds: float = field(default_factory=lambda: _env_float(8.0, "MISSING_ALERT_COOLDOWN_SECONDS"))

    def __post_init__(self) -> None:
        _clamp_config_speeds(self)


class RobotRuntime:
    def __init__(
        self,
        store: InspectionStore,
        warehouse_map: WarehouseMap,
        shelf_manifest: ShelfManifest,
        *,
        config: RobotRuntimeConfig | None = None,
        motion_adapter: Any = motion,
        sensor_adapter: Any = sensors,
        alarm_adapter: Any = alarm,
        gimbal_adapter: Any = gimbal,
        imu_adapter: Any = mpu6050,
        detection_provider: DetectionProvider = tag_detector.iter_detections,
    ) -> None:
        self.store = store
        self.warehouse_map = warehouse_map
        self.shelf_manifest = shelf_manifest
        self.config = config or RobotRuntimeConfig()
        if self.store and self.store.root:
            load_calibration_into_config(self.config, self.store.root)
        self.motion = motion_adapter
        self.sensors = sensor_adapter
        self.alarm = alarm_adapter
        self.gimbal = gimbal_adapter
        self.imu = imu_adapter
        self.detection_provider = detection_provider
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._blocked_count = 0
        self._obstacle_active = False
        self._last_boundary_turn = 0.0
        self._boundary_event_index = 0
        self._last_shelf_anchor: str | None = None
        self._bypassed_for_shelf_anchors: set[str] = set()
        self._line_follow_active = False
        self._line_lost_ticks = 0
        self._last_line_correction: str | None = None
        self._motion_step_index = 0
        self._last_motion_sensor_at = 0.0
        self._observed_shelf_sequence: list[str] = []
        self._pending_boundary_state: tuple[int, int, int, int] | None = None
        self._empty_vision_scans = 0
        self._last_camera_fallback_at = 0.0
        self._last_missing_alert_at = 0.0
        self._black_seen_at = [-1e9, -1e9, -1e9, -1e9]
        self._boundary_retreat_latched = False
        self._heading_guard: Any | None = None
        self._object_presence_hits = 0
        self._last_object_presence_at = 0.0
        self._last_object_yolo_at = 0.0
        self._orange_flash_until = 0.0
        self._last_cruise_log_at = 0.0
        self._recognition_last_at: dict[str, float] = {}
        self._cruise_active_shelf: str | None = None
        self._cruise_segment_items: dict[str, dict[str, object]] = {}
        self._cruise_scanner: _CruiseVisionScanner | None = None
        self._manual_override = threading.Event()
        self._last_heading_correction_at = 0.0
        self._last_heading_sample_log_at = 0.0
        self._heading_consecutive_count = 0
        self._heading_over_tolerance_count = 0
        self._cruise_vision_suppressed_until_boundary = False

    def start(self, shelf_order: Iterable[str] | None = None) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        clearer = getattr(self.motion, "clear_stop", None)
        if callable(clearer):
            clearer()
        self._reset_boundary_window()
        self._reset_heading_guard()
        self._thread = threading.Thread(target=self._run_continuous_patrol_safely, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._stop_cruise_scanner()
        stopper = getattr(self.motion, "request_stop", None)
        if callable(stopper):
            stopper()
        else:
            try:
                self.motion.stop()
            except RobotHardwareError:
                pass
        self.store.stop()
        self.store.record_motion_debug("runtime_stopped", "收到停止命令，电机已停止。", status="STOPPED")

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def request_manual_override(self) -> None:
        """Manual control takes over: halt the patrol loop, then clear _stop_event so
        the IMU closed-loop 90° turn (whose ``should_abort`` reads ``_stop_event``)
        does not self-abort on its very first check.

        R1 root cause: ``runtime.stop()`` sets ``_stop_event`` and never clears it
        before the manual command runs, so ``imu_turn(..., should_abort=_stop_event.is_set)``
        returns ``"aborted before motion"`` and the turn pulse is never issued.
        Joining the patrol thread first avoids the race in which a cruise tick
        rewrites the wheels mid-turn (R7).
        """

        self._manual_override.set()
        self._stop_event.set()  # let the patrol loop / motion slices exit
        self._stop_cruise_scanner()
        stopper = getattr(self.motion, "request_stop", None)
        if callable(stopper):
            stopper()
        else:
            try:
                self.motion.stop()
            except RobotHardwareError:
                pass
        # Wait for the patrol thread to actually exit so it cannot overwrite the
        # wheels while the manual command is running (R7).
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=0.2)
        # Critical: clear so the manual turn_90_closed_loop does not self-abort.
        self._stop_event.clear()
        clearer = getattr(self.motion, "clear_stop", None)
        if callable(clearer):
            clearer()
        self._heading_consecutive_count = 0
        self._heading_over_tolerance_count = 0
        self._last_heading_correction_at = 0.0
        self._last_heading_sample_log_at = 0.0

    def release_manual_override(self) -> None:
        """Manual control finished. The patrol thread has already exited, so to
        resume cruising the caller must invoke ``start()`` again.
        """

        self._manual_override.clear()

    def _run_patrol_safely(self, shelf_order: Iterable[str] | None = None) -> None:
        try:
            self.run_patrol(shelf_order=shelf_order)
        except RobotHardwareError as exc:
            self.store.record_robot_status("STOPPED", f"runtime hardware error: {exc}")

    def _run_continuous_patrol_safely(self) -> None:
        try:
            self.run_continuous_patrol()
        except RobotHardwareError as exc:
            self.store.record_run_mode("robot", False)
            self.store.record_motion_debug("runtime_hardware_error", f"runtime hardware error: {exc}", status="ERROR")
        except Exception as exc:
            self.store.record_run_mode("robot", False)
            self.store.record_motion_debug("runtime_fatal_error", f"runtime fatal error: {exc}", status="ERROR")

    def run_continuous_patrol(self, max_iterations: int | None = None) -> None:
        self._reset_boundary_window()
        self._reset_heading_guard()
        self.store.record_run_mode("robot", True)
        self.store.start()
        self.store.record_cycle(1, self._skip_shortage_for_cycle(1))
        cruise = self.config.smooth_cruise_enabled
        self.store.record_motion_debug(
            "runtime_started",
            (
                "巡航启动：低速匀速前进、连续陀螺仪纠偏、OpenCV 发现物体即停车识别、列端转向、禁区绕行。"
                if cruise
                else "巡逻启动：短步前进、列端转向、寻线过渡、禁区绕行；检测到目标后停车识别。"
            ),
            evidence={
                "smooth_cruise_enabled": cruise,
                "patrol_speed": self.config.cruise_speed if cruise else self.config.patrol_speed,
                "step_seconds": self.config.cruise_tick_seconds if cruise else self.config.step_seconds,
                "boundary_min_black_sensors": self.config.boundary_min_black_sensors,
                "boundary_confirm_samples": self.config.boundary_confirm_samples,
                "boundary_window_seconds": self.config.boundary_window_seconds,
                "scan_enabled": self.config.scan_enabled,
            },
        )
        self._boundary_event_index = 0
        self._last_shelf_anchor = None
        self._bypassed_for_shelf_anchors = set()
        self._line_follow_active = False
        self._line_lost_ticks = 0
        self._last_line_correction = None
        self._motion_step_index = 0
        self._object_presence_hits = 0
        self._orange_flash_until = 0.0
        self._last_cruise_log_at = 0.0
        self._recognition_last_at = {}
        self._cruise_active_shelf = None
        self._cruise_segment_items = {}
        self._cruise_vision_suppressed_until_boundary = False
        self._last_heading_sample_log_at = 0.0
        self._show_normal()
        self._initialize_gimbal()
        self.refresh_motion_sensor(force=True)
        if cruise:
            self._zupt_recalibrate("cruise_start")
            self._sync_cruise_scanner_for_phase()

        iterations = 0
        last_scan_at = time.monotonic()

        try:
            while not self._stop_event.is_set():
                if self._manual_override.is_set():
                    # Manual control has the floor: don't run any patrol tick that
                    # could overwrite the wheels. Spin cheaply until release().
                    self._interruptible_sleep(0.02)
                    continue
                if max_iterations is not None and iterations >= max_iterations:
                    self.motion.stop()
                    self.store.record_motion_debug(
                        "runtime_stopped",
                        f"continuous patrol stopped after {iterations} iterations",
                        status="STOPPED",
                    )
                    return

                tape_state = self.sensors.read_tape_boundary()
                boundary_outcome = self._handle_tape_boundary(tape_state)
                if boundary_outcome != "none":
                    current_cycle = self._current_cycle()
                    self.store.record_cycle(current_cycle, self._skip_shortage_for_cycle(current_cycle))
                    continue

                if not self._guard_obstacle(None):
                    continue

                if cruise:
                    if self._maybe_scan_for_object_presence():
                        iterations += 1
                        current_cycle = self._current_cycle()
                        self.store.record_cycle(current_cycle, self._skip_shortage_for_cycle(current_cycle))
                        continue
                    self._sync_cruise_scanner_for_phase()
                self.refresh_motion_sensor()
                self._drive_patrol_step(tape_state)
                self._update_indicator_light()
                iterations += 1
                current_cycle = self._current_cycle()
                self.store.record_cycle(current_cycle, self._skip_shortage_for_cycle(current_cycle))

                now = time.monotonic()
                if cruise:
                    self._sync_cruise_scanner_for_phase()
                    if self._handle_cruise_recognitions():
                        current_cycle = self._current_cycle()
                        self.store.record_cycle(current_cycle, self._skip_shortage_for_cycle(current_cycle))
                    continue
                if self._maybe_scan_for_object_presence():
                    last_scan_at = time.monotonic()
                    continue
                if self.config.scan_enabled and now - last_scan_at >= self.config.scan_interval_seconds:
                    self._scan_visible_shelf()
                    last_scan_at = now

            self.motion.stop()
            self.store.stop()
            self.store.record_motion_debug("runtime_stopped", "运动调试巡逻已停止。", status="STOPPED")
        finally:
            self._stop_cruise_scanner()

    def run_patrol(self, shelf_order: Iterable[str] | None = None, max_steps: int | None = None) -> None:
        order = list(shelf_order) if shelf_order is not None else list(self.shelf_manifest)
        try:
            route = plan_patrol_route(self.warehouse_map, order)
        except PlanningError as exc:
            self.store.record_robot_status("STOPPED", f"path planning failed: {exc}")
            return

        self.store.start()
        self.store.record_path(flatten_route(route), status="active")
        current = (int(self.warehouse_map["start"][0]), int(self.warehouse_map["start"][1]))
        heading = _normalize_heading(str(self.warehouse_map.get("start_heading", "E")), "E")
        executed = 0

        for step in route:
            if self._stop_event.is_set():
                return
            for cell in _cells_from_step(step):
                if self._stop_event.is_set():
                    return
                if cell == current:
                    self.store.record_pose(cell[0], cell[1], heading, source="runtime")
                    continue
                guarded_heading = self._guard_before_move(step.get("shelf_id"), heading)
                if guarded_heading is None:
                    return
                heading = guarded_heading
                next_heading = heading_for_delta(current, cell, heading)
                heading = self._move_between(current, cell, heading, next_heading)
                current = cell
                executed += 1
                self.store.record_pose(current[0], current[1], heading, source="runtime")
                if max_steps is not None and executed >= max_steps:
                    self.store.record_robot_status("STOPPED", f"runtime stopped after {executed} waypoint steps")
                    return
            if step["action"] == "scan" and step["shelf_id"] is not None:
                heading = self._scan_shelf(step, heading)

        self.motion.stop()
        self.alarm.show_normal()
        self.store.finish_run()

    def _guard_before_move(self, shelf_id: str | None, heading: str) -> str | None:
        boundary_outcome = self._handle_tape_boundary()
        if boundary_outcome == "turn":
            return _heading_after_right_turn(heading)
        if boundary_outcome == "bypass":
            return heading
        if not self._guard_obstacle(shelf_id):
            return None
        return heading

    def _handle_tape_boundary_turn(self, tape_state: tuple[int, int, int, int] | None = None) -> bool:
        return self._handle_tape_boundary(tape_state) == "turn"

    def _handle_tape_boundary(self, tape_state: tuple[int, int, int, int] | None = None) -> str:
        if tape_state is None:
            tape_state = self.sensors.read_tape_boundary()
        candidate = self._feed_boundary_window(tape_state)
        if not candidate and self._pending_boundary_state is not None:
            tape_state = self._pending_boundary_state
            self._pending_boundary_state = None
            candidate = True
        elif candidate:
            self._pending_boundary_state = None
            tape_state = self._boundary_window_state(tape_state)
        if not candidate:
            return "none"
        now = time.monotonic()
        if now - self._last_boundary_turn < self.config.boundary_cooldown_seconds:
            self._reset_boundary_window()
            return "none"
        self.motion.stop()
        self.store.record_motion_debug(
            "boundary_candidate",
            f"检测到黑胶带候选并立即停车：tape={_format_tape_state(tape_state)}，阈值={self.config.boundary_min_black_sensors} 路黑。",
            status="TURNING_AT_BOUNDARY",
            evidence={
                "tape_state": _json_tape_state(tape_state),
                "black_count": sensors.black_tape_count(tape_state),
                "min_black": self.config.boundary_min_black_sensors,
                "confirm_samples": self.config.boundary_confirm_samples,
                "boundary_window_seconds": self.config.boundary_window_seconds,
            },
        )
        confirmed_state = self._confirm_boundary_state(tape_state)
        if confirmed_state is None:
            self.store.record_motion_debug(
                "boundary_rejected",
                "黑胶带候选已忽略：确认样本未持续达到阈值，继续低速巡逻。",
                evidence={"first_tape_state": _json_tape_state(tape_state)},
            )
            return "none"
        if not self._boundary_retreat_latched:
            self._retreat_from_boundary("boundary_confirmed")
        self._last_boundary_turn = now
        action = self._next_boundary_action()
        if action == "bypass":
            return self._handle_forbidden_bypass(confirmed_state)
        return self._handle_planned_boundary_turn(confirmed_state, action)

    def _handle_planned_boundary_turn(self, tape_state: tuple[int, int, int, int], action: str) -> str:
        action_number = self._boundary_event_index + 1
        zone_id = f"black-tape-route-{action_number}"
        self.store.record_motion_debug(
            "boundary_action",
            self._boundary_action_message(action),
            status="TURNING_AT_BOUNDARY",
            evidence={
                "boundary_event_index": action_number,
                "action": action,
                "last_shelf_anchor": self._last_shelf_anchor,
                "tape_state": _json_tape_state(tape_state),
                "phase_before": self._patrol_phase_label(),
            },
        )
        self.alarm.show_warning()
        self.store.record_boundary(tape_state, True, action)
        self.store.record_forbidden_zone(zone_id, True)
        self._play_cue("obstacle", "检测到禁区/黑胶带边界，播放障碍提示音。")
        if not _turn_succeeded(self._turn_90("right")):
            return "none"
        self.store.record_boundary_turn("clockwise", 90)
        self._line_follow_active = False
        self._line_lost_ticks = 0
        self._last_line_correction = None
        self._reset_boundary_window()
        self._reset_heading_guard()
        self._zupt_recalibrate("post_boundary_turn")
        self.store.record_forbidden_zone(zone_id, False)
        self._advance_boundary_event()
        self._cruise_vision_suppressed_until_boundary = False
        self.store.record_motion_debug(
            "boundary_action_done",
            f"转向完成，当前阶段：{self._patrol_phase_label()}。",
            evidence={
                "line_follow_active": self._line_follow_active,
                "phase_after": self._patrol_phase_label(),
            },
        )
        self._show_line_follow() if self._line_follow_active else self._show_normal()
        return "turn"

    def _line_follow_auto_enter_allowed(self) -> bool:
        return self._last_shelf_anchor not in {"A4", "B1"}

    def _handle_forbidden_bypass(self, tape_state: tuple[int, int, int, int]) -> str:
        action_number = self._boundary_event_index + 1
        zone_id = f"black-tape-bypass-{action_number}"
        anchor = self._last_shelf_anchor
        self._line_follow_active = False
        self.alarm.show_obstacle_wait()
        self.store.record_motion_debug(
            "forbidden_bypass_start",
            self._boundary_action_message("bypass"),
            status="FORBIDDEN_ZONE_WAIT",
            evidence={
                "boundary_event_index": action_number,
                "last_shelf_anchor": anchor,
                "tape_state": _json_tape_state(tape_state),
                "phase_before": self._patrol_phase_label(),
            },
        )
        self.store.record_boundary(tape_state, True, "route_forbidden_bypass")
        self.store.record_forbidden_zone(zone_id, True)
        self._play_cue("obstacle", "检测到非寻线禁区，小车按障碍绕行。")
        if self._avoid_to_safe_side(
            None,
            side_clearance_bodies=self.config.forbidden_avoidance_side_clearance_bodies,
            parallel_bodies=self.config.forbidden_avoidance_parallel_bodies,
            return_bodies=self.config.forbidden_avoidance_return_bodies,
        ):
            self.store.record_forbidden_zone(zone_id, False)
            self._show_normal()
            if anchor is not None:
                self._bypassed_for_shelf_anchors.add(anchor)
        self._reset_boundary_window()
        self._reset_heading_guard()
        self._advance_boundary_event()
        self._cruise_vision_suppressed_until_boundary = False
        self.store.record_motion_debug(
            "forbidden_bypass_done",
            f"禁区绕行完成，继续阶段：{self._patrol_phase_label()}。",
            evidence={"phase_after": self._patrol_phase_label()},
        )
        return "bypass"

    def _candidate_boundary(self, tape_state: tuple[int, int, int, int] | None) -> bool:
        min_black = max(1, min(4, int(self.config.boundary_min_black_sensors)))
        if min_black >= 4:
            detector = getattr(self.sensors, "full_tape_boundary_detected", sensors.full_tape_boundary_detected)
            return detector(tape_state)
        detector = getattr(self.sensors, "tape_boundary_count_detected", sensors.tape_boundary_count_detected)
        return detector(tape_state, min_black=min_black)

    def _reset_boundary_window(self) -> None:
        self._black_seen_at = [-1e9, -1e9, -1e9, -1e9]
        self._pending_boundary_state = None
        self._boundary_retreat_latched = False

    def _feed_boundary_window(self, tape_state: tuple[int, int, int, int] | None) -> bool:
        if tape_state is None:
            return False
        now = time.monotonic()
        for index, value in enumerate(tape_state[:4]):
            if int(value) == 0:
                self._black_seen_at[index] = now
        window = max(0.0, float(self.config.boundary_window_seconds))
        min_black = max(1, min(4, int(self.config.boundary_min_black_sensors)))
        recent_black = sum(1 for seen_at in self._black_seen_at if now - seen_at <= window)
        return recent_black >= min_black

    def _boundary_window_state(
        self,
        fallback: tuple[int, int, int, int] | None = None,
    ) -> tuple[int, int, int, int] | None:
        now = time.monotonic()
        window = max(0.0, float(self.config.boundary_window_seconds))
        if all(seen_at <= -1e8 for seen_at in self._black_seen_at):
            return fallback
        return tuple(0 if now - seen_at <= window else 1 for seen_at in self._black_seen_at)  # type: ignore[return-value]

    def _confirm_boundary_state(
        self,
        first_state: tuple[int, int, int, int] | None,
    ) -> tuple[int, int, int, int] | None:
        if first_state is None:
            return None
        required = max(1, int(self.config.boundary_confirm_samples))
        if required <= 1:
            return first_state
        confirmed_state = first_state
        for _ in range(required - 1):
            gap = max(0.0, float(self.config.boundary_confirm_gap_seconds))
            if gap > 0.0 and not self._interruptible_sleep(gap):
                return None
            try:
                sample = self.sensors.read_tape_boundary()
            except RobotHardwareError:
                return None
            if not self._candidate_boundary(sample):
                return None
            confirmed_state = sample
        return confirmed_state

    def _retreat_from_boundary(self, source: str) -> bool:
        seconds = max(0.0, float(self.config.boundary_retreat_seconds))
        if seconds <= 0.0:
            self._boundary_retreat_latched = True
            return False
        speed = max(1, int(self.config.boundary_retreat_speed))
        command = str(self.config.boundary_retreat_command or "forward").strip().lower()
        command_name = "move_backward" if command in {"backward", "back", "reverse"} else "move_forward"
        mover = self.motion.move_backward_slow if command_name == "move_backward" else self.motion.move_forward_slow
        try:
            mover(speed=speed, duration_seconds=seconds)
            self.motion.stop()
        except RobotHardwareError:
            return False
        self._boundary_retreat_latched = True
        self.store.record_motion_debug(
            "boundary_retreat",
            f"黑胶带边界已小幅回退：speed={speed}, duration={seconds:.2f}s。",
            status="TURNING_AT_BOUNDARY",
            evidence={
                "source": source,
                "command": command_name,
                "speed": speed,
                "duration_seconds": seconds,
            },
        )
        return True

    def _drive_patrol_step(self, tape_state: tuple[int, int, int, int] | None) -> None:
        if self._line_follow_active and self.config.line_follow_enabled:
            self._drive_line_follow_step(tape_state)
            return
        if self.config.line_follow_enabled and self.config.line_follow_auto_enter and self._line_follow_auto_enter_allowed():
            decision = decide_line_follow_motion(tape_state)
            if decision.line_seen and not decision.boundary_candidate:
                self._line_follow_active = True
                self._line_lost_ticks = 0
                self.store.record_motion_debug(
                    "line_follow_auto_enter",
                    "Tape line detected during patrol; switching to line-follow correction.",
                    evidence={
                        "phase": self._patrol_phase_label(),
                        "decision": decision.command,
                        "description": decision.description,
                        "tape_state": _json_tape_state(tape_state),
                        "speed": self.config.line_follow_speed,
                        "step_seconds": self.config.line_follow_step_seconds,
                    },
                )
                self._drive_line_follow_step(tape_state)
                return
        if self.config.smooth_cruise_enabled:
            self._drive_cruise_step(tape_state)
            return
        self._motion_step_index += 1
        self.store.record_motion_debug(
            "patrol_step",
            (
                f"{self._patrol_phase_label()}：短步前进 #{self._motion_step_index}，"
                f"speed={self.config.patrol_speed}, step={self.config.step_seconds:.2f}s, "
                f"tape={_format_tape_state(tape_state)}。"
            ),
            evidence={
                "phase": self._patrol_phase_label(),
                "speed": self.config.patrol_speed,
                "step_seconds": self.config.step_seconds,
                "patrol_settle_seconds": self.config.patrol_settle_seconds,
                "line_follow_enabled": self.config.line_follow_enabled,
                "tape_state": _json_tape_state(tape_state),
            },
        )
        self._forward_step(
            speed=self.config.patrol_speed,
            duration_seconds=self.config.step_seconds,
            settle_seconds=self.config.patrol_settle_seconds,
        )

    def _drive_cruise_step(self, tape_state: tuple[int, int, int, int] | None) -> None:
        """One tick of constant-velocity cruise.

        Unlike the short-step patrol, the motor is never stopped between ticks:
        heading correction is folded into the forward wheel speeds, so the car
        can pull back toward straight without stop-rotate-stop pulses.
        """

        if self._manual_override.is_set():
            return
        if self._stop_event.is_set() or self._manual_override.is_set():
            self.motion.stop()
            return
        self._motion_step_index += 1
        now = time.monotonic()
        interval = max(0.0, float(self.config.cruise_log_interval_seconds))
        if now - self._last_cruise_log_at >= interval:
            self._last_cruise_log_at = now
            self.store.record_motion_debug(
                "cruise_step",
                (
                    f"{self._patrol_phase_label()}：匀速巡航 #{self._motion_step_index}，"
                    f"speed={self.config.cruise_speed}, tick={self.config.cruise_tick_seconds:.2f}s, "
                    f"tape={_format_tape_state(tape_state)}。"
                ),
                evidence={
                    "phase": self._patrol_phase_label(),
                    "speed": self.config.cruise_speed,
                    "tick_seconds": self.config.cruise_tick_seconds,
                    "tape_state": _json_tape_state(tape_state),
                },
            )
        self._run_timed_motion(
            self.motion.move_forward_slow,
            speed=self.config.cruise_speed,
            duration_seconds=self.config.cruise_tick_seconds,
            watch_boundary=True,
            heading_hold=True,
            keep_running=True,
        )
        self._log_motion_command(
            "cruise",
            "move_forward",
            speed=self.config.cruise_speed,
            duration_seconds=self.config.cruise_tick_seconds,
        )

    def _drive_line_follow_step(self, tape_state: tuple[int, int, int, int] | None) -> None:
        if self._stop_event.is_set():
            self.motion.stop()
            return
        decision = decide_line_follow_motion(tape_state)
        phase = self._patrol_phase_label()
        evidence = {
            "phase": phase,
            "decision": decision.command,
            "description": decision.description,
            "tape_state": _json_tape_state(tape_state),
            "lost_ticks": self._line_lost_ticks,
            "speed": self.config.line_follow_speed,
            "step_seconds": self.config.line_follow_step_seconds,
        }

        if decision.boundary_candidate:
            self.motion.stop()
            self.store.record_motion_debug(
                "line_follow_boundary_candidate",
                f"{phase}：寻线时检测到列端/禁区候选，等待主循环执行转向或绕行。",
                status="TURNING_AT_BOUNDARY",
                evidence=evidence,
            )
            return

        if decision.command in {"wait", "stop"} and not decision.line_seen:
            self._line_lost_ticks += 1
            evidence["lost_ticks"] = self._line_lost_ticks
            recovery_command = self._line_recovery_command()
            evidence["recovery_command"] = recovery_command
            if self._line_lost_ticks >= self.config.line_follow_max_lost_ticks:
                self.motion.stop()
                self._line_follow_active = False
                self.store.record_motion_debug(
                    "line_follow_lost",
                    f"{phase}：寻线丢线超过 {self.config.line_follow_max_lost_ticks} 次，已停车等待。",
                    status="STOPPED",
                    evidence=evidence,
                )
                self._interruptible_sleep(max(0.0, self.config.line_follow_poll_seconds))
                return
            if tape_state is None:
                self.motion.stop()
                self.store.record_motion_debug(
                    "line_follow_wait",
                    f"{phase}：{decision.description}，传感器读数无效，先停车等下一次读数。",
                    evidence=evidence,
                )
                self._interruptible_sleep(max(0.0, self.config.line_follow_poll_seconds))
                return
            if self._line_lost_ticks == 1:
                self.store.record_motion_debug(
                    "line_follow_search",
                    f"{phase}：{decision.description}，按最近纠偏方向 {recovery_command} 短步找线。",
                    evidence=evidence,
                )
            self._run_line_follow_command(recovery_command, search=True)
            self._interruptible_sleep(max(0.0, self.config.line_follow_poll_seconds))
            return

        if decision.command in {"wait", "stop"}:
            self.motion.stop()
            self._interruptible_sleep(max(0.0, self.config.line_follow_poll_seconds))
            return

        self._line_lost_ticks = 0
        self._remember_line_correction(decision.command)
        self._motion_step_index += 1
        self.store.record_motion_debug(
            "line_follow_step",
            (
                f"{phase}：寻线 #{self._motion_step_index}，{decision.description}，"
                f"动作={decision.command}, speed={self.config.line_follow_speed}, "
                f"step={self.config.line_follow_step_seconds:.2f}s, tape={_format_tape_state(tape_state)}。"
            ),
            evidence=evidence,
        )
        self._run_line_follow_command(decision.command)
        self._interruptible_sleep(max(0.0, self.config.line_follow_poll_seconds))

    def _run_line_follow_command(self, command: str, *, search: bool = False) -> None:
        step_seconds = self.config.line_follow_search_seconds if search else self.config.line_follow_step_seconds
        turn_seconds = self.config.line_follow_search_seconds if search else self.config.line_follow_turn_seconds
        if command == "forward":
            self._log_motion_command(
                "line_follow",
                "move_forward",
                speed=self.config.line_follow_speed,
                duration_seconds=step_seconds,
                search=search,
            )
            self._run_timed_motion(
                self.motion.move_forward_slow,
                speed=self.config.line_follow_speed,
                duration_seconds=step_seconds,
                heading_hold=False,
            )
        elif command == "strafe_left":
            self._log_motion_command(
                "line_follow",
                "strafe_left",
                speed=self.config.line_follow_speed,
                duration_seconds=step_seconds,
                search=search,
            )
            self._run_timed_motion(
                self.motion.strafe_left_slow,
                speed=self.config.line_follow_speed,
                duration_seconds=step_seconds,
                heading_hold=False,
            )
        elif command == "strafe_right":
            self._log_motion_command(
                "line_follow",
                "strafe_right",
                speed=self.config.line_follow_speed,
                duration_seconds=step_seconds,
                search=search,
            )
            self._run_timed_motion(
                self.motion.strafe_right_slow,
                speed=self.config.line_follow_speed,
                duration_seconds=step_seconds,
                heading_hold=False,
            )
        elif command == "turn_left":
            self._log_motion_command(
                "line_follow",
                "rotate_left",
                speed=self.config.line_follow_turn_speed,
                duration_seconds=turn_seconds,
                search=search,
            )
            self._run_timed_motion(
                self.motion.rotate_left_slow,
                speed=self.config.line_follow_turn_speed,
                duration_seconds=turn_seconds,
                heading_hold=False,
            )
        elif command == "turn_right":
            self._log_motion_command(
                "line_follow",
                "rotate_right",
                speed=self.config.line_follow_turn_speed,
                duration_seconds=turn_seconds,
                search=search,
            )
            self._run_timed_motion(
                self.motion.rotate_right_slow,
                speed=self.config.line_follow_turn_speed,
                duration_seconds=turn_seconds,
                heading_hold=False,
            )
        else:
            self.motion.stop()

    def _run_timed_motion(
        self,
        mover: Callable[..., None],
        *,
        speed: int,
        duration_seconds: float,
        watch_boundary: bool = True,
        heading_hold: bool = False,
        keep_running: bool = False,
    ) -> None:
        # keep_running leaves the motor energised at the end (no trailing stop) so
        # a caller can chain motion slices into a continuous, jerk-free cruise
        # instead of the stop-start cadence a per-step stop would produce.
        duration = max(0.0, float(duration_seconds))
        guard_interval = (
            max(0.0, float(self.config.motion_guard_poll_seconds))
            if watch_boundary or heading_hold
            else 0.0
        )
        if duration <= 0.0 or guard_interval <= 0.0 or duration <= guard_interval:
            correction = None
            if heading_hold:
                correction = self._heading_hold_correction(speed)
            self._call_mover_with_correction(mover, speed=speed, duration_seconds=duration, correction=correction)
            if correction is not None:
                self._record_heading_hold_correction(correction, speed=speed)
            latched = self._poll_boundary_during_motion(duration) if watch_boundary else False
            if not keep_running or latched:
                self.motion.stop()
            return

        remaining = duration
        elapsed = 0.0
        latched = False
        while remaining > 0 and not self._stop_event.is_set() and not self._manual_override.is_set():
            chunk = min(remaining, guard_interval)
            correction = None
            if heading_hold:
                correction = self._heading_hold_correction(speed)
                if self._stop_event.is_set() or self._manual_override.is_set():
                    break
            self._call_mover_with_correction(mover, speed=speed, duration_seconds=chunk, correction=correction)
            if correction is not None:
                self._record_heading_hold_correction(correction, speed=speed)
            elapsed += chunk
            remaining -= chunk
            if watch_boundary and self._poll_boundary_during_motion(elapsed):
                latched = True
                break
        if self._manual_override.is_set():
            # Manual control needs a clean slate: drop any energised axis so the
            # next manual motion command starts from a stopped chassis.
            self.motion.stop()
            return
        if not keep_running or latched or self._stop_event.is_set():
            self.motion.stop()

    def _poll_boundary_during_motion(self, elapsed_seconds: float) -> bool:
        try:
            tape_state = self.sensors.read_tape_boundary()
        except RobotHardwareError:
            return False
        if not self._feed_boundary_window(tape_state):
            return False
        self._pending_boundary_state = self._boundary_window_state(tape_state)
        self.motion.stop()
        self.store.record_motion_debug(
            "motion_guard_boundary_latched",
            (
                "运动过程中捕捉到黑胶带压线候选，已立即停车并锁存读数，"
                "下一轮控制循环将执行列端/禁区动作。"
            ),
            status="TURNING_AT_BOUNDARY",
            evidence={
                "tape_state": _json_tape_state(self._pending_boundary_state),
                "elapsed_seconds": round(max(0.0, float(elapsed_seconds)), 3),
                "guard_poll_seconds": self.config.motion_guard_poll_seconds,
                "boundary_window_seconds": self.config.boundary_window_seconds,
            },
        )
        return True

    def _remember_line_correction(self, command: str) -> None:
        if command in {"strafe_left", "strafe_right", "turn_left", "turn_right"}:
            self._last_line_correction = command

    def _line_recovery_command(self) -> str:
        if self._last_line_correction in {"strafe_left", "strafe_right", "turn_left", "turn_right"}:
            return self._last_line_correction
        return "forward"

    @staticmethod
    def _line_is_centered(tape_state: tuple[int, int, int, int] | None) -> bool:
        if tape_state is None:
            return True
        _, left_center, right_center, _ = tape_state
        return left_center == 0 and right_center == 0

    def _guard_obstacle(self, shelf_id: str | None) -> bool:
        distance_mm = self.sensors.read_distance_mm()
        if distance_mm is None:
            return True
        if distance_mm < self.config.blocked_distance_mm:
            self._blocked_count += 1
        else:
            self._blocked_count = 0
            if self._obstacle_active and distance_mm >= self.config.clear_distance_mm:
                self._obstacle_active = False
                self.alarm.clear_alarm()
                self.store.record_obstacle(distance_mm, False)
            return True

        if self._blocked_count < self.config.blocked_samples:
            return True

        self._obstacle_active = True
        self.motion.stop()
        self.alarm.show_obstacle_wait()
        self._zupt_recalibrate("obstacle_stop")
        self.store.record_obstacle(distance_mm, True, waiting_seconds=int(self.config.obstacle_wait_seconds))
        self._play_cue("obstacle", "检测到障碍物，小车停车等待。")
        return self._wait_for_obstacle_clear(shelf_id)

    def _initialize_gimbal(self) -> None:
        initializer = getattr(self.gimbal, "initialize_side_camera", None)
        if not callable(initializer):
            return
        try:
            initializer()
            yaw = getattr(self.gimbal, "DEFAULT_YAW_ANGLE", None)
            pitch = getattr(self.gimbal, "DEFAULT_PITCH_ANGLE", None)
            self.store.record_gimbal_initialized(yaw=yaw, pitch=pitch)
        except RobotHardwareError as exc:
            self.store.record_robot_status("PATROLLING", f"camera gimbal init skipped: {exc}")

    def refresh_motion_sensor(self, *, force: bool = False) -> None:
        interval = max(0.0, float(self.config.motion_sensor_interval_seconds))
        now = time.monotonic()
        if not force and now - self._last_motion_sensor_at < interval:
            return
        reader = getattr(self.imu, "read_motion_sample", None)
        if not callable(reader):
            return
        self._last_motion_sensor_at = now
        sample = reader()
        self.store.record_motion_sensor(sample)

    def turn_90_closed_loop(self, direction: str, *, speed: int | None = None, duration_seconds: float | None = None) -> dict[str, object] | None:
        return self._turn_90(direction, speed=speed, duration_seconds=duration_seconds)

    def _scan_visible_shelf(self) -> None:
        detections = self._collect_detections()
        shelf_id = self._shelf_id_from_detections(detections)
        if shelf_id is None:
            if detections:
                fallback_shelf = str(self.store.snapshot().get("current_shelf") or "UNKNOWN").strip().upper() or "UNKNOWN"
                self._perform_scan(fallback_shelf, f"{fallback_shelf}_SCAN", detections)
                return
            self._record_empty_vision_scan()
            return
        self._empty_vision_scans = 0
        self._perform_scan(shelf_id, f"{shelf_id}_SCAN", detections)

    def _maybe_scan_for_object_presence(self) -> bool:
        if not self.config.scan_enabled or not self.config.object_trigger_enabled or self._stop_event.is_set():
            return False
        now = time.monotonic()
        cooldown = max(0.0, float(self.config.object_presence_cooldown_seconds))
        if now - self._last_object_presence_at < cooldown:
            return False
        detector = str(self.config.object_detector or "opencv").strip().lower()
        if detector in {"yolov5_lite_cpu", "hailo_yolo"}:
            yolo_interval = max(0.0, float(self.config.object_yolo_min_interval_seconds))
            if now - self._last_object_yolo_at < yolo_interval:
                return False
            self._last_object_yolo_at = now
        try:
            present = tag_detector.detect_object_presence_from_camera(
                device=self.config.camera_device,
                detector=detector,
                model_path=self.config.object_detector_model,
                roi=self.config.object_roi,
                min_area_ratio=self.config.object_presence_min_area_ratio,
            )
        except Exception:
            self._object_presence_hits = 0
            return False
        if not present:
            self._object_presence_hits = 0
            return False
        self._object_presence_hits += 1
        required = max(1, int(self.config.object_presence_confirm_frames))
        if self._object_presence_hits < required:
            return False
        self._object_presence_hits = 0
        self.motion.stop()
        self._stop_cruise_scanner()
        self.store.record_motion_debug(
            "object_presence_triggered",
            "检测到画面中有新目标进入：已停车，执行一次完整视觉识别后继续巡逻。",
            status="SCANNING_SHELF",
            evidence={
                "detector": detector,
                "confirm_frames": required,
                "roi": self.config.object_roi,
                "min_area_ratio": self.config.object_presence_min_area_ratio,
            },
        )
        self._interruptible_sleep(max(0.0, float(self.config.object_settle_seconds)))
        if self._stop_event.is_set():
            return True
        self._scan_visible_shelf()
        self.motion.stop()
        self._reset_heading_guard()
        self._zupt_recalibrate("post_object_scan")
        self._last_object_presence_at = time.monotonic()
        self.store.record_motion_debug(
            "object_presence_resume",
            "目标识别完成，恢复低速巡逻。",
            evidence={"cooldown_seconds": cooldown},
        )
        return True

    def _shelf_id_from_detections(self, detections: list[dict[str, object]]) -> str | None:
        for detection in detections:
            enriched = self._enrich_detection(detection)
            shelf_id = enriched.get("shelf_id")
            if shelf_id is not None:
                normalized = str(shelf_id).strip().upper()
                if normalized:
                    return normalized
        return None

    def _enrich_detection(self, detection: Mapping[str, object]) -> dict[str, object]:
        enriched = dict(detection)
        tag_id = enriched.get("tag_id")
        if tag_id is None:
            return enriched
        info = self.store.tag_map.get(str(tag_id))
        if info is None:
            try:
                numeric_id = int(str(tag_id))
            except ValueError:
                return enriched
            if 101 <= numeric_id <= 120:
                enriched["kind"] = "shelf"
                enriched.pop("item_id", None)
            return enriched
        kind = str(info.get("kind", "item"))
        enriched["kind"] = kind
        enriched["name"] = info.get("name")
        if kind == "shelf" and info.get("shelf_id") is not None:
            enriched["shelf_id"] = str(info["shelf_id"]).strip().upper()
            enriched.pop("item_id", None)
        if kind == "item" and info.get("item_id") is not None:
            enriched["item_id"] = str(info["item_id"])
            enriched.pop("shelf_id", None)
        return enriched

    def _record_observed_shelf(self, shelf_id: str) -> None:
        order = [str(item).strip().upper() for item in self.config.patrol_order if str(item).strip()]
        normalized = shelf_id.strip().upper()
        if normalized not in order:
            return
        previous_anchor = self._last_shelf_anchor
        self._last_shelf_anchor = normalized
        if normalized in {"A4", "B1"}:
            self._cruise_vision_suppressed_until_boundary = True
            self._stop_cruise_scanner()
            self.store.record_motion_debug(
                "cruise_vision_suppressed",
                f"识别到 {normalized} 后进入跨列转场，移动视觉暂时关闭，直到下一次边界转向完成。",
                evidence={
                    "shelf_id": normalized,
                    "phase": self._patrol_phase_label(),
                },
            )
        else:
            self._cruise_vision_suppressed_until_boundary = False
        if normalized == "B3" and previous_anchor != "B3":
            self._bypassed_for_shelf_anchors.discard(normalized)
        if self._observed_shelf_sequence and self._observed_shelf_sequence[-1] == normalized:
            return
        index = order.index(normalized)
        if not self._observed_shelf_sequence:
            if index == 0:
                self._observed_shelf_sequence.append(normalized)
            return
        previous_index = order.index(self._observed_shelf_sequence[-1])
        if index > previous_index:
            self._observed_shelf_sequence.append(normalized)
        elif index == 0:
            self._observed_shelf_sequence = [normalized]
        else:
            return
        if normalized == order[-1]:
            self._complete_observed_cycle(order)

    def _record_empty_vision_scan(self) -> None:
        self._empty_vision_scans += 1
        threshold = max(0, int(self.config.camera_failure_scan_threshold))
        if threshold <= 0 or self._empty_vision_scans < threshold:
            return
        now = time.monotonic()
        cooldown = max(0.0, float(self.config.camera_failure_request_cooldown_seconds))
        if now - self._last_camera_fallback_at < cooldown:
            return
        self._last_camera_fallback_at = now
        order = [str(item).strip().upper() for item in self.config.patrol_order if str(item).strip()]
        self.store.record_camera_cycle_fallback_request(
            observed_shelves=list(self._observed_shelf_sequence),
            expected_shelves=order,
            failed_scans=self._empty_vision_scans,
        )

    def confirm_camera_cycle_fallback(self) -> int:
        order = [str(item).strip().upper() for item in self.config.patrol_order if str(item).strip()]
        observed = list(self._observed_shelf_sequence)
        missed = [shelf_id for shelf_id in order if shelf_id not in set(observed)]
        cycle = self._current_cycle()
        self.store.confirm()
        self.store.record_cycle_completed(cycle, observed, missed)
        next_cycle = cycle + 1
        self.store.record_cycle(next_cycle, self._skip_shortage_for_cycle(next_cycle))
        self._observed_shelf_sequence = []
        self._empty_vision_scans = 0
        return next_cycle

    def _complete_observed_cycle(self, order: list[str]) -> None:
        observed = list(self._observed_shelf_sequence)
        missed = [shelf_id for shelf_id in order if shelf_id not in set(observed)]
        if len(missed) > max(0, int(self.config.cycle_max_missed_shelves)):
            return
        cycle = self._current_cycle()
        self.store.record_cycle_completed(cycle, observed, missed)
        next_cycle = cycle + 1
        self.store.record_cycle(next_cycle, self._skip_shortage_for_cycle(next_cycle))
        self._observed_shelf_sequence = []

    def _current_cycle(self) -> int:
        try:
            return max(1, int(self.store.snapshot().get("patrol_cycle", 1)))
        except (TypeError, ValueError):
            return 1

    def _skip_shortage_for_cycle(self, cycle: int) -> bool:
        return max(1, int(cycle)) <= max(0, int(self.config.skip_scan_cycles))

    def _signal_scan_events(self, events: list[EventRecord]) -> None:
        waiting = [event for event in events if event.get("status") == "waiting_confirm"]
        missing_alerts = [
            event
            for event in events
            if event.get("type") == "missing_item" and event.get("status") in {"warning", "waiting_confirm"}
        ]
        if not waiting and not missing_alerts:
            return
        self._trigger_orange_flash()
        self.store.record_light_cue("orange", "扫描到货架/物品异常，橙色指示灯已触发。")
        if missing_alerts:
            self._play_missing_alert(missing_alerts)

    def _play_missing_alert(self, events: list[EventRecord]) -> None:
        missing = [event for event in events if event.get("type") == "missing_item"]
        if not missing:
            return
        now = time.monotonic()
        cooldown = max(0.0, float(self.config.missing_alert_cooldown_seconds))
        if now - self._last_missing_alert_at < cooldown:
            return
        self._last_missing_alert_at = now
        names = [str(event.get("item") or "物品") for event in missing[:3]]
        shelf_id = str(missing[0].get("shelf_id") or self.store.snapshot().get("current_shelf") or "当前货架")
        if len(missing) > 3:
            names.append(f"等 {len(missing)} 项")
        spoken = f"检测到 {_spoken_shelf_id(shelf_id)} 缺少 {'、'.join(names)}。"
        payload, status = start_spoken_message(self.store.root, spoken)
        error = None if status == 200 else str(payload.get("error", "speech alert failed"))
        self.store.record_audio_cue("missing_item", spoken if error is None else f"缺货语音报警失败: {error}", error)

    def _wait_for_obstacle_clear(self, shelf_id: str | None) -> bool:
        started_at = time.monotonic()
        deadline = started_at + self.config.obstacle_wait_seconds
        while not self._stop_event.is_set() and time.monotonic() < deadline:
            if not self._interruptible_sleep(self.config.poll_seconds):
                return False
            distance_mm = self.sensors.read_distance_mm()
            if distance_mm is not None and distance_mm >= self.config.clear_distance_mm:
                self._blocked_count = 0
                self._obstacle_active = False
                self.alarm.clear_alarm()
                self.store.record_obstacle(distance_mm, False)
                return True
        return self._avoid_to_safe_side(shelf_id)

    def _avoid_to_safe_side(
        self,
        shelf_id: str | None,
        *,
        side_clearance_bodies: float | None = None,
        parallel_bodies: float | None = None,
        return_bodies: float | None = None,
    ) -> bool:
        side_clearance = self.config.avoidance_side_clearance_bodies if side_clearance_bodies is None else side_clearance_bodies
        parallel = self.config.avoidance_parallel_bodies if parallel_bodies is None else parallel_bodies
        return_to_line = self.config.avoidance_return_bodies if return_bodies is None else return_bodies
        direction = self.config.avoidance_turn_direction.strip().lower()
        if direction not in {"left", "right"}:
            logger.warning("invalid avoidance turn direction %r; using right", direction)
            self.store.record_motion_debug(
                "avoidance_direction_fallback",
                f"避障转向配置无效：{direction}，已使用 right。",
                status="AVOIDING_OBSTACLE",
                evidence={"configured_direction": direction, "fallback": "right"},
            )
            direction = "right"
        turn_left = direction != "right"
        away_label = "left" if turn_left else "right"
        back_label = "right" if turn_left else "left"
        self.motion.stop()
        self.store.record_avoidance_step(f"turn_{away_label}_to_safe_side", nested_level=0)
        if not _turn_succeeded(self._turn_90(away_label)):
            return False
        if not self._avoidance_path_clear("after_first_turn"):
            return False

        if not self._avoidance_forward("side_clearance_forward", side_clearance):
            return False

        self.store.record_avoidance_step(f"turn_{back_label}_to_original_heading", nested_level=0)
        if not _turn_succeeded(self._turn_90(back_label)):
            return False
        if not self._avoidance_path_clear("after_restore_heading"):
            return False

        if not self._avoidance_forward("forward_past_obstacle", parallel):
            return False

        self.store.record_avoidance_step(f"turn_{back_label}_return_to_line", nested_level=0)
        if not _turn_succeeded(self._turn_90(back_label)):
            return False
        if not self._avoidance_path_clear("after_return_turn"):
            return False

        if not self._avoidance_forward("return_to_patrol_line", return_to_line):
            return False

        self.store.record_avoidance_step(f"turn_{away_label}_restore_heading", nested_level=0)
        if not _turn_succeeded(self._turn_90(away_label)):
            return False
        distance_mm = self.sensors.read_distance_mm()
        if distance_mm is None or self._distance_clear(distance_mm):
            self._blocked_count = 0
            self._obstacle_active = False
            self.alarm.clear_alarm()
            self.store.record_obstacle(distance_mm, False)
            return True
        self.store.record_obstacle(distance_mm, True, waiting_seconds=0)
        return False

    def _avoidance_forward(self, step: str, body_multiplier: float = 1.0) -> bool:
        self.store.record_avoidance_step(step, nested_level=0)
        duration_seconds = self.config.avoidance_body_seconds * max(0.0, float(body_multiplier))
        self._forward_step(
            speed=self.config.avoidance_speed,
            duration_seconds=duration_seconds,
            watch_boundary=False,
            heading_hold=False,
            source="obstacle",
        )
        return self._avoidance_path_clear(f"after_{step}")

    def _avoidance_path_clear(self, step: str) -> bool:
        distance_mm = self.sensors.read_distance_mm()
        if not self._distance_blocked(distance_mm):
            return True
        self.store.record_avoidance_step(f"blocked_{step}", nested_level=1)
        self.store.record_obstacle(distance_mm, True, waiting_seconds=0)
        self.motion.stop()
        return False

    def _distance_blocked(self, distance_mm: int | None) -> bool:
        return distance_mm is not None and distance_mm < self.config.blocked_distance_mm

    def _distance_clear(self, distance_mm: int | None) -> bool:
        return distance_mm is not None and distance_mm >= self.config.clear_distance_mm

    def _next_boundary_action(self) -> str:
        anchor = self._last_shelf_anchor
        if anchor == "B3" and anchor not in self._bypassed_for_shelf_anchors:
            return "bypass"
        return "turn_patrol"

    def _advance_boundary_event(self) -> None:
        self._boundary_event_index += 1

    def _boundary_action_message(self, action: str) -> str:
        if action == "bypass":
            return "最近识别到 B3：判定进入B列禁区段，停车后按障碍绕行流程绕过，再继续B列巡逻。"
        anchor = self._last_shelf_anchor
        if anchor == "A4":
            return "最近识别到 A4：判定已到A列末端，黑胶带只触发90度转向，直行前往B列。"
        if anchor == "B1":
            return "最近识别到 B1：判定已到B列起点，黑胶带只触发90度转向，直行前往A列。"
        if anchor == "B3":
            return "最近识别到 B3 且禁区已处理：黑胶带只作为行驶转向触发。"
        if anchor is not None:
            return f"最近识别到 {anchor}：黑胶带只作为行驶转向触发，不参与位置计数。"
        return "尚未识别到货架锚点：黑胶带按保守行驶转向处理，不参与位置计数。"

    def _patrol_phase_label(self) -> str:
        if self._line_follow_active:
            return "寻线过渡"
        anchor = self._last_shelf_anchor
        if anchor in {"A1", "A2", "A3"}:
            return "A列巡逻"
        if anchor == "A4":
            return "A列末端到B列转场"
        if anchor == "B3" and anchor not in self._bypassed_for_shelf_anchors:
            return "B列禁区前"
        if anchor in {"B3", "B2"}:
            return "B列短步巡逻"
        if anchor == "B1":
            return "B列起点到A列转场"
        return "未识别货架锚点巡逻"

    def _safe_side_for_shelf(self, shelf_id: str | None) -> str | None:
        if shelf_id is None:
            return None
        point = self.warehouse_map["shelf_points"].get(shelf_id)
        if point is None:
            return None
        return str(point["safe_side"]).upper()

    def _move_between(self, current: Cell, target: Cell, heading: str, target_heading: str) -> str:
        dx = target[0] - current[0]
        dy = target[1] - current[1]
        if abs(dx) + abs(dy) != 1:
            raise ValueError(f"non-adjacent waypoint transition: {current} -> {target}")
        if heading not in HEADINGS or target_heading not in HEADINGS:
            if not self._turn_to_heading(heading, target_heading):
                return heading
            self._forward_step(
                speed=self.config.patrol_speed,
                duration_seconds=self.config.step_seconds,
                settle_seconds=self.config.patrol_settle_seconds,
            )
            return target_heading
        delta = (HEADINGS.index(target_heading) - HEADINGS.index(heading)) % len(HEADINGS)
        if delta == 0:
            self._forward_step(
                speed=self.config.patrol_speed,
                duration_seconds=self.config.step_seconds,
                settle_seconds=self.config.patrol_settle_seconds,
            )
        elif delta == 1:
            self.motion.strafe_right_slow(speed=self.config.patrol_speed, duration_seconds=self.config.step_seconds)
            self.motion.stop()
            self._settle(self.config.patrol_settle_seconds)
        elif delta == 2:
            if not self._turn_to_heading(heading, target_heading):
                return heading
            self._forward_step(
                speed=self.config.patrol_speed,
                duration_seconds=self.config.step_seconds,
                settle_seconds=self.config.patrol_settle_seconds,
            )
            return target_heading
        else:
            self.motion.strafe_left_slow(speed=self.config.patrol_speed, duration_seconds=self.config.step_seconds)
            self.motion.stop()
            self._settle(self.config.patrol_settle_seconds)
        return heading

    def _turn_to_heading(self, heading: str, target_heading: str) -> bool:
        if heading not in HEADINGS or target_heading not in HEADINGS:
            return True
        delta = (HEADINGS.index(target_heading) - HEADINGS.index(heading)) % len(HEADINGS)
        if delta == 1:
            return _turn_succeeded(self._turn_90("right"))
        elif delta == 2:
            return _turn_succeeded(self._turn_90("right")) and _turn_succeeded(self._turn_90("right"))
        elif delta == 3:
            return _turn_succeeded(self._turn_90("left"))
        return True

    def _forward_step(
        self,
        *,
        speed: int,
        duration_seconds: float,
        watch_boundary: bool = True,
        heading_hold: bool = True,
        settle_seconds: float | None = None,
        source: str = "patrol",
    ) -> None:
        self._log_motion_command(
            source,
            "move_forward",
            speed=speed,
            duration_seconds=duration_seconds,
        )
        self._run_timed_motion(
            self.motion.move_forward_slow,
            speed=speed,
            duration_seconds=duration_seconds,
            watch_boundary=watch_boundary,
            heading_hold=heading_hold,
        )
        self._settle(settle_seconds)

    def _turn_90(self, direction: str, *, speed: int | None = None, duration_seconds: float | None = None) -> dict[str, object] | None:
        normalized = direction.strip().lower()
        turn_speed = self.config.turn_speed if speed is None else speed
        turn_seconds = self.config.turn_90_seconds if duration_seconds is None else duration_seconds
        turn_source = "manual" if self._manual_override.is_set() else "boundary"
        imu_turn = getattr(self.imu, "turn_90_with_result", None)
        if callable(imu_turn):
            imu_result = None
            try:
                imu_result = imu_turn(normalized, self.motion, turn_speed, turn_seconds, should_abort=self._stop_event.is_set)
            except RobotHardwareError as exc:
                self.store.record_robot_status("TURNING_AT_BOUNDARY", f"MPU6050 turn skipped: {exc}")
            except TypeError:
                try:
                    imu_result = imu_turn(normalized, self.motion, turn_speed, turn_seconds)
                except RobotHardwareError as exc:
                    self.store.record_robot_status("TURNING_AT_BOUNDARY", f"MPU6050 turn skipped: {exc}")
            if imu_result is not None:
                self.motion.stop()
                self._settle()
                result = dict(imu_result) if isinstance(imu_result, dict) else {"ok": bool(imu_result)}
                self._record_gyro_turn_result(result)
                self._log_motion_command(
                    turn_source,
                    f"turn_90_{normalized}",
                    speed=turn_speed,
                    duration_seconds=turn_seconds,
                    source_detail=result.get("source"),
                    ok=result.get("ok"),
                )
                self.refresh_motion_sensor(force=True)
                if not result.get("ok"):
                    message = str(
                        result.get("message")
                        or "MPU6050 closed-loop 90 degree turn did not converge within correction attempts."
                    )
                    self.store.record_robot_status("ERROR", f"{message} Keeping car stopped.")
                return result
        else:
            legacy_imu_turn = getattr(self.imu, "turn_90", None)
            if callable(legacy_imu_turn):
                try:
                    legacy_result = legacy_imu_turn(normalized, self.motion, turn_speed, turn_seconds)
                    if legacy_result is not None:
                        self.motion.stop()
                        self._settle()
                        self._log_motion_command(
                            turn_source,
                            f"turn_90_{normalized}",
                            speed=turn_speed,
                            duration_seconds=turn_seconds,
                            source_detail="mpu6050_legacy",
                        )
                        return {"ok": bool(legacy_result), "source": "mpu6050_legacy", "direction": normalized}
                except RobotHardwareError as exc:
                    self.store.record_robot_status("TURNING_AT_BOUNDARY", f"MPU6050 turn skipped: {exc}")
        if normalized == "left":
            self._log_motion_command(
                turn_source,
                "rotate_left",
                speed=turn_speed,
                duration_seconds=turn_seconds,
                source_detail="open_loop",
            )
            self.motion.rotate_left_slow(
                speed=turn_speed,
                duration_seconds=turn_seconds,
            )
        elif normalized == "right":
            self._log_motion_command(
                turn_source,
                "rotate_right",
                speed=turn_speed,
                duration_seconds=turn_seconds,
                source_detail="open_loop",
            )
            self.motion.rotate_right_slow(
                speed=turn_speed,
                duration_seconds=turn_seconds,
            )
        else:
            raise ValueError(f"unsupported turn direction: {direction}")
        self.motion.stop()
        self._settle()
        result = {
            "ok": True,
            "source": "open_loop",
            "direction": normalized,
            "target_degrees": 90.0,
            "final_degrees": None,
            "error_degrees": None,
            "attempts": 0,
            "message": "MPU6050 unavailable; used calibrated open-loop turn duration.",
        }
        self._record_gyro_turn_result(result)
        self.refresh_motion_sensor(force=True)
        return result

    def _record_gyro_turn_result(self, result: Mapping[str, object]) -> None:
        evidence = dict(result)
        ok = bool(evidence.get("ok"))
        direction = str(evidence.get("direction") or "-")
        source = str(evidence.get("source") or "unknown")
        final_degrees = evidence.get("final_degrees")
        error_degrees = evidence.get("error_degrees")
        self.store.record_motion_debug(
            "gyro_turn_closed_loop",
            (
                f"90度转向{'收敛' if ok else '未收敛'}：direction={direction}, source={source}, "
                f"final={final_degrees}, error={error_degrees}。"
            ),
            status="TURNING_AT_BOUNDARY" if ok else "ERROR",
            evidence=evidence,
        )

    def _log_motion_command(
        self,
        source: str,
        command: str,
        *,
        speed: int | None = None,
        duration_seconds: float | None = None,
        **extra: object,
    ) -> None:
        """Record a single structured trace of every motor output.

        The cruise regression (commit 7f7466e) made it impossible to tell which
        ``source`` was writing the wheels at any given tick — cruise, heading
        hold, manual, boundary and obstacle paths all call into the motion
        module directly. This helper gives every one of those call sites a
        single uniform record so the on-car motion log can answer "who issued
        this rotate_*?" and "was manual override armed?" without ambiguity.
        """

        self.store.record_motion_debug(
            "motion_command",
            (
                f"motor cmd: source={source}, command={command}, speed={speed}, "
                f"duration={duration_seconds}, manual_override={self._manual_override.is_set()}"
            ),
            evidence={
                "source": source,
                "command": command,
                "speed": speed,
                "duration_seconds": duration_seconds,
                "manual_override": self._manual_override.is_set(),
                **extra,
            },
        )

    def _settle(self, seconds: float | None = None) -> None:
        delay = self.config.action_settle_seconds if seconds is None else seconds
        delay = max(0.0, float(delay))
        if delay > 0:
            self._interruptible_sleep(delay)

    def _interruptible_sleep(self, seconds: float) -> bool:
        deadline = time.monotonic() + max(0.0, float(seconds))
        while not self._stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            time.sleep(min(remaining, 0.01))
        self.motion.stop()
        return False

    def _reset_heading_guard(self) -> None:
        self._heading_consecutive_count = 0
        self._heading_over_tolerance_count = 0
        guard = self._heading_guard
        if guard is not None:
            reset = getattr(guard, "reset", None)
            if callable(reset):
                reset()

    def _heading_guard_instance(self) -> Any | None:
        if not self.config.heading_hold_enabled:
            return None
        if self._heading_guard is not None:
            return self._heading_guard
        opener = getattr(self.imu, "open_straight_heading_guard", None)
        if not callable(opener):
            return None
        try:
            self._heading_guard = opener()
        except (RobotHardwareError, OSError, AttributeError, TypeError, ValueError):
            self._heading_guard = None
        return self._heading_guard

    def _integrate_guard_yaw(
        self,
        action: Callable[[float], None],
        *,
        seconds: float,
        slice_seconds: float = 0.05,
    ) -> float | None:
        """Drive ``action`` in short slices while integrating signed yaw.

        Uses the real ``StraightHeadingGuard`` (so ``yaw_sign``/deadband are
        applied exactly as the controller sees them) and returns the total
        integrated heading in degrees, or ``None`` if no IMU guard is available.
        """

        guard = self._heading_guard_instance()
        if guard is None:
            return None
        reset = getattr(guard, "reset", None)
        updater = getattr(guard, "update", None)
        if not callable(updater):
            return None
        if callable(reset):
            reset()
        updater()  # seed last_sample_at so the first slice integrates a real dt
        slice_seconds = max(0.01, float(slice_seconds))
        deadline = time.monotonic() + max(0.0, float(seconds))
        while time.monotonic() < deadline and not self._stop_event.is_set():
            action(slice_seconds)
            updater()
        self.motion.stop()
        return float(getattr(guard, "heading_degrees", 0.0))

    def run_heading_polarity_selfcheck(
        self,
        *,
        turn_speed: int | None = None,
        forward_speed: int | None = None,
        seconds: float = 0.6,
    ) -> dict[str, object]:
        """On-car self-check that locks in the heading-hold correction polarity.

        Rotates left briefly (gyro-sign check) then differential-steers right
        briefly (actuation-sign check), measuring integrated yaw through the
        real guard, and reports whether the correction is corrective plus the
        recommended ``MPU6050_YAW_SIGN`` / ``HEADING_HOLD_INVERT``. Needs robot
        mode with a live MPU6050, and a bit of clear floor ahead.
        """

        if not self.config.heading_hold_enabled:
            return {"ok": False, "error": "heading hold disabled (HEADING_HOLD_ENABLED=0)"}
        if self._heading_guard_instance() is None:
            return {"ok": False, "error": "no IMU heading guard (need MPU6050 in robot mode)"}
        turn_spd = int(self.config.turn_speed if turn_speed is None else turn_speed)
        fwd_spd = int(self.config.cruise_speed if forward_speed is None else forward_speed)
        correction = max(
            int(self.config.heading_hold_min_correction_speed),
            int(round(fwd_spd * 0.4)),
        )
        self.request_manual_override()
        try:
            left_yaw = self._integrate_guard_yaw(
                lambda s: self.motion.rotate_left_slow(speed=turn_spd, duration_seconds=s),
                seconds=seconds,
            )
            self._settle()
            forward_right_yaw = self._integrate_guard_yaw(
                lambda s: self.motion.move_forward_corrected_slow(
                    speed=fwd_spd, correction=correction, direction="right", duration_seconds=s
                ),
                seconds=seconds,
            )
            self._settle()
        finally:
            self.motion.stop()
            self.release_manual_override()
        if left_yaw is None or forward_right_yaw is None:
            return {"ok": False, "error": "IMU integration unavailable during self-check"}
        yaw_sign_reader = getattr(mpu6050, "_yaw_sign", None)
        current_yaw_sign = float(yaw_sign_reader()) if callable(yaw_sign_reader) else 1.0
        check = mpu6050.evaluate_heading_polarity(
            left_yaw,
            forward_right_yaw,
            current_yaw_sign=current_yaw_sign,
            current_invert=self.config.heading_hold_invert,
        )
        result: dict[str, object] = {
            "ok": check.ok,
            "left_turn_yaw_deg": round(check.left_turn_yaw_deg, 2),
            "forward_right_yaw_deg": round(check.forward_right_yaw_deg, 2),
            "gyro_sign_ok": check.gyro_sign_ok,
            "differential_sign_ok": check.differential_sign_ok,
            "recommended_yaw_sign": check.recommended_yaw_sign,
            "recommended_invert": check.recommended_invert,
            "message": check.message,
        }
        self.store.record_motion_debug(
            "heading_polarity_selfcheck",
            check.message,
            status="MANUAL_CONTROL",
            evidence=result,
        )
        return result

    def _heading_hold_settings(self, fallback_speed: int | None = None) -> HeadingHoldSettings:
        hold_fallback_speed = self.config.cruise_speed if fallback_speed is None else int(fallback_speed)
        return HeadingHoldSettings(
            enabled=self.config.heading_hold_enabled,
            tolerance_degrees=self.config.heading_hold_tolerance_deg,
            gain=self.config.heading_hold_gain,
            min_pulse_seconds=self.config.heading_hold_min_pulse_seconds,
            max_pulse_seconds=self.config.heading_hold_max_pulse_seconds,
            correction_speed=self.config.heading_hold_correction_speed,
            fallback_speed=hold_fallback_speed,
            invert=self.config.heading_hold_invert,
            rate_damping=self.config.heading_hold_rate_damping,
            speed_gain=self.config.heading_hold_speed_gain,
            min_correction_speed=self.config.heading_hold_min_correction_speed,
        )

    def _heading_hold_correction(self, fallback_speed: int | None = None) -> HeadingHoldCorrection | None:
        guard = self._heading_guard_instance()
        if self._stop_event.is_set() or self._manual_override.is_set():
            return None
        if guard is None:
            self._record_heading_hold_sample("guard_unavailable")
            return None
        now = time.monotonic()
        last_sample = getattr(guard, "last_sample_at", None)
        if last_sample is not None:
            min_sample_interval = max(0.0, float(self.config.heading_hold_min_sample_interval_seconds))
            if now - float(last_sample) < min_sample_interval:
                self._record_heading_hold_sample("sample_too_fresh")
                return None
        updater = getattr(guard, "update", None)
        if not callable(updater):
            self._record_heading_hold_sample("guard_missing_update")
            return None
        try:
            deviation = float(updater())
        except (RobotHardwareError, OSError, AttributeError, TypeError, ValueError):
            self._heading_guard = None
            self._record_heading_hold_sample("sample_error")
            return None
        tolerance = max(0.0, float(self.config.heading_hold_tolerance_deg))
        try:
            rate = float(getattr(guard, "last_rate_dps", 0.0))
        except (TypeError, ValueError):
            rate = 0.0
        if abs(deviation) <= tolerance:
            self._heading_consecutive_count = 0
            self._heading_over_tolerance_count = 0
            self._record_heading_hold_sample(
                "within_deadband",
                deviation_degrees=deviation,
                rate_dps=rate,
                tolerance_degrees=tolerance,
            )
            return None
        min_interval = max(0.0, float(self.config.heading_hold_min_interval_seconds))
        if min_interval > 0.0 and now - self._last_heading_correction_at < min_interval:
            self._record_heading_hold_sample(
                "correction_throttled",
                deviation_degrees=deviation,
                rate_dps=rate,
                tolerance_degrees=tolerance,
                min_interval_seconds=min_interval,
            )
            return None
        max_consecutive = max(0, int(self.config.heading_hold_max_consecutive))
        if max_consecutive > 0 and self._heading_consecutive_count >= max_consecutive:
            self._heading_consecutive_count = 0
            self._heading_over_tolerance_count = 0
            self._record_heading_hold_sample(
                "consecutive_cooldown",
                deviation_degrees=deviation,
                rate_dps=rate,
                tolerance_degrees=tolerance,
                max_consecutive=max_consecutive,
            )
            return None
        self._heading_over_tolerance_count += 1
        confirm_samples = max(1, int(self.config.heading_hold_confirm_samples))
        if self._heading_over_tolerance_count < confirm_samples:
            self._record_heading_hold_sample(
                "waiting_confirm",
                deviation_degrees=deviation,
                rate_dps=rate,
                tolerance_degrees=tolerance,
                confirm_count=self._heading_over_tolerance_count,
                confirm_samples=confirm_samples,
            )
            return None
        try:
            correction = compute_heading_hold_correction(
                guard,
                self._heading_hold_settings(fallback_speed),
                stop_requested=lambda: self._stop_event.is_set() or self._manual_override.is_set(),
                deviation_degrees=deviation,
            )
        except (RobotHardwareError, OSError, AttributeError, TypeError, ValueError):
            self._heading_guard = None
            self._record_heading_hold_sample("controller_error")
            return None
        if correction is None:
            self._record_heading_hold_sample(
                "controller_rejected",
                deviation_degrees=deviation,
                rate_dps=rate,
                tolerance_degrees=tolerance,
            )
            return None
        self._last_heading_correction_at = now
        self._heading_consecutive_count += 1
        self._heading_over_tolerance_count = 0
        return correction

    def _record_heading_hold_sample(self, reason: str, **evidence: object) -> None:
        interval = max(0.0, float(self.config.heading_hold_trace_interval_seconds))
        now = time.monotonic()
        if interval > 0 and now - self._last_heading_sample_log_at < interval:
            return
        self._last_heading_sample_log_at = now
        self.store.record_motion_debug(
            "heading_hold_sample",
            f"直行纠偏采样：{reason}。",
            evidence={
                "reason": reason,
                "enabled": self.config.heading_hold_enabled,
                "phase": self._patrol_phase_label(),
                "consecutive_count": self._heading_consecutive_count,
                **evidence,
            },
        )

    def _forward_mover_for_correction(self, correction: HeadingHoldCorrection | None) -> Callable[..., None]:
        if correction is None:
            return self.motion.move_forward_slow
        corrected = getattr(self.motion, "move_forward_corrected_slow", None)
        if not callable(corrected):
            return self.motion.move_forward_slow

        def mover(*, speed: int, duration_seconds: float) -> None:
            corrected(
                speed=speed,
                correction=correction.correction_speed,
                direction=correction.direction,
                duration_seconds=duration_seconds,
            )

        return mover

    def _call_mover_with_correction(
        self,
        mover: Callable[..., None],
        *,
        speed: int,
        duration_seconds: float,
        correction: HeadingHoldCorrection | None,
    ) -> None:
        if correction is not None and getattr(mover, "__name__", "") == "move_forward_slow":
            self._forward_mover_for_correction(correction)(speed=speed, duration_seconds=duration_seconds)
            return
        mover(speed=speed, duration_seconds=duration_seconds)

    def _apply_heading_hold(self) -> None:
        correction = self._heading_hold_correction(self.config.cruise_speed)
        if correction is None:
            return
        self._forward_mover_for_correction(correction)(speed=self.config.cruise_speed, duration_seconds=0.0)
        self._record_heading_hold_correction(correction, speed=self.config.cruise_speed)

    def _record_heading_hold_correction(self, correction: HeadingHoldCorrection, *, speed: int) -> None:
        self._log_motion_command(
            "heading_hold",
            "move_forward_corrected",
            speed=speed,
            correction=correction.correction_speed,
            direction=correction.direction,
            deviation=round(correction.deviation_degrees, 3),
            rate=round(correction.rate_dps, 3),
            effective=round(correction.effective_degrees, 3),
            fresh_sample=True,
        )
        self.store.record_motion_debug(
            "heading_hold_correction",
            "heading hold forward wheel-speed correction applied.",
            evidence={
                "deviation_degrees": round(correction.deviation_degrees, 3),
                "rate_dps": round(correction.rate_dps, 3),
                "effective_degrees": round(correction.effective_degrees, 3),
                "direction": correction.direction,
                "correction_speed": correction.correction_speed,
                "consecutive_count": self._heading_consecutive_count,
            },
        )

    def _zupt_recalibrate(self, reason: str) -> None:
        """Re-estimate gyro bias while the car is stopped (zero-velocity update).

        This is the main defence against gyro zero-drift: the heading integrator's
        bias would otherwise wander with temperature and make the car curve while
        "holding straight". Only meaningful when a real heading guard is open.
        """

        if not (self.config.heading_zupt_enabled and self.config.heading_hold_enabled):
            return
        guard = self._heading_guard_instance()
        if guard is None:
            return
        recalibrate = getattr(guard, "recalibrate_bias", None)
        if not callable(recalibrate):
            return
        try:
            updated = bool(
                recalibrate(
                    samples=self.config.heading_zupt_samples,
                    sample_seconds=self.config.heading_zupt_sample_seconds,
                )
            )
        except (RobotHardwareError, OSError, AttributeError, TypeError, ValueError):
            self._heading_guard = None
            return
        self.store.record_motion_debug(
            "heading_zupt",
            f"陀螺仪零漂重标定（{reason}）：{'已更新偏置' if updated else '读数不稳定，跳过'}。",
            evidence={"reason": reason, "updated": updated, "samples": self.config.heading_zupt_samples},
        )

    def _show_normal(self) -> None:
        try:
            self.alarm.show_normal()
        except RobotHardwareError:
            pass

    def _show_line_follow(self) -> None:
        indicator = getattr(self.alarm, "show_line_follow", None)
        try:
            if callable(indicator):
                indicator()
            else:
                self.alarm.show_normal()
        except RobotHardwareError:
            pass

    def _update_indicator_light(self) -> None:
        """Set the base status colour, but hold the orange flash while it is active.

        The orange recognition flash is applied non-blocking (a timed window) so
        the control loop never sleeps just to blink — we simply refrain from
        overwriting orange with the base colour until the window elapses.
        """

        if time.monotonic() < self._orange_flash_until:
            return
        self._show_line_follow() if self._line_follow_active else self._show_normal()

    def _trigger_orange_flash(self) -> None:
        flash = getattr(self.alarm, "show_recognition", None)
        if not callable(flash):
            return
        try:
            flash()
        except RobotHardwareError:
            return
        self._orange_flash_until = time.monotonic() + max(0.0, float(self.config.cruise_recognition_flash_seconds))

    def _start_cruise_scanner(self) -> None:
        if not self.config.cruise_vision_enabled or self._cruise_scanner is not None:
            return
        scanner = _CruiseVisionScanner(
            provider=self.detection_provider,
            config=self.config,
            enrich=self._enrich_detection,
            stop_event=self._stop_event,
        )
        scanner.start()
        self._cruise_scanner = scanner

    def _cruise_scanner_allowed_for_phase(self) -> bool:
        if not self.config.cruise_vision_enabled:
            return False
        return not self._cruise_vision_suppressed_until_boundary

    def _sync_cruise_scanner_for_phase(self) -> None:
        if self._cruise_scanner_allowed_for_phase():
            self._start_cruise_scanner()
        else:
            self._stop_cruise_scanner()

    def _stop_cruise_scanner(self) -> None:
        self._flush_cruise_shelf_segment("scanner_stopped")
        scanner = self._cruise_scanner
        if scanner is None:
            return
        self._cruise_scanner = None
        scanner.stop()

    def _handle_cruise_recognitions(self) -> bool:
        scanner = self._cruise_scanner
        if scanner is None:
            return False
        if self._cruise_vision_suppressed_until_boundary:
            suppressed = len(scanner.poll_new())
            if suppressed:
                self.store.record_motion_debug(
                    "cruise_recognition_suppressed",
                    "跨列转场期间已丢弃移动识别结果，保持连续直行直到边界。",
                    evidence={
                        "suppressed_count": suppressed,
                        "phase": self._patrol_phase_label(),
                    },
                )
            return False
        for recognition in scanner.poll_new():
            self._stop_for_cruise_recognition(recognition)
            return True
        return False

    def _stop_for_cruise_recognition(self, recognition: Mapping[str, object]) -> None:
        recognition = self._enrich_detection(recognition)
        shelf_id = _optional_text(recognition.get("shelf_id"))
        if shelf_id is not None:
            shelf_id = shelf_id.strip().upper()
        if not shelf_id:
            shelf_id = str(self.store.snapshot().get("current_shelf") or "UNKNOWN").strip().upper() or "UNKNOWN"
        self.motion.stop()
        self._stop_cruise_scanner()
        self.store.record_motion_debug(
            "cruise_recognition_stop",
            "移动中识别到货架/物品标记：已立即停车，执行完整停车扫描。",
            status="SCANNING_SHELF",
            evidence={
                "shelf_id": shelf_id,
                "item_id": _optional_text(recognition.get("item_id")),
                "tag_id": _optional_text(recognition.get("tag_id")),
                "phase": self._patrol_phase_label(),
            },
        )
        self._interruptible_sleep(max(0.0, float(self.config.object_settle_seconds)))
        if self._stop_event.is_set():
            return
        self._perform_scan(shelf_id, f"{shelf_id}_SCAN")
        self.motion.stop()
        self._reset_heading_guard()
        self._zupt_recalibrate("post_cruise_recognition_scan")
        self._last_object_presence_at = time.monotonic()

    def _handle_moving_recognition(self, recognition: Mapping[str, object]) -> None:
        """React to a shelf/item recognised while cruising, without stopping.

        Flashes the orange marker (extra visual cue on top of the audio one),
        keeps the perceptual cycle bookkeeping alive, and logs a lightweight
        detection. Item ownership is determined by shelf boundaries: items are
        buffered after a shelf tag and committed to that shelf only when the next
        shelf tag appears.
        """

        recognition = self._enrich_detection(recognition)
        if self._cruise_vision_suppressed_until_boundary:
            self.store.record_motion_debug(
                "cruise_recognition_suppressed",
                "跨列转场期间忽略移动识别结果，避免视觉流程打断直行。",
                evidence={
                    "phase": self._patrol_phase_label(),
                    "shelf_id": _optional_text(recognition.get("shelf_id")),
                    "item_id": _optional_text(recognition.get("item_id")),
                },
            )
            return

        shelf_id = _optional_text(recognition.get("shelf_id"))
        item_id = _optional_text(recognition.get("item_id"))
        if shelf_id is not None:
            shelf_id = shelf_id.strip().upper()
            if not shelf_id:
                shelf_id = None
        if item_id is not None:
            self._buffer_cruise_item(recognition, item_id)
        if shelf_id is not None:
            self._advance_cruise_shelf_segment(shelf_id)
        key = shelf_id or item_id
        if key is None:
            return
        now = time.monotonic()
        cooldown = max(0.0, float(self.config.cruise_recognition_cooldown_seconds))
        last_at = self._recognition_last_at.get(key)
        if last_at is not None and now - last_at < cooldown:
            return
        self._recognition_last_at[key] = now
        self._trigger_orange_flash()
        self.store.record_motion_debug(
            "cruise_recognition",
            (
                f"移动中识别到{'货架 ' + shelf_id if shelf_id else '物品 ' + str(item_id)}，"
                "闪烁橙灯并继续匀速巡航（不停车）。"
            ),
            evidence={
                "shelf_id": shelf_id,
                "item_id": item_id,
                "tag_id": _optional_text(recognition.get("tag_id")),
            },
        )
        if shelf_id is not None:
            self._play_cue("first", f"识别到 {shelf_id} 货架。")
        else:
            self._play_cue("following", f"识别到物品 {item_id}。")

    def _advance_cruise_shelf_segment(self, shelf_id: str) -> None:
        if self._cruise_active_shelf == shelf_id:
            return
        if self._cruise_active_shelf is not None:
            self._flush_cruise_shelf_segment("next_shelf_detected")
        self._cruise_active_shelf = shelf_id
        self._cruise_segment_items = {}
        self.store.record_shelf_arrival(shelf_id, target=f"{shelf_id}_SCAN")
        self._record_observed_shelf(shelf_id)

    def _buffer_cruise_item(self, recognition: Mapping[str, object], item_id: str) -> None:
        if self._cruise_active_shelf is None:
            self.store.record_motion_debug(
                "cruise_item_unassigned",
                f"移动中识别到物品 {item_id}，但尚未检测到所属货架，暂不归属。",
                evidence={
                    "item_id": item_id,
                    "tag_id": _optional_text(recognition.get("tag_id")),
                },
            )
            return
        self._cruise_segment_items.setdefault(
            item_id,
            {
                "tag_id": _optional_text(recognition.get("tag_id")),
                "kind": "item",
                "item_id": item_id,
            },
        )

    def _flush_cruise_shelf_segment(self, reason: str) -> None:
        shelf_id = self._cruise_active_shelf
        if shelf_id is None:
            return
        frame_id = f"cruise-{shelf_id.lower()}-{int(time.time())}"
        item_ids = list(self._cruise_segment_items)
        events = self.store.record_scan_result(shelf_id, item_ids, frame_id=frame_id)
        self.store.record_motion_debug(
            "cruise_segment_committed",
            f"{shelf_id} 货架巡视段已结算，共识别 {len(item_ids)} 个物品。",
            evidence={
                "shelf_id": shelf_id,
                "item_ids": item_ids,
                "reason": reason,
                "frame_id": frame_id,
            },
        )
        self._signal_scan_events(events)
        self._cruise_segment_items = {}
        self._cruise_active_shelf = None

    def _scan_shelf(self, step: RouteStep, heading: str) -> str:
        shelf_id = str(step["shelf_id"])
        scan_heading = step.get("heading")
        if isinstance(scan_heading, str):
            if not self._turn_to_heading(heading, scan_heading):
                return heading
            heading = scan_heading
        self.motion.stop()
        self._perform_scan(shelf_id, step["target"])
        return heading

    def _perform_scan(
        self,
        shelf_id: str,
        target: str,
        detections: list[dict[str, object]] | None = None,
    ) -> None:
        self.store.record_shelf_arrival(shelf_id, target=target)
        self._play_cue("first", f"扫描到 {shelf_id} 货架。")
        frame_id = f"runtime-{shelf_id.lower()}-{int(time.time())}"
        self.store.record_scan_start(shelf_id, target=target, frame_id=frame_id)
        raw_detections = self._collect_detections() if detections is None else detections
        scan_detections = [self._enrich_detection(detection) for detection in raw_detections]
        events: list[EventRecord]
        if scan_detections:
            events = self.store.record_detection_evidence(shelf_id, scan_detections, frame_id=frame_id)
            if any(
                str(detection.get("kind", "item")) == "item" or detection.get("item_id")
                for detection in scan_detections
            ):
                self._play_cue("following", f"识别到 {shelf_id} 货架上的物品。")
        else:
            events = self.store.record_scan_result(shelf_id, [], frame_id=frame_id)
        self._record_observed_shelf(shelf_id)
        self._signal_scan_events(events)

    def _collect_detections(self) -> list[dict[str, object]]:
        self.store.record_motion_debug(
            "vision_scan_start",
            "Starting parked vision scan: AprilTag/OCR/color/classifier run only after the car is stopped.",
            status="SCANNING_SHELF",
            evidence={
                "camera_device": self.config.camera_device,
                "scan_timeout_seconds": self.config.scan_timeout_seconds,
                "scan_max_detections": self.config.scan_max_detections,
                "vision_stability_enabled": self.config.vision_stability_enabled,
                "image_classifier_enabled": self.config.image_classifier_enabled,
            },
        )
        try:
            iterator = self.detection_provider(
                device=self.config.camera_device,
                cooldown_seconds=0.5,
                idle_timeout_seconds=self.config.scan_timeout_seconds,
                stability_enabled=self.config.vision_stability_enabled,
                stability_min_frames=self.config.vision_min_stable_frames,
                stability_max_center_shift_px=self.config.vision_max_center_shift_px,
                stability_max_corner_shift_px=self.config.vision_max_corner_shift_px,
                stability_max_angle_delta_deg=self.config.vision_max_angle_delta_deg,
                image_classifier_enabled=self.config.image_classifier_enabled,
                vision_state_machine_enabled=self.config.vision_state_machine_enabled,
            )
        except TypeError:
            iterator = self.detection_provider(device=self.config.camera_device, cooldown_seconds=0.5)
        detections: list[dict[str, object]] = []
        try:
            for detection in itertools.islice(iterator, self.config.scan_max_detections):
                detections.append(self._enrich_detection(detection))
        except (RobotHardwareError, VisionDependencyError) as exc:
            self.store.record_robot_status("SCANNING_SHELF", f"side camera scan skipped: {exc}")
            self.store.record_motion_debug(
                "vision_scan_error",
                f"Parked vision scan skipped: {exc}",
                status="SCANNING_SHELF",
                evidence={"error": str(exc)},
            )
        summary = [self._detection_log_summary(detection) for detection in detections]
        self.store.record_motion_debug(
            "vision_scan_result",
            f"Parked vision scan finished: {len(detections)} detection(s).",
            status="SCANNING_SHELF",
            evidence={"count": len(detections), "detections": summary},
        )
        if tag_detector.ocr_enabled():
            for item in summary:
                if item.get("tag_id") and not item.get("ocr_text"):
                    self.store.record_motion_debug(
                        "vision_text_missing",
                        f"QR/AprilTag {item['tag_id']} was detected, but OCR text is empty.",
                        status="SCANNING_SHELF",
                        evidence=item,
                    )
        return detections

    def _detection_log_summary(self, detection: Mapping[str, object]) -> dict[str, object]:
        confidence = detection.get("confidence")
        try:
            confidence_value: float | None = None if confidence is None else float(confidence)
        except (TypeError, ValueError):
            confidence_value = None
        return {
            "tag_id": _optional_text(detection.get("tag_id")),
            "marker_family": _optional_text(detection.get("marker_family")),
            "kind": _optional_text(detection.get("kind")),
            "item_id": _optional_text(detection.get("item_id")),
            "shelf_id": _optional_text(detection.get("shelf_id")),
            "ocr_text": _optional_text(detection.get("ocr_text")),
            "color": _optional_text(detection.get("color")),
            "image_class": _optional_text(detection.get("image_class")),
            "source": _optional_text(detection.get("source")),
            "confidence": confidence_value,
            "has_tag": detection.get("tag_id") is not None,
            "has_ocr": bool(_optional_text(detection.get("ocr_text"))),
        }

    def _play_cue(self, cue: str, message: str) -> None:
        payload, status = start_audio_cue(self.store.root, cue)
        error = None if status == 200 else str(payload.get("error", cue))
        display_message = message if error is None else f"音频播放失败: {error}"
        self.store.record_audio_cue(cue, display_message, error)


class _CruiseVisionScanner:
    """Background recognition for the smooth-cruise mode.

    Runs the vision detector in its own thread while the car keeps moving so the
    control loop never has to stop or block for a scan. Each detection that
    resolves to a known shelf/item is queued; the control loop drains the queue
    on its own thread (single-threaded LED/audio) and reacts. On the real car the
    camera stays open across detections; the provider is re-opened whenever it
    idles out. All hardware errors are swallowed so a flaky camera cannot crash
    the patrol — the car simply cruises without the extra recognition cue.
    """

    def __init__(
        self,
        *,
        provider: DetectionProvider,
        config: RobotRuntimeConfig,
        enrich: Callable[[Mapping[str, object]], dict[str, object]],
        stop_event: threading.Event,
    ) -> None:
        self._provider = provider
        self._config = config
        self._enrich = enrich
        self._stop_event = stop_event
        self._own_stop = threading.Event()
        self._lock = threading.Lock()
        self._pending: list[dict[str, object]] = []
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="cruise-vision", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._own_stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def _should_stop(self) -> bool:
        return self._own_stop.is_set() or self._stop_event.is_set()

    def poll_new(self) -> list[dict[str, object]]:
        with self._lock:
            if not self._pending:
                return []
            items = self._pending
            self._pending = []
            return items

    def _run(self) -> None:
        reopen_seconds = max(0.0, float(self._config.cruise_vision_reopen_seconds))
        while not self._should_stop():
            try:
                iterator = self._open_iterator()
                self._ingest(iterator)
            except Exception:  # pragma: no cover - defensive against camera/vision failures
                pass
            if self._should_stop():
                return
            self._own_stop.wait(reopen_seconds)

    def _open_iterator(self) -> Iterator[Mapping[str, object]]:
        try:
            return self._provider(
                device=self._config.camera_device,
                cooldown_seconds=0.5,
                idle_timeout_seconds=self._config.scan_timeout_seconds,
                stability_enabled=self._config.vision_stability_enabled,
                stability_min_frames=self._config.vision_min_stable_frames,
                stability_max_center_shift_px=self._config.vision_max_center_shift_px,
                stability_max_corner_shift_px=self._config.vision_max_corner_shift_px,
                stability_max_angle_delta_deg=self._config.vision_max_angle_delta_deg,
                image_classifier_enabled=self._config.image_classifier_enabled,
                vision_state_machine_enabled=self._config.vision_state_machine_enabled,
            )
        except TypeError:
            return self._provider(device=self._config.camera_device, cooldown_seconds=0.5)

    def _ingest(self, iterator: Iterator[Mapping[str, object]]) -> None:
        for detection in iterator:
            if self._should_stop():
                return
            recognition = self._recognition_from(detection)
            if recognition is not None:
                with self._lock:
                    self._pending.append(recognition)

    def _recognition_from(self, detection: Mapping[str, object]) -> dict[str, object] | None:
        enriched = self._enrich(detection)
        shelf_id = _optional_text(enriched.get("shelf_id"))
        item_id = _optional_text(enriched.get("item_id"))
        if shelf_id is None and item_id is None:
            return None
        return {
            "shelf_id": shelf_id,
            "item_id": item_id,
            "tag_id": _optional_text(enriched.get("tag_id")),
        }


def start_background_runtime(
    store: InspectionStore,
    warehouse_map: WarehouseMap,
    shelf_manifest: ShelfManifest,
    config: RobotRuntimeConfig | None = None,
) -> RobotRuntime:
    runtime = RobotRuntime(store, warehouse_map, shelf_manifest, config=config)
    runtime.start()
    return runtime


def flatten_route(route: Iterable[RouteStep]) -> list[Cell]:
    waypoints: list[Cell] = []
    for step in route:
        for cell in _cells_from_step(step):
            if not waypoints or waypoints[-1] != cell:
                waypoints.append(cell)
    return waypoints


def heading_for_delta(current: Cell, target: Cell, fallback: str = "E") -> str:
    dx = target[0] - current[0]
    dy = target[1] - current[1]
    if dx > 0:
        return "E"
    if dx < 0:
        return "W"
    # Grid coordinates use screen-style rows: larger y means farther south.
    if dy > 0:
        return "S"
    if dy < 0:
        return "N"
    return fallback


def _turn_succeeded(result: Mapping[str, object] | None) -> bool:
    return result is None or bool(result.get("ok", True))


def _event_priority(event: Mapping[str, object]) -> int:
    try:
        return int(event.get("priority", 0))
    except (TypeError, ValueError):
        return 0


def _heading_after_right_turn(heading: str) -> str:
    normalized = _normalize_heading(heading, "E")
    return HEADINGS[(HEADINGS.index(normalized) + 1) % len(HEADINGS)]


def _normalize_heading(value: str, fallback: str) -> str:
    normalized = value.strip().upper()
    return normalized if normalized in HEADINGS else fallback


def _format_tape_state(tape_state: tuple[int, int, int, int] | None) -> str:
    if tape_state is None:
        return "无读数"
    return "".join(str(value) for value in tape_state)


def _json_tape_state(tape_state: tuple[int, int, int, int] | None) -> list[int] | None:
    return list(tape_state) if tape_state is not None else None


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


_DIGIT_SPOKEN = {
    "0": "零",
    "1": "一",
    "2": "二",
    "3": "三",
    "4": "四",
    "5": "五",
    "6": "六",
    "7": "七",
    "8": "八",
    "9": "九",
}


def _spoken_shelf_id(shelf_id: str) -> str:
    text = shelf_id.strip()
    if len(text) >= 2 and text[0].isalpha() and text[1:].isdigit():
        number = "".join(_DIGIT_SPOKEN.get(char, char) for char in text[1:])
        return f"{text[0].upper()}区{number}号货架"
    return text or "当前货架"


def _cells_from_step(step: RouteStep) -> list[Cell]:
    return [(int(cell[0]), int(cell[1])) for cell in step["path"]]


def _running_speed(value: int | None) -> int:
    if value is None:
        return DEFAULT_MIN_RUNNING_SPEED
    parsed = int(value)
    if parsed <= 0:
        return 0
    return max(DEFAULT_MIN_RUNNING_SPEED, min(parsed, 100))


def _clamp_config_speeds(config: RobotRuntimeConfig) -> None:
    for name in (
        "patrol_speed",
        "turn_speed",
        "avoidance_speed",
        "object_slow_speed",
        "heading_hold_correction_speed",
        "boundary_retreat_speed",
        "cruise_speed",
        "line_follow_speed",
        "line_follow_turn_speed",
    ):
        setattr(config, name, _running_speed(getattr(config, name)))


def load_calibration_into_config(config: RobotRuntimeConfig, root: Path) -> None:
    path = root / "config" / "calibration.json"
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("straight_speed") is not None:
                config.patrol_speed = int(data["straight_speed"])
            if data.get("patrol_step_seconds") is not None:
                config.step_seconds = float(data["patrol_step_seconds"])
            if data.get("patrol_settle_seconds") is not None:
                config.patrol_settle_seconds = float(data["patrol_settle_seconds"])
            if data.get("action_settle_seconds") is not None:
                config.action_settle_seconds = float(data["action_settle_seconds"])
            if data.get("turn_speed") is not None:
                config.turn_speed = int(data["turn_speed"])
            if data.get("turn_cw90_seconds") is not None:
                config.turn_90_seconds = float(data["turn_cw90_seconds"])
            if data.get("line_follow_speed") is not None:
                config.line_follow_speed = int(data["line_follow_speed"])
            if data.get("line_follow_step_seconds") is not None:
                config.line_follow_step_seconds = float(data["line_follow_step_seconds"])
            _clamp_config_speeds(config)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
