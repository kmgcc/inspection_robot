from __future__ import annotations

import itertools
import os
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from .audio import start_audio_cue
from .config import ShelfManifest, WarehouseMap
from .core.planner import PlanningError, RouteStep, plan_patrol_route
from .core.store import InspectionStore
from .robot import alarm, gimbal, motion, sensors
from .robot.sensors import RobotHardwareError
from .vision import tag_detector
from .vision.tag_detector import VisionDependencyError


Cell = tuple[int, int]
DetectionProvider = Callable[..., Iterator[Mapping[str, object]]]


@dataclass(slots=True)
class RobotRuntimeConfig:
    blocked_distance_mm: int = int(os.environ.get("BLOCKED_DISTANCE_MM", "160"))
    clear_distance_mm: int = int(os.environ.get("CLEAR_DISTANCE_MM", "240"))
    blocked_samples: int = int(os.environ.get("BLOCKED_SAMPLES", "2"))
    patrol_speed: int = int(os.environ.get("ROBOT_PATROL_SPEED", os.environ.get("ROBOT_SLOW_SPEED", "22")))
    step_seconds: float = float(os.environ.get("ROBOT_STEP_SECONDS", "0.14"))
    poll_seconds: float = float(os.environ.get("ROBOT_POLL_SECONDS", "0.12"))
    scan_timeout_seconds: float = 4.0
    scan_max_detections: int = 6
    scan_interval_seconds: float = float(os.environ.get("SCAN_INTERVAL_SECONDS", "0.8"))
    turn_90_seconds: float = float(os.environ.get("ROBOT_TURN_90_SECONDS", "0.75"))
    turn_speed: int = int(os.environ.get("ROBOT_TURN_SPEED", "18"))
    action_settle_seconds: float = float(os.environ.get("ROBOT_ACTION_SETTLE_SECONDS", "0.35"))
    obstacle_wait_seconds: float = float(os.environ.get("OBSTACLE_WAIT_SECONDS", "6.0"))
    avoidance_speed: int = int(os.environ.get("AVOIDANCE_SPEED", "14"))
    avoidance_body_seconds: float = float(os.environ.get("AVOIDANCE_BODY_SECONDS", "1.00"))
    avoidance_turn_direction: str = os.environ.get("AVOIDANCE_TURN_DIRECTION", "right")
    boundary_min_black_sensors: int = int(os.environ.get("BOUNDARY_MIN_BLACK_SENSORS", "4"))
    boundary_confirm_samples: int = int(os.environ.get("BOUNDARY_CONFIRM_SAMPLES", "2"))
    boundary_confirm_gap_seconds: float = float(os.environ.get("BOUNDARY_CONFIRM_GAP_SECONDS", "0.08"))
    boundary_cooldown_seconds: float = 1.2
    line_follow_enabled: bool = os.environ.get("LINE_FOLLOW_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
    line_follow_speed: int = int(os.environ.get("LINE_FOLLOW_SPEED", os.environ.get("ROBOT_PATROL_SPEED", os.environ.get("ROBOT_SLOW_SPEED", "22"))))
    line_follow_step_seconds: float = float(os.environ.get("LINE_FOLLOW_STEP_SECONDS", os.environ.get("ROBOT_STEP_SECONDS", "0.14")))
    turns_per_cycle: int = 2
    skip_scan_cycles: int = 1
    camera_device: int = 0


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
        detection_provider: DetectionProvider = tag_detector.iter_detections,
    ) -> None:
        self.store = store
        self.warehouse_map = warehouse_map
        self.shelf_manifest = shelf_manifest
        self.config = config or RobotRuntimeConfig()
        self.motion = motion_adapter
        self.sensors = sensor_adapter
        self.alarm = alarm_adapter
        self.gimbal = gimbal_adapter
        self.detection_provider = detection_provider
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._blocked_count = 0
        self._obstacle_active = False
        self._last_boundary_turn = 0.0

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
            self.store.record_robot_status("ERROR", f"runtime hardware error: {exc}")

    def run_continuous_patrol(self, max_iterations: int | None = None) -> None:
        self.store.record_run_mode("robot", True)
        self.store.start()
        self.store.record_cycle(1, True)
        self.store.record_robot_status("PATROLLING", "自主巡检循环已启动，小车慢速前进。")
        self._initialize_gimbal()

        iterations = 0
        turn_count = 0
        cycle_count = 0
        last_scan_at = time.monotonic()

        while not self._stop_event.is_set():
            if max_iterations is not None and iterations >= max_iterations:
                self.motion.stop()
                self.store.record_robot_status("STOPPED", f"continuous patrol stopped after {iterations} iterations")
                return

            tape_state = self.sensors.read_tape_boundary()
            if self._handle_tape_boundary_turn(tape_state):
                turn_count += 1
                cycle_count = turn_count // max(1, self.config.turns_per_cycle)
                current_cycle = cycle_count + 1
                self.store.record_cycle(current_cycle, current_cycle <= self.config.skip_scan_cycles)
                self.store.record_robot_status(
                    "TURNING_AT_BOUNDARY",
                    f"检测到货架尽头黑胶带，已顺时针转向；当前第 {current_cycle} 轮。",
                )
                continue

            if not self._guard_obstacle(None):
                continue

            self._drive_patrol_step(tape_state)
            iterations += 1
            current_cycle = cycle_count + 1
            self.store.record_cycle(current_cycle, current_cycle <= self.config.skip_scan_cycles)
            self.store.record_robot_status("PATROLLING", f"自主巡检中，第 {current_cycle} 轮。")

            now = time.monotonic()
            if now - last_scan_at >= self.config.scan_interval_seconds:
                self._scan_visible_shelf()
                last_scan_at = now

        self.motion.stop()
        self.store.stop()

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
        heading = "E"
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
                if not self._guard_before_move(step.get("shelf_id")):
                    return
                next_heading = heading_for_delta(current, cell, heading)
                self._move_between(current, cell, heading, next_heading)
                heading = next_heading
                current = cell
                executed += 1
                self.store.record_pose(current[0], current[1], heading, source="runtime")
                if max_steps is not None and executed >= max_steps:
                    self.store.record_robot_status("STOPPED", f"runtime stopped after {executed} waypoint steps")
                    return
            if step["action"] == "scan" and step["shelf_id"] is not None:
                self._scan_shelf(step)

        self.motion.stop()
        self.alarm.show_normal()
        self.store.finish_run()

    def _guard_before_move(self, shelf_id: str | None) -> bool:
        if self._handle_tape_boundary_turn():
            return True
        return self._guard_obstacle(shelf_id)

    def _handle_tape_boundary_turn(self, tape_state: tuple[int, int, int, int] | None = None) -> bool:
        if tape_state is None:
            tape_state = self.sensors.read_tape_boundary()
        if not self._candidate_boundary(tape_state):
            return False
        now = time.monotonic()
        if now - self._last_boundary_turn < self.config.boundary_cooldown_seconds:
            return False
        self.motion.stop()
        confirmed_state = self._confirm_boundary_state(tape_state)
        if confirmed_state is None:
            return False
        self._last_boundary_turn = now
        self.alarm.show_warning()
        self.store.record_boundary(confirmed_state, True, "column_end")
        self.store.record_forbidden_zone("black-tape-end", True)
        self._turn_90("right")
        self.store.record_boundary_turn("clockwise", 90)
        self.store.record_forbidden_zone("black-tape-end", False)
        self.alarm.show_normal()
        return True

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
        if self.config.line_follow_enabled and not self._line_is_centered(tape_state):
            self.motion.stop()
            self._settle()
            tape_state = self.sensors.read_tape_boundary()
            if not self._line_is_centered(tape_state):
                self.store.record_boundary(tape_state, False, "line_not_centered")
                return
        self._forward_step(
            speed=self.config.line_follow_speed if self.config.line_follow_enabled else self.config.patrol_speed,
            duration_seconds=self.config.line_follow_step_seconds if self.config.line_follow_enabled else self.config.step_seconds,
        )

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

    def _scan_visible_shelf(self) -> None:
        detections = self._collect_detections()
        shelf_id = self._shelf_id_from_detections(detections)
        if shelf_id is None:
            return
        self.store.record_shelf_arrival(shelf_id)
        self._play_cue("first", f"扫描到 {shelf_id} 货架。")
        frame_id = f"runtime-{shelf_id.lower()}-{int(time.time())}"
        if detections:
            self.store.record_detection_evidence(shelf_id, detections, frame_id=frame_id)
            if any(str(detection.get("kind", "item")) == "item" or detection.get("item_id") for detection in detections):
                self._play_cue("following", f"识别到 {shelf_id} 货架上的物品。")
        else:
            self.store.record_scan_result(shelf_id, [], frame_id=frame_id)

    def _shelf_id_from_detections(self, detections: list[dict[str, object]]) -> str | None:
        for detection in detections:
            shelf_id = detection.get("shelf_id")
            if shelf_id is not None:
                normalized = str(shelf_id).strip().upper()
                if normalized:
                    return normalized
        return None

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
        turn_left = direction != "right"
        away_label = "left" if turn_left else "right"
        back_label = "right" if turn_left else "left"
        self.motion.stop()
        self.store.record_avoidance_step(f"turn_{away_label}_to_safe_side", nested_level=0)
        self._turn_90(away_label)
        if not self._avoidance_path_clear("after_first_turn"):
            return False

        if not self._avoidance_forward("side_clearance_forward"):
            return False

        self.store.record_avoidance_step(f"turn_{back_label}_to_original_heading", nested_level=0)
        self._turn_90(back_label)
        if not self._avoidance_path_clear("after_restore_heading"):
            return False

        if not self._avoidance_forward("forward_past_obstacle"):
            return False

        self.store.record_avoidance_step(f"turn_{back_label}_return_to_line", nested_level=0)
        self._turn_90(back_label)
        if not self._avoidance_path_clear("after_return_turn"):
            return False

        if not self._avoidance_forward("return_to_patrol_line"):
            return False

        self.store.record_avoidance_step(f"turn_{away_label}_restore_heading", nested_level=0)
        self._turn_90(away_label)
        distance_mm = self.sensors.read_distance_mm()
        if distance_mm is None or self._distance_clear(distance_mm):
            self._blocked_count = 0
            self._obstacle_active = False
            self.alarm.clear_alarm()
            self.store.record_obstacle(distance_mm, False)
            return True
        self.store.record_obstacle(distance_mm, True, waiting_seconds=0)
        return False

    def _avoidance_forward(self, step: str) -> bool:
        self.store.record_avoidance_step(step, nested_level=0)
        self._forward_step(speed=self.config.avoidance_speed, duration_seconds=self.config.avoidance_body_seconds)
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

    def _safe_side_for_shelf(self, shelf_id: str | None) -> str | None:
        if shelf_id is None:
            return None
        point = self.warehouse_map["shelf_points"].get(shelf_id)
        if point is None:
            return None
        return str(point["safe_side"]).upper()

    def _move_between(self, current: Cell, target: Cell, heading: str, target_heading: str) -> None:
        dx = target[0] - current[0]
        dy = target[1] - current[1]
        if abs(dx) + abs(dy) != 1:
            raise ValueError(f"non-adjacent waypoint transition: {current} -> {target}")
        self._turn_to_heading(heading, target_heading)
        self._forward_step(speed=self.config.patrol_speed, duration_seconds=self.config.step_seconds)

    def _turn_to_heading(self, heading: str, target_heading: str) -> None:
        headings = ["N", "E", "S", "W"]
        if heading not in headings or target_heading not in headings:
            return
        delta = (headings.index(target_heading) - headings.index(heading)) % len(headings)
        if delta == 1:
            self._turn_90("right")
        elif delta == 2:
            self._turn_90("right")
            self._turn_90("right")
        elif delta == 3:
            self._turn_90("left")

    def _forward_step(self, *, speed: int, duration_seconds: float) -> None:
        self.motion.move_forward_slow(speed=speed, duration_seconds=duration_seconds)
        self.motion.stop()
        self._settle()

    def _turn_90(self, direction: str, *, speed: int | None = None, duration_seconds: float | None = None) -> None:
        normalized = direction.strip().lower()
        if normalized == "left":
            self.motion.rotate_left_slow(
                speed=self.config.turn_speed if speed is None else speed,
                duration_seconds=self.config.turn_90_seconds if duration_seconds is None else duration_seconds,
            )
        elif normalized == "right":
            self.motion.rotate_right_slow(
                speed=self.config.turn_speed if speed is None else speed,
                duration_seconds=self.config.turn_90_seconds if duration_seconds is None else duration_seconds,
            )
        else:
            raise ValueError(f"unsupported turn direction: {direction}")
        self.motion.stop()
        self._settle()

    def _settle(self) -> None:
        delay = max(0.0, float(self.config.action_settle_seconds))
        if delay > 0:
            time.sleep(delay)

    def _scan_shelf(self, step: RouteStep) -> None:
        shelf_id = str(step["shelf_id"])
        self.motion.stop()
        self.store.record_shelf_arrival(shelf_id, target=step["target"])
        self._play_cue("first", f"扫描到 {shelf_id} 货架。")
        detections = self._collect_detections()
        frame_id = f"runtime-{shelf_id.lower()}-{int(time.time())}"
        if detections:
            self.store.record_detection_evidence(shelf_id, detections, frame_id=frame_id)
            if any(str(detection.get("kind", "item")) == "item" or detection.get("item_id") for detection in detections):
                self._play_cue("following", f"识别到 {shelf_id} 货架上的物品。")
        else:
            self.store.record_scan_result(shelf_id, [], frame_id=frame_id)

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
                detections.append(dict(detection))
        except (RobotHardwareError, VisionDependencyError) as exc:
            self.store.record_robot_status("SCANNING_SHELF", f"side camera scan skipped: {exc}")
        return detections

    def _play_cue(self, cue: str, message: str) -> None:
        payload, status = start_audio_cue(self.store.root, cue)
        self.store.record_audio_cue(cue, message if status == 200 else message, None if status == 200 else str(payload.get("error")))


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
    if dy > 0:
        return "S"
    if dy < 0:
        return "N"
    return fallback


def _cells_from_step(step: RouteStep) -> list[Cell]:
    return [(int(cell[0]), int(cell[1])) for cell in step["path"]]
