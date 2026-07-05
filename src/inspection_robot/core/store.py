from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from threading import Lock

from ..config import DEFAULT_SHELF_MANIFEST, DEFAULT_WAREHOUSE_MAP, JsonValue, ShelfManifest, TagMap, WarehouseMap
from . import rules
from .events import EventRecord, make_event
from .persistence import events_to_csv, load_events, persist_events, remove_temp
from .snapshot import build_status_snapshot
from .status import (
    DashboardState,
    StatusSnapshot,
    new_dashboard_state,
)

MAX_EVENTS = 1000
ACTIONABLE_INVENTORY_EVENT_TYPES = {"added_item", "missing_item"}
POSE_PROTECTED_STATUSES = {
    "ALIGNING_SHELF",
    "SCANNING_SHELF",
    "ANALYZING",
    "WAIT_CONFIRM",
    "ABNORMAL_ALARM",
    "OBSTACLE_WAIT",
    "REROUTING",
    "FORBIDDEN_ZONE_WAIT",
    "GIMBAL_INIT",
    "TURNING_AT_BOUNDARY",
    "AVOIDING_OBSTACLE",
    "NESTED_AVOIDANCE",
    "STOPPED",
    "FINISHED",
    "CONFIRMED",
}
OBSTACLE_CLEAR_RESUME_STATUSES = {
    "OBSTACLE_WAIT",
    "AVOIDING_OBSTACLE",
    "NESTED_AVOIDANCE",
    "PATROLLING",
    "MOVING",
}


