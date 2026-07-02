from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from .config import ShelfManifest, WarehouseMap
from .core.planner import PlanningError, RouteStep, plan_patrol_route
from .core.store import InspectionStore
from .robot import alarm, motion, sensors
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
        detection_provider: DetectionProvider = tag_detector.iter_detections,
    ) -> None:
        self.store = store
        self.warehouse_map = warehouse_map
        self.shelf_manifest = shelf_manifest
        self.config = config or RobotRuntimeConfig()
        self.motion = motion_adapter
        self.sensors = sensor_adapter
        self.alarm = alarm_adapter
        self.detection_provider = detection_provider
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._blocked_count = 0
        self._obstacle_active = False

    def start(self, shelf_order: Iterable[str] | None = None) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        order = list(shelf_order) if shelf_order is not None else list(self.shelf_manifest)
        self._thread = threading.Thread(target=self._run_patrol_safely, kwargs={"shelf_order": order}, daemon=True)
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
        tape_state = self.sensors.read_tape_boundary()
        if self.sensors.tape_boundary_detected(tape_state):
            self.motion.stop()
            self.alarm.show_warning()
            self.store.record_forbidden_zone("black-tape-boundary", True)
            self.motion.move_backward_slow(duration_seconds=min(self.config.step_seconds, 0.25))
            return False

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
        self.store.record_obstacle(distance_mm, True)
        self._wait_for_obstacle_clear(shelf_id)
        return True

    def _wait_for_obstacle_clear(self, shelf_id: str | None) -> None:
        deadline = time.monotonic() + 3.0
        while not self._stop_event.is_set() and time.monotonic() < deadline:
            time.sleep(self.config.poll_seconds)
            distance_mm = self.sensors.read_distance_mm()
            if distance_mm is not None and distance_mm >= self.config.clear_distance_mm:
                self._blocked_count = 0
                self._obstacle_active = False
                self.alarm.clear_alarm()
                self.store.record_obstacle(distance_mm, False)
                return
        self._avoid_to_safe_side(shelf_id)

    def _avoid_to_safe_side(self, shelf_id: str | None) -> None:
        safe_side = self._safe_side_for_shelf(shelf_id)
        if safe_side == "W":
            self.motion.strafe_left_slow(duration_seconds=self.config.step_seconds)
        elif safe_side == "E":
            self.motion.strafe_right_slow(duration_seconds=self.config.step_seconds)
        elif safe_side == "N":
            self.motion.move_forward_slow(duration_seconds=self.config.step_seconds)
        elif safe_side == "S":
            self.motion.move_backward_slow(duration_seconds=self.config.step_seconds)
        else:
            self.store.record_robot_status("OBSTACLE_WAIT", "obstacle remains and no safe side is configured")

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
        detections = self._collect_detections()
        frame_id = f"runtime-{shelf_id.lower()}-{int(time.time())}"
        if detections:
            self.store.record_detection_evidence(shelf_id, detections, frame_id=frame_id)
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
