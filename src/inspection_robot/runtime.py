from __future__ import annotations

import itertools
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
    blocked_distance_mm: int = 200
    clear_distance_mm: int = 280
    blocked_samples: int = 3
    step_seconds: float = 0.35
    poll_seconds: float = 0.2
    scan_timeout_seconds: float = 4.0
    scan_max_detections: int = 6
    scan_interval_seconds: float = 1.5
    turn_90_seconds: float = 0.55
    turn_speed: int = 30
    obstacle_wait_seconds: float = 6.0
    avoidance_body_seconds: float = 0.45
    max_avoidance_depth: int = 2
    boundary_cooldown_seconds: float = 1.2
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

            if self._handle_tape_boundary_turn():
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
                return

            self.motion.move_forward_slow(duration_seconds=self.config.step_seconds)
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
                heading = heading_for_delta(current, cell, heading)
                self._move_between(current, cell)
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

    def _handle_tape_boundary_turn(self) -> bool:
        tape_state = self.sensors.read_tape_boundary()
        detector = getattr(self.sensors, "full_tape_boundary_detected", sensors.full_tape_boundary_detected)
        if detector(tape_state):
            now = time.monotonic()
            if now - self._last_boundary_turn < self.config.boundary_cooldown_seconds:
                return False
            self._last_boundary_turn = now
            self.motion.stop()
            self.alarm.show_warning()
            self.store.record_boundary(tape_state, True, "column_end")
            self.store.record_forbidden_zone("black-tape-end", True)
            self.motion.rotate_right_slow(speed=self.config.turn_speed, duration_seconds=self.config.turn_90_seconds)
            self.store.record_boundary_turn("clockwise", 90)
            self.store.record_forbidden_zone("black-tape-end", False)
            self.alarm.show_normal()
            return True

        if tape_state is None or not sensors.tape_boundary_detected(tape_state):
            return False
        now = time.monotonic()
        if now - self._last_boundary_turn < self.config.boundary_cooldown_seconds:
            return False
        self._last_boundary_turn = now
        self.motion.stop()
        self.alarm.show_obstacle_wait()
        self.store.record_boundary(tape_state, False, "unexpected_partial")
        self.store.record_obstacle(None, True, waiting_seconds=int(self.config.obstacle_wait_seconds))
        self._play_cue("obstacle", "检测到非预期黑胶带，进入保护处理。")
        self._wait_for_obstacle_clear(None)
        return True

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
        self._wait_for_obstacle_clear(shelf_id)
        return True

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

    def _wait_for_obstacle_clear(self, shelf_id: str | None) -> None:
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
                return
        self._avoid_to_safe_side(shelf_id, nested_level=0)

    def _avoid_to_safe_side(self, shelf_id: str | None, nested_level: int = 0) -> None:
        if nested_level > self.config.max_avoidance_depth:
            self.motion.stop()
            self.store.record_robot_status("ERROR", "避障嵌套次数过多，已停车等待人工处理。")
            return
        self.store.record_avoidance_step("start_right_side_avoidance", nested_level=nested_level)
        steps = [
            ("clockwise_90", lambda: self.motion.rotate_right_slow(speed=self.config.turn_speed, duration_seconds=self.config.turn_90_seconds)),
            ("forward_body_1", lambda: self.motion.move_forward_slow(duration_seconds=self.config.avoidance_body_seconds)),
            ("counterclockwise_90_a", lambda: self.motion.rotate_left_slow(speed=self.config.turn_speed, duration_seconds=self.config.turn_90_seconds)),
            ("forward_body_2", lambda: self.motion.move_forward_slow(duration_seconds=self.config.avoidance_body_seconds)),
            ("counterclockwise_90_b", lambda: self.motion.rotate_left_slow(speed=self.config.turn_speed, duration_seconds=self.config.turn_90_seconds)),
            ("forward_body_3", lambda: self.motion.move_forward_slow(duration_seconds=self.config.avoidance_body_seconds)),
            ("clockwise_restore_90", lambda: self.motion.rotate_right_slow(speed=self.config.turn_speed, duration_seconds=self.config.turn_90_seconds)),
        ]
        for label, action in steps:
            if self._stop_event.is_set():
                self.motion.stop()
                return
            self.store.record_avoidance_step(label, nested_level=nested_level)
            action()
            distance_mm = self.sensors.read_distance_mm()
            if distance_mm is not None and distance_mm < self.config.blocked_distance_mm:
                self.motion.stop()
                self.store.record_obstacle(distance_mm, True, waiting_seconds=int(self.config.obstacle_wait_seconds))
                self._avoid_to_safe_side(shelf_id, nested_level=nested_level + 1)
        self._blocked_count = 0
        self._obstacle_active = False
        self.store.record_obstacle(self.sensors.read_distance_mm(), False)

    def _safe_side_for_shelf(self, shelf_id: str | None) -> str | None:
        if shelf_id is None:
            return None
        point = self.warehouse_map["shelf_points"].get(shelf_id)
        if point is None:
            return None
        return str(point["safe_side"]).upper()

    def _move_between(self, current: Cell, target: Cell) -> None:
        dx = target[0] - current[0]
        dy = target[1] - current[1]
        if dx == 1 and dy == 0:
            self.motion.move_forward_slow(duration_seconds=self.config.step_seconds)
        elif dx == -1 and dy == 0:
            self.motion.move_backward_slow(duration_seconds=self.config.step_seconds)
        elif dx == 0 and dy == 1:
            self.motion.strafe_right_slow(duration_seconds=self.config.step_seconds)
        elif dx == 0 and dy == -1:
            self.motion.strafe_left_slow(duration_seconds=self.config.step_seconds)
        else:
            raise ValueError(f"non-adjacent waypoint transition: {current} -> {target}")

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