class InspectionStore:
    def __init__(
        self,
        tag_map: TagMap,
        *,
        warehouse_map: WarehouseMap | None = None,
        shelf_manifest: ShelfManifest | None = None,
        root: Path | None = None,
        events_path: Path | None = None,
    ) -> None:
        self.tag_map = tag_map
        self.warehouse_map = warehouse_map or DEFAULT_WAREHOUSE_MAP
        self.shelf_manifest = shelf_manifest or DEFAULT_SHELF_MANIFEST
        self.root = root or Path(__file__).resolve().parents[3]
        self.events_path = events_path or self.root / "data" / "events.json"
        self.state = self._new_state()
        self.lock = Lock()
        self._patrol_started = False
        self._boundary_turn_count = 0
        self._load_events()

    def snapshot(self) -> StatusSnapshot:
        with self.lock:
            return build_status_snapshot(self.state)

    def export_events_csv(self) -> str:
        with self.lock:
            return events_to_csv(list(self.state.events))

    def start(self) -> None:
        with self.lock:
            self._patrol_started = True
            self.state.task_status = "PATROLLING"
            self.state.robot_status = "巡逻中"
            self.state.path["status"] = "active"
            self.state.last_message = "自主巡检任务已开始。"

    def stop(self) -> None:
        with self.lock:
            self.state.task_status = "STOPPED"
            self.state.robot_status = "停车"
            self.state.last_message = "巡检任务已停止。"

    def reset(self) -> None:
        with self.lock:
            self.state = self._new_state("系统已重置。")
            self._patrol_started = False
            self._persist_events_locked()

    def handle_tag(self, tag_id: str) -> None:
        self.record_tag(tag_id)

    def record_run_mode(self, mode: str, hardware_connected: bool) -> None:
        normalized = mode.strip().lower() or "simulate"
        if normalized not in {"simulate", "robot"}:
            normalized = "simulate"
        with self.lock:
            self.state.run_mode = normalized
            self.state.hardware_connected = bool(hardware_connected)

    def record_cycle(self, cycle: int, skip_shortage_detection: bool) -> None:
        cycle_value = max(1, int(cycle))
        with self.lock:
            previous = self.state.patrol_cycle
            self.state.patrol_cycle = cycle_value
            self.state.skip_shortage_detection = bool(skip_shortage_detection)
            if cycle_value != previous:
                event_type = "cycle_started"
                message = f"进入第 {cycle_value} 轮巡检。"
                self._append_event_locked(
                    make_event(event_type, priority=1, status="info", source="runtime", message=message)
                )
                self._persist_events_locked()

    def record_cycle_completed(self, cycle: int, observed_shelves: list[str], missed_shelves: list[str]) -> EventRecord:
        cycle_value = max(1, int(cycle))
        evidence = {"observed_shelves": list(observed_shelves), "missed_shelves": list(missed_shelves)}
        with self.lock:
            event = make_event(
                "cycle_completed",
                priority=1,
                status="info",
                source="runtime",
                message=f"第 {cycle_value} 轮巡检完成。",
                evidence=evidence,
            )
            self._append_event_locked(event)
            self._persist_events_locked()
            return event

    def record_camera_cycle_fallback_request(
        self,
        *,
        observed_shelves: list[str],
        expected_shelves: list[str],
        failed_scans: int,
    ) -> EventRecord:
        evidence = {
            "reason": "camera_cycle_fallback_required",
            "observed_shelves": list(observed_shelves),
            "expected_shelves": list(expected_shelves),
            "failed_scans": max(0, int(failed_scans)),
        }
        with self.lock:
            self.state.task_status = "WAIT_CONFIRM"
            self.state.robot_status = "视觉兜底待确认"
            self.state.alarm = {"level": "warning", "message": "视觉兜底待确认", "light": "red"}
            self.state.last_message = "连续扫描未识别到货架，请人工确认是否进入下一轮。"
            event = make_event(
                "scan_failed",
                priority=2,
                status="waiting_confirm",
                source="camera",
                message=self.state.last_message,
                evidence=evidence,
            )
            self._append_event_locked(event)
            self._persist_events_locked()
            return event

    def record_gimbal_initialized(self, yaw: int | None = None, pitch: int | None = None) -> None:
        with self.lock:
            self.state.gimbal = {"side_initialized": True, "yaw": yaw, "pitch": pitch}
            self.state.task_status = "GIMBAL_INIT"
            self.state.robot_status = "云台已初始化"
            self.state.last_message = "摄像云台已初始化到侧向货架视角。"
            self._append_event_locked(
                make_event("gimbal_initialized", status="info", source="runtime", message=self.state.last_message)
            )
            self._persist_events_locked()

    def record_motion_sensor(self, sample: Mapping[str, JsonValue]) -> None:
        with self.lock:
            self.state.motion_sensor = dict(sample)

    def record_boundary(self, tape_state: tuple[int, int, int, int] | None, full_black: bool, kind: str) -> None:
        state = list(tape_state) if tape_state is not None else None
        with self.lock:
            self.state.boundary = {"tape_state": state, "full_black": bool(full_black), "kind": kind}
            if full_black:
                is_exact_full_black = tape_state is not None and all(value == 0 for value in tape_state)
                trigger_text = "四路黑胶带同时触发" if is_exact_full_black else "黑胶带达到列端阈值"
                self.state.task_status = "TURNING_AT_BOUNDARY"
                self.state.robot_status = "列端转向"
                self.state.last_message = f"{trigger_text}，执行列端/禁区动作。"
                self._append_event_locked(
                    make_event(
                        "boundary_full_black",
                        shelf_id=self.state.current_shelf,
                        priority=1,
                        status="info",
                        source="line_sensor",
                        message=self.state.last_message,
                        evidence={"tape_state": state, "kind": kind, "exact_full_black": is_exact_full_black},
                    )
                )
            elif tape_state is not None and any(value == 0 for value in tape_state):
                self.state.task_status = "FORBIDDEN_ZONE_WAIT"
                self.state.robot_status = "非预期黑胶带"
                self.state.last_message = "检测到局部黑胶带，按非预期禁区保护处理。"
                self._append_event_locked(
                    make_event(
                        "unexpected_boundary",
                        shelf_id=self.state.current_shelf,
                        priority=2,
                        status="warning",
                        source="line_sensor",
                        message=self.state.last_message,
                        evidence={"tape_state": state, "kind": kind},
                    )
                )
            self._persist_events_locked()

    def record_boundary_turn(self, direction: str = "clockwise", degrees: int = 90) -> None:
        with self.lock:
            self._boundary_turn_count += 1
            node_id = f"turn-{self._boundary_turn_count}"
            label = f"{'顺时针' if direction == 'clockwise' else '逆时针'} {degrees} 度"
            self._upsert_topology_node_locked({"id": node_id, "kind": "boundary_turn", "label": label})
            self.state.task_status = "TURNING_AT_BOUNDARY"
            self.state.robot_status = "列端转向"
            self.state.last_message = f"列端触发，已{label}转向。"
            self._append_event_locked(
                make_event(
                    "boundary_turn",
                    shelf_id=self.state.current_shelf,
                    priority=1,
                    status="info",
                    source="runtime",
                    message=self.state.last_message,
                    evidence={"direction": direction, "degrees": degrees},
                )
            )
            self._persist_events_locked()

    def record_tag(self, tag_id: str, observed_zone: str | None = None, source: str = "simulate") -> None:
        tag_key = str(tag_id)
        with self.lock:
            info = self.tag_map.get(tag_key)
            if info is None:
                self._append_scan_events_locked([rules.unknown_tag(tag_key, current_shelf=self.state.current_shelf, source=source)])
                return
            self.state.current_tag = tag_key
            self.state.current_item = info["name"]
            self.state.current_zone = observed_zone or info.get("zone")
            if str(info.get("kind", "item")) == "shelf":
                self._record_shelf_arrival_locked(str(info["shelf_id"]), source=source, tag_id=tag_key)
                return
            normal = rules.normal_tag(tag_key, info, current_shelf=self.state.current_shelf or "A1", source=source)
            event = normal or rules.wrong_zone(tag_key, info, current_shelf=self.state.current_shelf or "A1", source=source)
            if event is not None:
                self._append_scan_events_locked([event])

    def record_pose(self, x: int, y: int, heading: str, source: str = "runtime") -> None:
        with self.lock:
            message = f"当前位置更新为 ({x}, {y}, {heading})。"
            self.state.pose = {"x": x, "y": y, "heading": heading}
            if self.state.task_status not in POSE_PROTECTED_STATUSES:
                self.state.task_status = "MOVING"
                self.state.robot_status = "移动中"
                self.state.last_message = message
            self._append_event_locked(make_event("path_step", shelf_id=self.state.current_shelf, target=self.state.current_target, source=source, message=message))
            self._persist_events_locked()

    def record_path(self, waypoints: list[tuple[int, int]], status: str = "active") -> None:
        with self.lock:
            self.state.path = {"status": status, "waypoints": [[x, y] for x, y in waypoints], "next_index": 0}
            self.state.task_status = "PLAN_READY"
            self.state.robot_status = "路径就绪"
            self.state.last_message = f"路径规划完成，共 {len(waypoints)} 个路径点。"
            self._append_event_locked(make_event("path_planned", status="info", source="planner", message=self.state.last_message))
            self._persist_events_locked()

    def record_shelf_arrival(self, shelf_id: str, target: str | None = None) -> None:
        with self.lock:
            self._record_shelf_arrival_locked(shelf_id, target=target, source="runtime")

    def record_scan_start(self, shelf_id: str, target: str | None = None, frame_id: str | None = None) -> None:
        with self.lock:
            self.state.current_shelf = shelf_id
            self.state.current_target = target or f"{shelf_id}_SCAN"
            self.state.scan = {"active": True, "shelf_id": shelf_id, "detected_items": [], "frame_id": frame_id, "detections": []}
            self.state.task_status = "SCANNING_SHELF"
            self.state.robot_status = "侧向扫描中"
            self.state.last_message = f"{shelf_id} 货架开始侧向扫描。"
            self._mark_shelf_locked(shelf_id, "scanning", 0)
            self._append_event_locked(
                make_event("shelf_aligned", shelf_id=shelf_id, target=self.state.current_target, status="info", source="runtime", frame_id=frame_id, message=self.state.last_message)
            )
            self._persist_events_locked()

    def record_scan_result(self, shelf_id: str, detected_items: list[str], frame_id: str | None = None) -> list[EventRecord]:
        with self.lock:
            unique_items = self._unique_item_ids(rules.normalize_detected_item_ids(detected_items, self.tag_map))
            self.state.scan = {"active": False, "shelf_id": shelf_id, "detected_items": unique_items, "frame_id": frame_id, "detections": []}
            self.state.current_shelf = shelf_id
            previous_items = self._shelf_item_ids_locked(shelf_id)
            events = rules.evaluate_shelf_scan(
                shelf_id,
                unique_items,
                self.shelf_manifest,
                self.tag_map,
                frame_id=frame_id,
                skip_missing=self.state.skip_shortage_detection,
            )
            events.extend(self._inventory_change_events_locked(shelf_id, previous_items, unique_items, frame_id))
            self._update_shelf_items_locked(shelf_id, unique_items, events)
            self._append_scan_events_locked(events)
            return events

    def record_detection_evidence(self, shelf_id: str, detections: list[Mapping[str, JsonValue]], frame_id: str | None = None) -> list[EventRecord]:
        with self.lock:
            normalized = self._unique_detections([dict(detection) for detection in detections])
            detected_items = self._item_ids_from_detections(normalized)
            self.state.scan = {"active": False, "shelf_id": shelf_id, "detected_items": detected_items, "frame_id": frame_id, "detections": normalized}
            self.state.current_shelf = shelf_id
            previous_items = self._shelf_item_ids_locked(shelf_id)
            events = rules.evaluate_detection_evidence(
                shelf_id,
                normalized,
                self.shelf_manifest,
                self.tag_map,
                frame_id=frame_id,
                skip_missing=self.state.skip_shortage_detection,
            )
            events.extend(self._inventory_change_events_locked(shelf_id, previous_items, detected_items, frame_id))
            self._update_shelf_items_locked(shelf_id, detected_items, events)
            self._append_scan_events_locked(events)
            return events

    def record_forbidden_zone(self, zone_id: str | None, blocked: bool) -> None:
        with self.lock:
            zone_key = zone_id or "map"
            message = f"禁区 {zone_key} {'触发' if blocked else '解除'}。"
            if blocked:
                self.state.task_status = "FORBIDDEN_ZONE_WAIT"
                self.state.robot_status = "禁区等待"
                self.state.last_message = message
                self.state.alarm = {"level": "warning", "message": "禁区等待", "light": "blue"}
            elif self.state.task_status == "FORBIDDEN_ZONE_WAIT":
                self.state.task_status = "MOVING"
                self.state.robot_status = "移动中"
                self.state.last_message = message
                self.state.alarm = {"level": "normal", "message": "正常", "light": "green"}
            self._upsert_forbidden_zone_locked(zone_key, blocked)
            self._append_event_locked(
                make_event("forbidden_zone_detected", shelf_id=self.state.current_shelf, priority=2 if blocked else 1, status="warning" if blocked else "info", message=message, source="line_sensor")
            )
            self._persist_events_locked()

    def record_obstacle(self, distance_mm: int | None, blocked: bool, waiting_seconds: int = 0) -> None:
        with self.lock:
            self.state.obstacle = {"distance_mm": distance_mm, "blocked": blocked, "waiting_seconds": waiting_seconds}
            if blocked:
                self.state.task_status = "OBSTACLE_WAIT"
                self.state.robot_status = "障碍等待"
                self.state.last_message = f"检测到障碍，小车停车等待 {waiting_seconds} 秒。"
                self.state.alarm = {"level": "warning", "message": "障碍等待", "light": "blue"}
                event_type = "obstacle_wait"
                message = self.state.last_message
            else:
                message = "障碍已解除，恢复巡检。"
                if self.state.task_status in OBSTACLE_CLEAR_RESUME_STATUSES:
                    self.state.task_status = "PATROLLING"
                    self.state.robot_status = "巡逻中"
                    self.state.last_message = message
                    self.state.alarm = {"level": "normal", "message": "正常", "light": "green"}
                event_type = "obstacle_clear"
            self._append_event_locked(make_event(event_type, shelf_id=self.state.current_shelf, priority=1, status="info", message=message, source="ultrasonic"))
            self._persist_events_locked()

    def record_avoidance_step(self, step: str, nested_level: int = 0) -> None:
        with self.lock:
            self.state.task_status = "NESTED_AVOIDANCE" if nested_level else "AVOIDING_OBSTACLE"
            self.state.robot_status = "嵌套避障" if nested_level else "绕行避障"
            self.state.last_message = f"执行避障动作：{step}。"
            self._append_event_locked(
                make_event(
                    "obstacle_avoidance_nested" if nested_level else "obstacle_avoidance_step",
                    shelf_id=self.state.current_shelf,
                    priority=2,
                    status="warning" if nested_level else "info",
                    source="runtime",
                    message=self.state.last_message,
                    evidence={"step": step, "nested_level": nested_level},
                )
            )
            self._persist_events_locked()

    def record_audio_cue(self, cue: str, message: str | None = None, error: str | None = None) -> None:
        with self.lock:
            self.state.audio = {"last_cue": cue, "last_message": message, "last_error": error}
            self._append_event_locked(
                make_event(
                    "audio_cue",
                    shelf_id=self.state.current_shelf,
                    priority=1 if error is None else 2,
                    status="info" if error is None else "warning",
                    source="audio",
                    message=message or error or f"音频提示：{cue}",
                    evidence={"cue": cue, "error": error},
                )
            )
            self._persist_events_locked()

    def record_light_cue(self, color: str, reason: str | None = None) -> None:
        with self.lock:
            self.state.alarm = {**self.state.alarm, "light": color}
            self._append_event_locked(
                make_event(
                    "light_cue",
                    shelf_id=self.state.current_shelf,
                    priority=1,
                    status="info",
                    source="light",
                    message=reason or f"灯光提示：{color}",
                    evidence={"color": color},
                )
            )
            self._persist_events_locked()

    def record_motion_debug(
        self,
        stage: str,
        message: str,
        *,
        status: str = "PATROLLING",
        evidence: Mapping[str, JsonValue] | None = None,
    ) -> None:
        with self.lock:
            self.state.task_status = status
            self.state.robot_status = {
                "PATROLLING": "巡逻中",
                "TURNING_AT_BOUNDARY": "列端转向",
                "FORBIDDEN_ZONE_WAIT": "禁区等待",
                "OBSTACLE_WAIT": "障碍等待",
                "AVOIDING_OBSTACLE": "绕行避障",
                "STOPPED": "停车",
                "ERROR": "错误",
            }.get(status, status)
            self.state.last_message = message
            detail = dict(evidence or {})
            detail["stage"] = stage
            self._append_event_locked(
                make_event(
                    "motion_debug",
                    shelf_id=self.state.current_shelf,
                    target=self.state.current_target,
                    priority=1,
                    status="info" if status not in {"ERROR", "FORBIDDEN_ZONE_WAIT", "OBSTACLE_WAIT"} else "warning",
                    source="runtime",
                    message=message,
                    evidence=detail,
                )
            )
            self._persist_events_locked()

    def record_topology_node(self, node: dict[str, object]) -> None:
        with self.lock:
            self._upsert_topology_node_locked(node)
            self._persist_events_locked()

    def record_topology_edge(self, source: str, target: str) -> None:
        with self.lock:
            self._append_topology_edge_locked(source, target)
            self._persist_events_locked()

    def record_robot_status(self, status: str, message: str | None = None) -> None:
        with self.lock:
            self.state.task_status = status
            self.state.robot_status = status
            if message is not None:
                self.state.last_message = message

    def confirm(self, event_id: str | None = None) -> bool:
        with self.lock:
            target = next(
                (
                    event
                    for event in reversed(self.state.events)
                    if self._event_is_confirmable(event, event_id)
                ),
                None,
            )
            if target is None:
                self.state.last_message = "当前没有待确认异常。"
                return False
            original_status = str(target.get("status", ""))
            target["status"] = "confirmed"
            target["message"] = "人工已完成处理确认。"
            if target.get("type") in ACTIONABLE_INVENTORY_EVENT_TYPES and target.get("shelf_id") is not None:
                self._resolve_inventory_event_locked(target)
            self.state.task_status = "CONFIRMED"
            self.state.robot_status = "已确认"
            self.state.alarm = {"level": "normal", "message": "正常", "light": "green"}
            self._append_event_locked(
                make_event("manual_confirm", tag_id=target["tag_id"], item=target["item"], zone=target["zone"], expected_zone=target["expected_zone"], shelf_id=target.get("shelf_id"), expected_shelf=target.get("expected_shelf"), priority=max(int(target["priority"]), 1), status="info", message=f"人工确认事件 {target['id']}。")
            )
            if any(self._event_requires_attention(event) for event in self.state.events):
                self.state.task_status = "WAIT_CONFIRM"
                self.state.robot_status = "仍有异常待确认"
                self.state.alarm = {"level": "warning", "message": "待确认异常", "light": "red"}
                self.state.last_message = f"异常事件 {target['id']} 已确认，仍有异常等待处理。"
            else:
                label = "库存变化" if original_status in {"warning", "info"} else "异常事件"
                self.state.last_message = f"{label} {target['id']} 已人工确认，恢复巡检。"
            self._persist_events_locked()
            return True

    def finish_run(self) -> None:
        with self.lock:
            if not self._patrol_started:
                self.state.last_message = "巡检尚未开始，未生成结束事件。"
                return
            self.state.task_status = "FINISHED"
            self.state.robot_status = "巡检完成"
            self.state.last_message = "巡检完成。"
            self.state.alarm = {"level": "normal", "message": "正常", "light": "green"}
            self._append_event_locked(make_event("system", status="info", message="巡检完成。"))
            self._persist_events_locked()

    def _append_event_locked(self, event: EventRecord) -> None:
        self.state.events.append(event)
        overflow = len(self.state.events) - MAX_EVENTS
        if overflow > 0:
            del self.state.events[:overflow]

    def _load_events(self) -> None:
        temp_path = self.events_path.parent / f"{self.events_path.name}.tmp"
        try:
            remove_temp(temp_path)
        except OSError:
            pass
        if not self.events_path.exists():
            return
        try:
            self.state.events = load_events(self.events_path)
        except (OSError, ValueError) as exc:
            self.state.last_message = f"历史事件读取失败：{exc}"

    def _persist_events_locked(self) -> None:
        temp_path = self.events_path.parent / f"{self.events_path.name}.tmp"
        try:
            persist_events(self.events_path, self.state.events)
        except OSError as exc:
            self.state.last_message = f"事件已记录，但写入 {self.events_path} 失败：{exc}"
            try:
                remove_temp(temp_path)
            except OSError:
                return

    def _new_state(self, message: str | None = None) -> DashboardState:
        return new_dashboard_state(self.warehouse_map, self.shelf_manifest, message)

    def _record_shelf_arrival_locked(self, shelf_id: str, target: str | None = None, source: str = "runtime", tag_id: str | None = None) -> None:
        self.state.current_shelf = shelf_id
        self.state.current_target = target or f"{shelf_id}_SCAN"
        self.state.task_status = "ALIGNING_SHELF"
        self.state.robot_status = "对准货架"
        self.state.last_message = f"到达 {shelf_id} 货架，准备侧向扫描。"
        self._mark_shelf_locked(shelf_id, "aligning", 0)
        self._upsert_topology_node_locked({"id": shelf_id, "kind": "shelf", "label": shelf_id})
        self._append_event_locked(make_event("shelf_arrived", tag_id=tag_id, shelf_id=shelf_id, target=self.state.current_target, source=source, message=self.state.last_message))
        self._persist_events_locked()

    def _append_scan_events_locked(self, events: list[EventRecord]) -> None:
        if not events:
            return
        has_waiting = any(event["status"] == "waiting_confirm" for event in events)
        has_warning = any(event["status"] in {"warning", "error"} for event in events)
        for event in events:
            self._append_event_locked(event)
        shelf_id = events[0].get("shelf_id")
        if shelf_id is not None:
            anomaly_count = sum(1 for event in events if event["status"] in {"waiting_confirm", "warning", "error"})
            status = "waiting_confirm" if has_waiting else "abnormal" if has_warning else "normal"
            self._mark_shelf_locked(str(shelf_id), status, anomaly_count)
        latest = events[0]
        self.state.current_tag = latest.get("tag_id")
        self.state.current_item = latest.get("item")
        if has_waiting:
            self.state.task_status = "WAIT_CONFIRM"
            self.state.robot_status = "异常告警"
            self.state.alarm = {"level": "warning", "message": "待确认异常", "light": "red"}
            self.state.last_message = f"扫描完成，发现 {sum(1 for event in events if event['status'] == 'waiting_confirm')} 个异常。"
        elif has_warning:
            self.state.task_status = "ABNORMAL_ALARM"
            self.state.robot_status = "库存变化"
            self.state.alarm = {"level": "warning", "message": "库存变化", "light": "yellow"}
            self.state.last_message = f"扫描完成，发现 {sum(1 for event in events if event['status'] in {'warning', 'error'})} 个库存变化。"
        else:
            self.state.task_status = "NORMAL_LOGGED"
            self.state.robot_status = "正常已记录"
            self.state.alarm = {"level": "normal", "message": "正常", "light": "green"}
            self.state.last_message = "扫描完成，未发现异常。"
        self._persist_events_locked()

    def _mark_shelf_locked(self, shelf_id: str, status: str, anomaly_count: int) -> None:
        for shelf in self.state.shelves:
            if shelf.get("shelf_id") == shelf_id:
                shelf["status"] = status
                shelf["anomaly_count"] = anomaly_count
                return
        self.state.shelves.append({"shelf_id": shelf_id, "status": status, "anomaly_count": anomaly_count, "items": []})

    def _shelf_item_ids_locked(self, shelf_id: str) -> list[str]:
        for shelf in self.state.shelves:
            if shelf.get("shelf_id") != shelf_id:
                continue
            items = shelf.get("items")
            if not isinstance(items, list):
                return []
            result: list[str] = []
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                item_id = item.get("item_id")
                if item_id is not None and item.get("status") != "missing":
                    result.append(str(item_id))
            return result
        return []

    def _shelf_inventory_observed_locked(self, shelf_id: str) -> bool:
        for shelf in self.state.shelves:
            if shelf.get("shelf_id") == shelf_id:
                return bool(shelf.get("inventory_observed"))
        return False

    def _item_ids_from_detections(self, detections: list[Mapping[str, JsonValue]]) -> list[str]:
        item_ids: list[str] = []
        for detection in detections:
            tag_id = detection.get("tag_id")
            if tag_id is not None:
                info = self.tag_map.get(str(tag_id))
                if info is not None:
                    if str(info.get("kind", "item")) == "item" and info.get("item_id") is not None:
                        item_ids.append(str(info["item_id"]))
                    continue
                try:
                    numeric_id = int(str(tag_id))
                except ValueError:
                    pass
                else:
                    if 101 <= numeric_id <= 120:
                        continue
            item_id = detection.get("item_id")
            if item_id is None:
                continue
            item_ids.extend(rules.normalize_detected_item_ids([str(item_id)], self.tag_map))
        return self._unique_item_ids(item_ids)

    def _unique_item_ids(self, item_ids: list[str]) -> list[str]:
        seen: set[str] = set()
        unique: list[str] = []
        for item_id in item_ids:
            key = str(item_id)
            if key in seen:
                continue
            seen.add(key)
            unique.append(key)
        return unique

    def _unique_detections(self, detections: list[dict[str, JsonValue]]) -> list[dict[str, JsonValue]]:
        positions: dict[tuple[str, str], int] = {}
        unique: list[dict[str, JsonValue]] = []
        for detection in detections:
            key = self._detection_identity(detection)
            if key is None:
                unique.append(detection)
                continue
            position = positions.get(key)
            if position is None:
                positions[key] = len(unique)
                unique.append(detection)
                continue
            unique[position] = self._merge_detection(unique[position], detection)
        return unique

    def _detection_identity(self, detection: Mapping[str, JsonValue]) -> tuple[str, str] | None:
        tag_id = detection.get("tag_id")
        if tag_id is not None:
            info = self.tag_map.get(str(tag_id))
            if info is not None:
                if str(info.get("kind", "item")) == "item" and info.get("item_id") is not None:
                    return ("item", str(info["item_id"]))
                if str(info.get("kind")) == "shelf" and info.get("shelf_id") is not None:
                    return ("shelf", str(info["shelf_id"]))
            try:
                numeric_id = int(str(tag_id))
            except ValueError:
                pass
            else:
                if 101 <= numeric_id <= 120:
                    return ("shelf_tag", str(tag_id))
            return ("tag", str(tag_id))
        item_id = detection.get("item_id")
        if item_id is not None:
            normalized = rules.normalize_detected_item_ids([str(item_id)], self.tag_map)
            if normalized:
                return ("item", normalized[0])
            return None
        ocr_text = detection.get("ocr_text")
        if ocr_text is not None and str(ocr_text).strip():
            return ("ocr", str(ocr_text).strip().casefold())
        return None

    def _merge_detection(self, current: dict[str, JsonValue], incoming: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
        merged = dict(current)
        for key, value in incoming.items():
            if value is None or value == "":
                continue
            if merged.get(key) is None or merged.get(key) == "":
                merged[key] = value
        return merged

    def _inventory_change_events_locked(
        self,
        shelf_id: str,
        previous_items: list[str],
        detected_items: list[str],
        frame_id: str | None,
    ) -> list[EventRecord]:
        if self.state.skip_shortage_detection:
            return []
        if not previous_items and not self._shelf_inventory_observed_locked(shelf_id):
            return []
        previous = set(previous_items)
        current = set(detected_items)
        events: list[EventRecord] = []
        for item_id in sorted(current - previous):
            tag_id, info = self._item_info_for_id(item_id)
            item_name = str(info.get("name", item_id)) if info is not None else item_id
            events.append(
                make_event(
                    "added_item",
                    tag_id=tag_id,
                    item=item_name,
                    shelf_id=shelf_id,
                    expected_shelf=shelf_id,
                    priority=max(int(info.get("priority", 1)) if info is not None else 1, 1),
                    status="info",
                    message=f"{shelf_id} 货架新增 {item_name}。",
                    source="inventory_diff",
                    frame_id=frame_id,
                    evidence={"identity_kind": "item", "change": "added", "item_id": item_id},
                )
            )
        for item_id in sorted(previous - current):
            tag_id, info = self._item_info_for_id(item_id)
            item_name = str(info.get("name", item_id)) if info is not None else item_id
            events.append(
                make_event(
                    "missing_item",
                    tag_id=tag_id,
                    item=item_name,
                    shelf_id=shelf_id,
                    expected_shelf=shelf_id,
                    priority=max(int(info.get("priority", 1)) if info is not None else 1, 1),
                    status="warning",
                    message=f"{shelf_id} 货架缺失 {item_name}。",
                    source="inventory_diff",
                    frame_id=frame_id,
                    evidence={"identity_kind": "item", "change": "missing", "item_id": item_id},
                )
            )
        return events

    def _update_shelf_items_locked(self, shelf_id: str, detected_items: list[str], events: list[EventRecord]) -> None:
        detected_set = set(detected_items)
        missing_ids = {
            str(event.get("evidence", {}).get("item_id"))
            for event in events
            if event.get("type") == "missing_item" and isinstance(event.get("evidence"), Mapping) and event.get("evidence", {}).get("item_id")
        }
        added_ids = {
            str(event.get("evidence", {}).get("item_id"))
            for event in events
            if event.get("type") == "added_item" and isinstance(event.get("evidence"), Mapping) and event.get("evidence", {}).get("item_id")
        }
        all_ids = sorted(detected_set | missing_ids)
        items = []
        for item_id in all_ids:
            tag_id, info = self._item_info_for_id(item_id)
            status = "missing" if item_id in missing_ids and item_id not in detected_set else "added" if item_id in added_ids else "present"
            items.append(
                {
                    "item_id": item_id,
                    "tag_id": tag_id,
                    "name": str(info.get("name", item_id)) if info is not None else item_id,
                    "status": status,
                }
            )
        for shelf in self.state.shelves:
            if shelf.get("shelf_id") == shelf_id:
                shelf["items"] = items
                shelf["inventory_observed"] = True
                return
        self.state.shelves.append({"shelf_id": shelf_id, "status": "normal", "anomaly_count": 0, "items": items, "inventory_observed": True})

    def _event_is_confirmable(self, event: EventRecord, event_id: str | None) -> bool:
        if event_id is not None and event.get("id") != event_id:
            return False
        if event.get("status") == "confirmed":
            return False
        if event.get("status") == "waiting_confirm":
            return True
        return event.get("type") in ACTIONABLE_INVENTORY_EVENT_TYPES and event.get("status") in {"warning", "info"}

    def _event_requires_attention(self, event: EventRecord) -> bool:
        if event.get("status") == "waiting_confirm":
            return True
        return event.get("type") in ACTIONABLE_INVENTORY_EVENT_TYPES and event.get("status") in {"warning", "info"}

    def _resolve_inventory_event_locked(self, event: EventRecord) -> None:
        shelf_id = str(event.get("shelf_id"))
        evidence = event.get("evidence")
        item_id = str(evidence.get("item_id")) if isinstance(evidence, Mapping) and evidence.get("item_id") else None
        for shelf in self.state.shelves:
            if shelf.get("shelf_id") != shelf_id:
                continue
            items = shelf.get("items")
            if item_id is not None and isinstance(items, list):
                resolved_items = []
                for item in items:
                    if (
                        isinstance(item, Mapping)
                        and str(item.get("item_id")) == item_id
                        and item.get("status") in {"missing", "added"}
                    ):
                        if event.get("type") == "added_item":
                            next_item = dict(item)
                            next_item["status"] = "present"
                            resolved_items.append(next_item)
                        continue
                    resolved_items.append(item)
                shelf["items"] = resolved_items
            if not any(isinstance(item, Mapping) and item.get("status") in {"missing", "added"} for item in shelf.get("items", [])):
                shelf["status"] = "normal"
                shelf["anomaly_count"] = 0
            return

    def _item_info_for_id(self, item_id: str) -> tuple[str | None, Mapping[str, JsonValue] | None]:
        for tag_id, info in self.tag_map.items():
            if str(info.get("kind", "item")) == "item" and str(info.get("item_id")) == item_id:
                return tag_id, info
        return None, None

    def _upsert_forbidden_zone_locked(self, zone_id: str, blocked: bool) -> None:
        for zone in self.state.forbidden_zones:
            if zone.get("id") == zone_id:
                zone["blocked"] = blocked
                return
        self.state.forbidden_zones.append({"id": zone_id, "cells": [], "blocked": blocked})

    def _upsert_topology_node_locked(self, node: Mapping[str, object]) -> None:
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            return
        nodes = self.state.topology.setdefault("nodes", [])
        if not isinstance(nodes, list):
            nodes = []
            self.state.topology["nodes"] = nodes
        previous = self.state.topology.get("current_node")
        normalized = {key: value for key, value in node.items() if value is not None}
        existing = next((item for item in nodes if isinstance(item, dict) and item.get("id") == node_id), None)
        if existing is None:
            nodes.append(normalized)
        else:
            existing.update(normalized)
        if isinstance(previous, str) and previous and previous != node_id:
            self._append_topology_edge_locked(previous, node_id)
        self.state.topology["current_node"] = node_id
        self.state.topology["status"] = "building"

    def _append_topology_edge_locked(self, source: str, target: str) -> None:
        if not source or not target or source == target:
            return
        edges = self.state.topology.setdefault("edges", [])
        if not isinstance(edges, list):
            edges = []
            self.state.topology["edges"] = edges
        edge = [source, target]
        if edge not in edges:
            edges.append(edge)
