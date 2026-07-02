from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock
from typing import Any
from uuid import uuid4


@dataclass
class DashboardState:
    task_status: str = "未开始"
    robot_status: str = "待命"
    current_tag: str | None = None
    current_item: str | None = None
    current_zone: str | None = None
    last_message: str = "系统已启动，等待开始巡检。"
    events: list[dict[str, Any]] = field(default_factory=list)


class InspectionStore:
    def __init__(self, tag_map: dict[str, dict[str, str]]) -> None:
        self.tag_map = tag_map
        self.state = DashboardState()
        self.lock = Lock()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "task_status": self.state.task_status,
                "robot_status": self.state.robot_status,
                "current_tag": self.state.current_tag,
                "current_item": self.state.current_item,
                "current_zone": self.state.current_zone,
                "last_message": self.state.last_message,
                "events": list(self.state.events),
            }

    def start(self) -> None:
        with self.lock:
            self.state.task_status = "巡检中"
            self.state.robot_status = "等待标签"
            self.state.last_message = "巡检任务已开始。当前版本使用模拟标签，下一步接入真实 AprilTag。"

    def stop(self) -> None:
        with self.lock:
            self.state.task_status = "已停止"
            self.state.robot_status = "停车"
            self.state.last_message = "巡检任务已停止。"

    def reset(self) -> None:
        with self.lock:
            self.state = DashboardState(last_message="系统已重置。")

    def handle_tag(self, tag_id: str) -> None:
        with self.lock:
            info = self.tag_map.get(tag_id)
            if info is None:
                self.state.robot_status = "识别异常"
                self.state.last_message = f"识别到未知标签 {tag_id}。"
                self._add_event(tag_id, "Unknown", "-", "待确认", "未知标签，需要人工核查。")
                return

            item = info["name"]
            zone = info["zone"]
            expected_zone = info["expected_zone"]
            self.state.current_tag = tag_id
            self.state.current_item = item
            self.state.current_zone = zone

            if zone == expected_zone:
                self.state.robot_status = "标签正常"
                self.state.last_message = f"标签 {tag_id} 识别正常：{item} 位于 {zone}。"
                self._add_event(tag_id, item, zone, "正常", "物品与分区匹配。")
            else:
                self.state.task_status = "等待人工确认"
                self.state.robot_status = "异常告警"
                self.state.last_message = (
                    f"标签 {tag_id} 异常：{item} 当前在 {zone}，期望分区为 {expected_zone}。"
                )
                self._add_event(tag_id, item, zone, "待确认", f"错放，期望分区为 {expected_zone}。")

    def confirm(self, event_id: str | None = None) -> bool:
        with self.lock:
            target = None
            for event in self.state.events:
                if event["status"] == "待确认" and (event_id is None or event["id"] == event_id):
                    target = event
                    break

            if target is None:
                self.state.last_message = "当前没有待确认异常。"
                return False

            target["status"] = "已确认"
            target["message"] = "人工已完成回收确认。"
            self.state.task_status = "巡检中"
            self.state.robot_status = "异常已关闭"
            self.state.last_message = f"异常事件 {target['id']} 已人工确认。"
            return True

    def _add_event(self, tag_id: str, item: str, zone: str, status: str, message: str) -> None:
        event = {
            "id": uuid4().hex[:8],
            "time": datetime.now().strftime("%H:%M:%S"),
            "tag_id": tag_id,
            "item": item,
            "zone": zone,
            "status": status,
            "message": message,
        }
        self.state.events.insert(0, event)
        self.state.events = self.state.events[:20]
