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

from .audio import start_audio_cue
from .config import ShelfManifest, WarehouseMap
from .core.planner import PlanningError, RouteStep, plan_patrol_route
from .core.events import EventRecord
from .core.store import InspectionStore
from .robot import alarm, gimbal, motion, mpu6050, oled_display, sensors
from .robot.line_following import decide_line_follow_motion
from .robot.sensors import RobotHardwareError
from .vision import tag_detector
from .vision.tag_detector import VisionDependencyError


Cell = tuple[int, int]
DetectionProvider = Callable[..., Iterator[Mapping[str, object]]]
HEADINGS = ["N", "E", "S", "W"]
DEFAULT_BOUNDARY_ACTION_PATTERN = "turn_follow,turn_patrol,bypass,turn_follow,turn_patrol"
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


def _env_float(default: float, *names: str) -> float:
    return float(_env_value(names, str(default)))


def _env_text(default: str, *names: str) -> str:
    return _env_value(names, default)


@dataclass(slots=True)
class RobotRuntimeConfig:
    blocked_distance_mm: int = field(default_factory=lambda: _env_int(160, "BLOCKED_DISTANCE_MM"))
    clear_distance_mm: int = field(default_factory=lambda: _env_int(240, "CLEAR_DISTANCE_MM"))
    blocked_samples: int = field(default_factory=lambda: _env_int(2, "BLOCKED_SAMPLES"))
    patrol_speed: int = field(default_factory=lambda: _env_int(30, "ROBOT_PATROL_SPEED", "ROBOT_SLOW_SPEED"))
    step_seconds: float = field(default_factory=lambda: _env_float(0.25, "ROBOT_PATROL_STEP_SECONDS", "ROBOT_STEP_SECONDS"))
    poll_seconds: float = field(default_factory=lambda: _env_float(0.12, "ROBOT_POLL_SECONDS"))
    scan_enabled: bool = field(default_factory=lambda: _env_bool("ROBOT_SCAN_ENABLED", False))
    scan_timeout_seconds: float = 4.0
    scan_max_detections: int = 6
    scan_interval_seconds: float = field(default_factory=lambda: _env_float(0.8, "SCAN_INTERVAL_SECONDS"))
    turn_90_seconds: float = field(default_factory=lambda: _env_float(0.85, "ROBOT_TURN_90_SECONDS"))
    turn_speed: int = field(default_factory=lambda: _env_int(22, "ROBOT_TURN_SPEED"))
    action_settle_seconds: float = field(default_factory=lambda: _env_float(0.45, "ROBOT_ACTION_SETTLE_SECONDS"))
    obstacle_wait_seconds: float = field(default_factory=lambda: _env_float(6.0, "OBSTACLE_WAIT_SECONDS"))
    avoidance_speed: int = field(default_factory=lambda: _env_int(20, "AVOIDANCE_SPEED"))
    avoidance_body_seconds: float = field(default_factory=lambda: _env_float(0.50, "AVOIDANCE_BODY_SECONDS"))
    avoidance_side_clearance_bodies: float = field(default_factory=lambda: _env_float(1.0, "AVOIDANCE_SIDE_CLEARANCE_BODIES"))
    avoidance_parallel_bodies: float = field(default_factory=lambda: _env_float(2.0, "AVOIDANCE_PARALLEL_BODIES"))
    avoidance_return_bodies: float = field(default_factory=lambda: _env_float(1.0, "AVOIDANCE_RETURN_BODIES"))
    avoidance_turn_direction: str = field(default_factory=lambda: _env_text("right", "AVOIDANCE_TURN_DIRECTION"))
    boundary_min_black_sensors: int = field(default_factory=lambda: _env_int(4, "BOUNDARY_MIN_BLACK_SENSORS"))
    boundary_confirm_samples: int = field(default_factory=lambda: _env_int(1, "BOUNDARY_CONFIRM_SAMPLES"))
    boundary_confirm_gap_seconds: float = field(default_factory=lambda: _env_float(0.03, "BOUNDARY_CONFIRM_GAP_SECONDS"))
    boundary_cooldown_seconds: float = field(default_factory=lambda: _env_float(0.10, "BOUNDARY_COOLDOWN_SECONDS"))
    line_follow_enabled: bool = field(default_factory=lambda: _env_bool("LINE_FOLLOW_ENABLED", False))
    line_follow_speed: int = field(default_factory=lambda: _env_int(30, "LINE_FOLLOW_SPEED", "ROBOT_PATROL_SPEED", "ROBOT_SLOW_SPEED"))
    line_follow_step_seconds: float = field(default_factory=lambda: _env_float(0.14, "LINE_FOLLOW_STEP_SECONDS", "ROBOT_STEP_SECONDS"))
    line_follow_turn_speed: int = field(
        default_factory=lambda: _env_int(
            30,
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
    boundary_action_pattern: str = field(default_factory=lambda: _env_text(DEFAULT_BOUNDARY_ACTION_PATTERN, "BOUNDARY_ACTION_PATTERN"))
    turns_per_cycle: int = 2
    skip_scan_cycles: int = 1
    camera_device: int = 0
    patrol_order: tuple[str, ...] = field(default_factory=lambda: ("A1", "A2", "A3", "A4", "B4", "B3", "B2", "B1"))
    cycle_max_missed_shelves: int = 1
    video_width: int = 640
    video_height: int = 360
    video_fps: int = 8


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
        display_adapter: Any = oled_display,
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
        self.display = display_adapter
        self.detection_provider = detection_provider
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._blocked_count = 0
        self._obstacle_active = False
        self._last_boundary_turn = 0.0
        self._boundary_action_index = 0
        self._line_follow_active = False
        self._line_lost_ticks = 0
        self._last_line_correction: str | None = None
        self._motion_step_index = 0
        self._last_motion_sensor_at = 0.0
        self._observed_shelf_sequence: list[str] = []

    def start(self, shelf_order: Iterable[str] | None = None) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_continuous_patrol_safely, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        try:
            self.motion.stop()
        except RobotHardwareError:
            pass
        self.store.stop()
        self.store.record_motion_debug("runtime_stopped", "收到停止命令，电机已停止。", status="STOPPED")

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

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
        self.store.record_run_mode("robot", True)
        self.store.start()
        self.store.record_cycle(1, self._skip_shortage_for_cycle(1))
        self.store.record_motion_debug(
            "runtime_started",
            "运动调试巡逻启动：短步前进、列端转向、寻线过渡、禁区绕行；当前暂不做货架识别。",
            evidence={
                "patrol_speed": self.config.patrol_speed,
                "step_seconds": self.config.step_seconds,
                "boundary_min_black_sensors": self.config.boundary_min_black_sensors,
                "boundary_confirm_samples": self.config.boundary_confirm_samples,
                "scan_enabled": self.config.scan_enabled,
            },
        )
        self._boundary_action_index = 0
        self._line_follow_active = False
        self._line_lost_ticks = 0
        self._last_line_correction = None
        self._motion_step_index = 0
        self._show_normal()
        self._initialize_gimbal()
        self.refresh_motion_sensor(force=True)

        iterations = 0
        last_scan_at = time.monotonic()

        while not self._stop_event.is_set():
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

            self.refresh_motion_sensor()
            self._drive_patrol_step(tape_state)
            self._show_line_follow() if self._line_follow_active else self._show_normal()
            iterations += 1
            current_cycle = self._current_cycle()
            self.store.record_cycle(current_cycle, self._skip_shortage_for_cycle(current_cycle))

            now = time.monotonic()
            if self.config.scan_enabled and now - last_scan_at >= self.config.scan_interval_seconds:
                self._scan_visible_shelf()
                last_scan_at = now

        self.motion.stop()
        self.store.stop()
        self.store.record_motion_debug("runtime_stopped", "运动调试巡逻已停止。", status="STOPPED")

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
        if not self._candidate_boundary(tape_state):
            return "none"
        now = time.monotonic()
        if now - self._last_boundary_turn < self.config.boundary_cooldown_seconds:
            return "none"
        self.motion.stop()
        self.store.record_motion_debug(
            "boundary_candidate",
            f"检测到黑胶带候选，先停车确认：tape={_format_tape_state(tape_state)}，阈值={self.config.boundary_min_black_sensors} 路黑。",
            status="TURNING_AT_BOUNDARY",
            evidence={
                "tape_state": _json_tape_state(tape_state),
                "black_count": sensors.black_tape_count(tape_state),
                "min_black": self.config.boundary_min_black_sensors,
                "confirm_samples": self.config.boundary_confirm_samples,
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
        self._last_boundary_turn = now
        action = self._next_boundary_action()
        if action == "bypass":
            return self._handle_forbidden_bypass(confirmed_state)
        return self._handle_planned_boundary_turn(confirmed_state, action)

    def _handle_planned_boundary_turn(self, tape_state: tuple[int, int, int, int], action: str) -> str:
        action_number = self._boundary_action_index + 1
        zone_id = f"black-tape-route-{action_number}"
        self.store.record_motion_debug(
            "boundary_action",
            self._boundary_action_message(action),
            status="TURNING_AT_BOUNDARY",
            evidence={
                "action_index": action_number,
                "action": action,
                "tape_state": _json_tape_state(tape_state),
                "phase_before": self._patrol_phase_label(),
            },
        )
        self.alarm.show_warning()
        self.store.record_boundary(tape_state, True, action)
        self.store.record_forbidden_zone(zone_id, True)
        if not _turn_succeeded(self._turn_90("right")):
            return "none"
        self.store.record_boundary_turn("clockwise", 90)
        self._line_follow_active = action == "turn_follow" and self.config.line_follow_enabled
        self._line_lost_ticks = 0
        self._last_line_correction = None
        self.store.record_forbidden_zone(zone_id, False)
        self._advance_boundary_action()
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

    def _handle_forbidden_bypass(self, tape_state: tuple[int, int, int, int]) -> str:
        action_number = self._boundary_action_index + 1
        zone_id = f"black-tape-bypass-{action_number}"
        self._line_follow_active = False
        self.alarm.show_obstacle_wait()
        self.store.record_motion_debug(
            "forbidden_bypass_start",
            self._boundary_action_message("bypass"),
            status="FORBIDDEN_ZONE_WAIT",
            evidence={
                "action_index": action_number,
                "tape_state": _json_tape_state(tape_state),
                "phase_before": self._patrol_phase_label(),
            },
        )
        self.store.record_boundary(tape_state, True, "route_forbidden_bypass")
        self.store.record_forbidden_zone(zone_id, True)
        self._play_cue("obstacle", "检测到非寻线禁区，小车按障碍绕行。")
        if self._avoid_to_safe_side(None):
            self.store.record_forbidden_zone(zone_id, False)
            self._show_normal()
        self._advance_boundary_action()
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

    def _confirm_boundary_state(
        self,
        first_state: tuple[int, int, int, int] | None,
    ) -> tuple[int, int, int, int] | None:
        state = first_state
        samples = max(1, int(self.config.boundary_confirm_samples))
        for index in range(samples):
            if index > 0:
                time.sleep(max(0.0, self.config.boundary_confirm_gap_seconds))
                state = self.sensors.read_tape_boundary()
            if not self._candidate_boundary(state):
                return None
        return state

    def _drive_patrol_step(self, tape_state: tuple[int, int, int, int] | None) -> None:
        if self._line_follow_active and self.config.line_follow_enabled:
            self._drive_line_follow_step(tape_state)
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
                "tape_state": _json_tape_state(tape_state),
            },
        )
        self._forward_step(
            speed=self.config.patrol_speed,
            duration_seconds=self.config.step_seconds,
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
                time.sleep(max(0.0, self.config.line_follow_poll_seconds))
                return
            if tape_state is None:
                self.motion.stop()
                self.store.record_motion_debug(
                    "line_follow_wait",
                    f"{phase}：{decision.description}，传感器读数无效，先停车等下一次读数。",
                    evidence=evidence,
                )
                time.sleep(max(0.0, self.config.line_follow_poll_seconds))
                return
            if self._line_lost_ticks == 1:
                self.store.record_motion_debug(
                    "line_follow_search",
                    f"{phase}：{decision.description}，按最近纠偏方向 {recovery_command} 短步找线。",
                    evidence=evidence,
                )
            self._run_line_follow_command(recovery_command, search=True)
            time.sleep(max(0.0, self.config.line_follow_poll_seconds))
            return

        if decision.command in {"wait", "stop"}:
            self.motion.stop()
            time.sleep(max(0.0, self.config.line_follow_poll_seconds))
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
        time.sleep(max(0.0, self.config.line_follow_poll_seconds))

    def _run_line_follow_command(self, command: str, *, search: bool = False) -> None:
        step_seconds = self.config.line_follow_search_seconds if search else self.config.line_follow_step_seconds
        turn_seconds = self.config.line_follow_search_seconds if search else self.config.line_follow_turn_seconds
        if command == "forward":
            self._run_timed_motion(
                self.motion.move_forward_slow,
                speed=self.config.line_follow_speed,
                duration_seconds=step_seconds,
            )
        elif command == "strafe_left":
            self._run_timed_motion(
                self.motion.strafe_left_slow,
                speed=self.config.line_follow_speed,
                duration_seconds=step_seconds,
            )
        elif command == "strafe_right":
            self._run_timed_motion(
                self.motion.strafe_right_slow,
                speed=self.config.line_follow_speed,
                duration_seconds=step_seconds,
            )
        elif command == "turn_left":
            self._run_timed_motion(
                self.motion.rotate_left_slow,
                speed=self.config.line_follow_turn_speed,
                duration_seconds=turn_seconds,
            )
        elif command == "turn_right":
            self._run_timed_motion(
                self.motion.rotate_right_slow,
                speed=self.config.line_follow_turn_speed,
                duration_seconds=turn_seconds,
            )
        else:
            self.motion.stop()

    def _run_timed_motion(self, mover: Callable[..., None], *, speed: int, duration_seconds: float) -> None:
        mover(speed=speed, duration_seconds=duration_seconds)
        self.motion.stop()

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
        updater = getattr(self.display, "update_motion_sensor", None)
        if callable(updater):
            updater(sample)

    def turn_90_closed_loop(self, direction: str, *, speed: int | None = None, duration_seconds: float | None = None) -> dict[str, object] | None:
        return self._turn_90(direction, speed=speed, duration_seconds=duration_seconds)

    def _scan_visible_shelf(self) -> None:
        detections = self._collect_detections()
        shelf_id = self._shelf_id_from_detections(detections)
        if shelf_id is None:
            return
        self._perform_scan(shelf_id, f"{shelf_id}_SCAN", detections)

    def _shelf_id_from_detections(self, detections: list[dict[str, object]]) -> str | None:
        for detection in detections:
            shelf_id = detection.get("shelf_id")
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
            return enriched
        kind = str(info.get("kind", "item"))
        enriched.setdefault("kind", kind)
        enriched.setdefault("name", info.get("name"))
        if kind == "shelf" and info.get("shelf_id") is not None:
            enriched.setdefault("shelf_id", str(info["shelf_id"]).strip().upper())
        if kind == "item" and info.get("item_id") is not None:
            enriched.setdefault("item_id", str(info["item_id"]))
        return enriched

    def _record_observed_shelf(self, shelf_id: str) -> None:
        order = [str(item).strip().upper() for item in self.config.patrol_order if str(item).strip()]
        normalized = shelf_id.strip().upper()
        if normalized not in order:
            return
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
        if not waiting:
            return
        high_priority = any(event.get("type") == "missing_item" or _event_priority(event) >= 3 for event in waiting)
        try:
            if high_priority:
                notifier = getattr(self.alarm, "show_high_priority_alarm", None)
                if callable(notifier):
                    notifier()
                else:
                    self.alarm.show_warning()
            else:
                self.alarm.show_warning()
        except RobotHardwareError:
            pass

    def _wait_for_obstacle_clear(self, shelf_id: str | None) -> bool:
        started_at = time.monotonic()
        deadline = started_at + self.config.obstacle_wait_seconds
        while not self._stop_event.is_set() and time.monotonic() < deadline:
            time.sleep(self.config.poll_seconds)
            distance_mm = self.sensors.read_distance_mm()
            if distance_mm is not None and distance_mm >= self.config.clear_distance_mm:
                self._blocked_count = 0
                self._obstacle_active = False
                self.alarm.clear_alarm()
                self.store.record_obstacle(distance_mm, False)
                return True
        return self._avoid_to_safe_side(shelf_id)

    def _avoid_to_safe_side(self, shelf_id: str | None) -> bool:
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

        if not self._avoidance_forward("side_clearance_forward", self.config.avoidance_side_clearance_bodies):
            return False

        self.store.record_avoidance_step(f"turn_{back_label}_to_original_heading", nested_level=0)
        if not _turn_succeeded(self._turn_90(back_label)):
            return False
        if not self._avoidance_path_clear("after_restore_heading"):
            return False

        if not self._avoidance_forward("forward_past_obstacle", self.config.avoidance_parallel_bodies):
            return False

        self.store.record_avoidance_step(f"turn_{back_label}_return_to_line", nested_level=0)
        if not _turn_succeeded(self._turn_90(back_label)):
            return False
        if not self._avoidance_path_clear("after_return_turn"):
            return False

        if not self._avoidance_forward("return_to_patrol_line", self.config.avoidance_return_bodies):
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
        self._forward_step(speed=self.config.avoidance_speed, duration_seconds=duration_seconds)
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
        pattern = _boundary_action_pattern(self.config.boundary_action_pattern)
        return pattern[self._boundary_action_index % len(pattern)]

    def _advance_boundary_action(self) -> None:
        self._boundary_action_index += 1

    def _boundary_action_message(self, action: str) -> str:
        pattern_index = self._boundary_action_index % len(_boundary_action_pattern(self.config.boundary_action_pattern))
        if action == "bypass":
            return "B列中段禁区触发：停车后按右侧绕行流程绕过，再继续B列短步巡逻。"
        messages = {
            0: "A列末端禁区/列端触发：顺时针转 90 度，随后进入寻线到B端。",
            1: "B端入口禁区/列端触发：顺时针转 90 度，退出寻线，进入B列短步巡逻。",
            2: "中段禁区触发：停车后按绕行流程越过禁区。",
            3: "B列末端禁区/列端触发：顺时针转 90 度，随后寻线回A端。",
            4: "A端入口禁区/列端触发：顺时针转 90 度，回到A列起点，进入下一轮。",
        }
        return messages.get(pattern_index, f"列端触发：执行 {action} 动作。")

    def _patrol_phase_label(self) -> str:
        pattern_index = self._boundary_action_index % len(_boundary_action_pattern(self.config.boundary_action_pattern))
        if self._line_follow_active:
            if pattern_index == 1:
                return "A端到B端寻线"
            if pattern_index == 4:
                return "B端到A端寻线"
            return "寻线过渡"
        if pattern_index in {2, 3}:
            return "B列短步巡逻"
        return "A列短步巡逻"

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
            self._forward_step(speed=self.config.patrol_speed, duration_seconds=self.config.step_seconds)
            return target_heading
        delta = (HEADINGS.index(target_heading) - HEADINGS.index(heading)) % len(HEADINGS)
        if delta == 0:
            self._forward_step(speed=self.config.patrol_speed, duration_seconds=self.config.step_seconds)
        elif delta == 1:
            self.motion.strafe_right_slow(speed=self.config.patrol_speed, duration_seconds=self.config.step_seconds)
            self.motion.stop()
            self._settle()
        elif delta == 2:
            if not self._turn_to_heading(heading, target_heading):
                return heading
            self._forward_step(speed=self.config.patrol_speed, duration_seconds=self.config.step_seconds)
            return target_heading
        else:
            self.motion.strafe_left_slow(speed=self.config.patrol_speed, duration_seconds=self.config.step_seconds)
            self.motion.stop()
            self._settle()
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

    def _forward_step(self, *, speed: int, duration_seconds: float) -> None:
        self.motion.move_forward_slow(speed=speed, duration_seconds=duration_seconds)
        self.motion.stop()
        self._settle()

    def _turn_90(self, direction: str, *, speed: int | None = None, duration_seconds: float | None = None) -> dict[str, object] | None:
        normalized = direction.strip().lower()
        turn_speed = self.config.turn_speed if speed is None else speed
        turn_seconds = self.config.turn_90_seconds if duration_seconds is None else duration_seconds
        imu_turn = getattr(self.imu, "turn_90_with_result", None)
        if callable(imu_turn):
            try:
                imu_result = imu_turn(normalized, self.motion, turn_speed, turn_seconds)
            except RobotHardwareError as exc:
                self.store.record_robot_status("TURNING_AT_BOUNDARY", f"MPU6050 turn skipped: {exc}")
            else:
                if imu_result is not None:
                    self.motion.stop()
                    self._settle()
                    result = dict(imu_result) if isinstance(imu_result, dict) else {"ok": bool(imu_result)}
                    self._record_gyro_turn_result(result)
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
                        return {"ok": bool(legacy_result), "source": "mpu6050_legacy", "direction": normalized}
                except RobotHardwareError as exc:
                    self.store.record_robot_status("TURNING_AT_BOUNDARY", f"MPU6050 turn skipped: {exc}")
        if normalized == "left":
            self.motion.rotate_left_slow(
                speed=turn_speed,
                duration_seconds=turn_seconds,
            )
        elif normalized == "right":
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

    def _settle(self) -> None:
        delay = max(0.0, float(self.config.action_settle_seconds))
        if delay > 0:
            time.sleep(delay)

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
        try:
            iterator = self.detection_provider(
                device=self.config.camera_device,
                cooldown_seconds=0.5,
                idle_timeout_seconds=self.config.scan_timeout_seconds,
            )
        except TypeError:
            iterator = self.detection_provider(device=self.config.camera_device, cooldown_seconds=0.5)
        detections: list[dict[str, object]] = []
        try:
            for detection in itertools.islice(iterator, self.config.scan_max_detections):
                detections.append(self._enrich_detection(detection))
        except (RobotHardwareError, VisionDependencyError) as exc:
            self.store.record_robot_status("SCANNING_SHELF", f"side camera scan skipped: {exc}")
        return detections

    def _play_cue(self, cue: str, message: str) -> None:
        payload, status = start_audio_cue(self.store.root, cue)
        error = None if status == 200 else str(payload.get("error", cue))
        display_message = message if error is None else f"音频播放失败: {error}"
        self.store.record_audio_cue(cue, display_message, error)


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


def _cycle_from_turn_count(turn_count: int, turns_per_cycle: int) -> int:
    if turn_count <= 0:
        return 1
    return ((turn_count - 1) // max(1, int(turns_per_cycle))) + 1


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


def _boundary_action_pattern(raw_pattern: str) -> list[str]:
    actions: list[str] = []
    for item in raw_pattern.split(","):
        normalized = item.strip().lower().replace("-", "_")
        if normalized in {"turn_follow", "follow", "line", "line_follow"}:
            actions.append("turn_follow")
        elif normalized in {"turn_patrol", "patrol", "exit_line", "line_exit"}:
            actions.append("turn_patrol")
        elif normalized in {"bypass", "avoid", "obstacle", "forbidden"}:
            actions.append("bypass")
    return actions or ["turn_follow", "turn_patrol", "bypass", "turn_follow", "turn_patrol"]


def _format_tape_state(tape_state: tuple[int, int, int, int] | None) -> str:
    if tape_state is None:
        return "无读数"
    return "".join(str(value) for value in tape_state)


def _json_tape_state(tape_state: tuple[int, int, int, int] | None) -> list[int] | None:
    return list(tape_state) if tape_state is not None else None


def _cells_from_step(step: RouteStep) -> list[Cell]:
    return [(int(cell[0]), int(cell[1])) for cell in step["path"]]


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
            if data.get("turn_speed") is not None:
                config.turn_speed = int(data["turn_speed"])
            if data.get("turn_cw90_seconds") is not None:
                config.turn_90_seconds = float(data["turn_cw90_seconds"])
            if data.get("line_follow_speed") is not None:
                config.line_follow_speed = int(data["line_follow_speed"])
            if data.get("line_follow_step_seconds") is not None:
                config.line_follow_step_seconds = float(data["line_follow_step_seconds"])
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
