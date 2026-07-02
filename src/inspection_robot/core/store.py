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
            self.state.task_status = "PLANNING"
            self.state.robot_status = "规划中"
            self.state.path["status"] = "planning"
            self.state.last_message = "巡检任务已开始，等待固定地图路径规划。"

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
            self.state.pose = {"x": x, "y": y, "heading": heading}
            self.state.task_status = "MOVING"
            self.state.robot_status = "移动中"
            self.state.last_message = f"当前位置更新为 ({x}, {y}, {heading})。"
            self._append_event_locked(make_event("path_step", shelf_id=self.state.current_shelf, target=self.state.current_target, source=source, message=self.state.last_message))
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

    def record_scan_result(self, shelf_id: str, detected_items: list[str], frame_id: str | None = None) -> None:
        with self.lock:
            self.state.scan = {"active": False, "shelf_id": shelf_id, "detected_items": list(detected_items), "frame_id": frame_id, "detections": []}
            self.state.current_shelf = shelf_id
            events = rules.evaluate_shelf_scan(shelf_id, detected_items, self.shelf_manifest, self.tag_map, frame_id=frame_id)
            self._append_scan_events_locked(events)

    def record_detection_evidence(self, shelf_id: str, detections: list[Mapping[str, JsonValue]], frame_id: str | None = None) -> None:
        with self.lock:
            normalized = [dict(detection) for detection in detections]
            self.state.scan = {"active": False, "shelf_id": shelf_id, "detected_items": [], "frame_id": frame_id, "detections": normalized}
            self.state.current_shelf = shelf_id
            events = rules.evaluate_detection_evidence(shelf_id, detections, self.shelf_manifest, self.tag_map, frame_id=frame_id)
            self._append_scan_events_locked(events)

    def record_forbidden_zone(self, zone_id: str | None, blocked: bool) -> None:
        with self.lock:
            zone_key = zone_id or "map"
            self.state.task_status = "FORBIDDEN_ZONE_WAIT" if blocked else "MOVING"
            self.state.robot_status = "禁区等待" if blocked else "移动中"
            self.state.last_message = f"禁区 {zone_key} {'触发' if blocked else '解除'}。"
            self._upsert_forbidden_zone_locked(zone_key, blocked)
            self._append_event_locked(
                make_event("forbidden_zone_detected", shelf_id=self.state.current_shelf, priority=2 if blocked else 1, status="warning" if blocked else "info", message=self.state.last_message, source="line_sensor")
            )
            self._persist_events_locked()

    def record_obstacle(self, distance_mm: int | None, blocked: bool) -> None:
        with self.lock:
            self.state.obstacle = {"distance_mm": distance_mm, "blocked": blocked}
            if blocked:
                self.state.task_status = "OBSTACLE_WAIT"
                self.state.robot_status = "障碍等待"
                self.state.last_message = "检测到障碍，小车停车等待。"
                self.state.alarm = {"level": "warning", "message": "障碍等待"}
                event_type = "obstacle_wait"
                message = "检测到障碍，小车停车等待。"
            else:
                self.state.task_status = "MOVING"
                self.state.robot_status = "移动中"
                self.state.last_message = "障碍已解除，恢复巡检。"
                self.state.alarm = {"level": "normal", "message": "正常"}
                event_type = "obstacle_clear"
                message = "障碍已解除，恢复巡检。"
            self._append_event_locked(make_event(event_type, shelf_id=self.state.current_shelf, priority=1, status="info", message=message, source="ultrasonic"))
            self._persist_events_locked()

    def record_robot_status(self, status: str, message: str | None = None) -> None:
        with self.lock:
            self.state.task_status = status
            self.state.robot_status = status
            if message is not None:
                self.state.last_message = message

    def confirm(self, event_id: str | None = None) -> bool:
        with self.lock:
            target = next((event for event in self.state.events if event["status"] == "waiting_confirm" and (event_id is None or event["id"] == event_id)), None)
            if target is None:
                self.state.last_message = "当前没有待确认异常。"
                return False
            target["status"] = "confirmed"
            target["message"] = "人工已完成处理确认。"
            self.state.task_status = "CONFIRMED"
            self.state.robot_status = "已确认"
            self.state.alarm = {"level": "normal", "message": "正常"}
            self._append_event_locked(
                make_event("manual_confirm", tag_id=target["tag_id"], item=target["item"], zone=target["zone"], expected_zone=target["expected_zone"], shelf_id=target.get("shelf_id"), expected_shelf=target.get("expected_shelf"), priority=max(int(target["priority"]), 1), status="info", message=f"人工确认事件 {target['id']}。")
            )
            if any(event["status"] == "waiting_confirm" for event in self.state.events):
                self.state.task_status = "WAIT_CONFIRM"
                self.state.robot_status = "仍有异常待确认"
                self.state.alarm = {"level": "warning", "message": "待确认异常"}
                self.state.last_message = f"异常事件 {target['id']} 已确认，仍有异常等待处理。"
            else:
                self.state.last_message = f"异常事件 {target['id']} 已人工确认，恢复巡检。"
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
            self.state.alarm = {"level": "normal", "message": "正常"}
            self._append_event_locked(make_event("system", status="info", message="巡检完成。"))
            self._persist_events_locked()

    def _append_event_locked(self, event: EventRecord) -> None:
        self.state.events.insert(0, event)

    def _load_events(self) -> None:
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
        self._append_event_locked(make_event("shelf_arrived", tag_id=tag_id, shelf_id=shelf_id, target=self.state.current_target, source=source, message=self.state.last_message))
        self._persist_events_locked()

    def _append_scan_events_locked(self, events: list[EventRecord]) -> None:
        has_waiting = any(event["status"] == "waiting_confirm" for event in events)
        for event in events:
            self._append_event_locked(event)
        shelf_id = events[0].get("shelf_id") if events else self.state.current_shelf
        if shelf_id is not None:
            self._mark_shelf_locked(str(shelf_id), "waiting_confirm" if has_waiting else "normal", sum(1 for event in events if event["status"] == "waiting_confirm"))
        if events:
            latest = events[0]
            self.state.current_tag = latest.get("tag_id")
            self.state.current_item = latest.get("item")
        if has_waiting:
            self.state.task_status = "WAIT_CONFIRM"
            self.state.robot_status = "异常告警"
            self.state.alarm = {"level": "warning", "message": "待确认异常"}
            self.state.last_message = f"扫描完成，发现 {sum(1 for event in events if event['status'] == 'waiting_confirm')} 个异常。"
        else:
            self.state.task_status = "NORMAL_LOGGED"
            self.state.robot_status = "正常已记录"
            self.state.alarm = {"level": "normal", "message": "正常"}
            self.state.last_message = "扫描完成，未发现异常。"
        self._persist_events_locked()

    def _mark_shelf_locked(self, shelf_id: str, status: str, anomaly_count: int) -> None:
        for shelf in self.state.shelves:
            if shelf.get("shelf_id") == shelf_id:
                shelf["status"] = status
                shelf["anomaly_count"] = anomaly_count
                return
        self.state.shelves.append({"shelf_id": shelf_id, "status": status, "anomaly_count": anomaly_count})

    def _upsert_forbidden_zone_locked(self, zone_id: str, blocked: bool) -> None:
        for zone in self.state.forbidden_zones:
            if zone.get("id") == zone_id:
                zone["blocked"] = blocked
                return
        self.state.forbidden_zones.append({"id": zone_id, "cells": [], "blocked": blocked})
