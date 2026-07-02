from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from io import StringIO
from threading import Lock
from uuid import uuid4


JsonValue = str | int | bool | None
ObstacleState = dict[str, int | bool | None]
AlarmState = dict[str, str]
ZoneState = dict[str, JsonValue]
EventRecord = dict[str, JsonValue]
StatusSnapshot = dict[str, JsonValue | ObstacleState | AlarmState | list[ZoneState] | list[EventRecord]]


@dataclass(frozen=True, slots=True)
class EventInput:
    event_type: str
    tag_id: str | None
    item: str
    zone: str
    expected_zone: str | None
    priority: int
    status: str
    message: str


def default_obstacle() -> ObstacleState:
    return {"distance_mm": None, "blocked": False}


def default_alarm() -> AlarmState:
    return {"level": "normal", "message": "正常"}


@dataclass(slots=True)
class DashboardState:
    run_id: str = "local-001"
    task_status: str = "IDLE"
    robot_status: str = "待命"
    current_tag: str | None = None
    current_item: str | None = None
    current_zone: str | None = None
    last_message: str = "系统已启动，等待开始巡检。"
    obstacle: ObstacleState = field(default_factory=default_obstacle)
    alarm: AlarmState = field(default_factory=default_alarm)
    zones: list[ZoneState] = field(default_factory=list)
    events: list[EventRecord] = field(default_factory=list)


class InspectionStore:
    def __init__(self, tag_map: dict[str, dict[str, str]]) -> None:
        self.tag_map = tag_map
        self.state = DashboardState()
        self.lock = Lock()

    def snapshot(self) -> StatusSnapshot:
        with self.lock:
            return {
                "run_id": self.state.run_id,
                "task_status": self.state.task_status,
                "robot_status": self.state.robot_status,
                "current_zone": self.state.current_zone,
                "current_tag": self.state.current_tag,
                "current_item": self.state.current_item,
                "last_message": self.state.last_message,
                "obstacle": {
                    "distance_mm": self.state.obstacle["distance_mm"],
                    "blocked": self.state.obstacle["blocked"],
                },
                "alarm": {
                    "level": self.state.alarm["level"],
                    "message": self.state.alarm["message"],
                },
                "zones": [zone.copy() for zone in self.state.zones],
                "events": [event.copy() for event in self.state.events],
            }

    def export_events_csv(self) -> str:
        with self.lock:
            events = list(reversed(self.state.events))

        output = StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["事件ID", "时间", "类型", "标签ID", "物品", "当前分区", "期望分区", "优先级", "状态", "说明"])
        for event in events:
            writer.writerow(
                [
                    event["id"],
                    event["time"],
                    event["type"],
                    event["tag_id"],
                    event["item"],
                    event["zone"],
                    event["expected_zone"],
                    event["priority"],
                    event["status"],
                    event["message"],
                ]
            )
        return output.getvalue()

    def start(self) -> None:
        with self.lock:
            self.state.task_status = "PATROL"
            self.state.robot_status = "等待标签"
            self.state.last_message = "巡检任务已开始。当前版本使用模拟标签，下一步接入真实 AprilTag。"

    def stop(self) -> None:
        with self.lock:
            self.state.task_status = "STOPPED"
            self.state.robot_status = "停车"
            self.state.last_message = "巡检任务已停止。"

    def reset(self) -> None:
        with self.lock:
            self.state = DashboardState(last_message="系统已重置。")

    def handle_tag(self, tag_id: str) -> None:
        self.record_tag(tag_id)

    def record_tag(
        self,
        tag_id: str,
        observed_zone: str | None = None,
        source: str = "simulate",
    ) -> None:
        with self.lock:
            info = self.tag_map.get(tag_id)
            if info is None:
                zone = observed_zone or "-"
                self.state.task_status = "WAIT_CONFIRM"
                self.state.robot_status = "识别异常"
                self.state.last_message = f"识别到未知标签 {tag_id}。"
                self.state.alarm = {"level": "warning", "message": "未知标签"}
                self._add_event(
                    EventInput(
                        event_type="unknown_tag",
                        tag_id=tag_id,
                        item="Unknown",
                        zone=zone,
                        expected_zone=None,
                        priority=2,
                        status="waiting_confirm",
                        message=f"未知标签，需要人工核查。来源：{source}。",
                    )
                )
                return

            item = info["name"]
            zone = observed_zone or info["zone"]
            expected_zone = info["expected_zone"]
            priority = int(info.get("priority", 1))
            self.state.current_tag = tag_id
            self.state.current_item = item
            self.state.current_zone = zone

            if zone == expected_zone:
                self.state.task_status = "NORMAL_LOGGED"
                self.state.robot_status = "标签正常"
                self.state.last_message = f"标签 {tag_id} 识别正常：{item} 位于 {zone}。"
                self.state.alarm = {"level": "normal", "message": "正常"}
                self._add_event(
                    EventInput(
                        event_type="normal_tag",
                        tag_id=tag_id,
                        item=item,
                        zone=zone,
                        expected_zone=expected_zone,
                        priority=priority,
                        status="normal",
                        message=f"物品与分区匹配。来源：{source}。",
                    )
                )
            else:
                self.state.task_status = "WAIT_CONFIRM"
                self.state.robot_status = "异常告警"
                self.state.last_message = (
                    f"标签 {tag_id} 异常：{item} 当前在 {zone}，期望分区为 {expected_zone}。"
                )
                self.state.alarm = {"level": "warning", "message": "错放待确认"}
                self._add_event(
                    EventInput(
                        event_type="wrong_zone",
                        tag_id=tag_id,
                        item=item,
                        zone=zone,
                        expected_zone=expected_zone,
                        priority=max(priority, 2),
                        status="waiting_confirm",
                        message=f"错放，期望分区为 {expected_zone}。来源：{source}。",
                    )
                )

    def record_obstacle(self, distance_mm: int | None, blocked: bool) -> None:
        with self.lock:
            self.state.obstacle = {"distance_mm": distance_mm, "blocked": blocked}
            zone = self.state.current_zone or "-"
            if blocked:
                self.state.task_status = "OBSTACLE_WAIT"
                self.state.robot_status = "障碍等待"
                self.state.last_message = "检测到障碍，小车停车等待。"
                self.state.alarm = {"level": "warning", "message": "障碍等待"}
                event_type = "obstacle_wait"
                message = "检测到障碍，小车停车等待。"
            else:
                self.state.task_status = "PATROL"
                self.state.robot_status = "巡检中"
                self.state.last_message = "障碍已解除，恢复巡检。"
                self.state.alarm = {"level": "normal", "message": "正常"}
                event_type = "obstacle_clear"
                message = "障碍已解除，恢复巡检。"

            self._add_event(
                EventInput(
                    event_type=event_type,
                    tag_id=None,
                    item="-",
                    zone=zone,
                    expected_zone=None,
                    priority=1,
                    status="info",
                    message=message,
                )
            )

    def record_robot_status(self, status: str, message: str | None = None) -> None:
        with self.lock:
            self.state.task_status = status
            self.state.robot_status = status
            if message is not None:
                self.state.last_message = message

    def confirm(self, event_id: str | None = None) -> bool:
        with self.lock:
            target = None
            for event in self.state.events:
                if event["status"] == "waiting_confirm" and (event_id is None or event["id"] == event_id):
                    target = event
                    break

            if target is None:
                self.state.last_message = "当前没有待确认异常。"
                return False

            target["status"] = "confirmed"
            target["message"] = "人工已完成回收确认。"
            self.state.task_status = "PATROL"
            self.state.robot_status = "异常已关闭"
            self.state.last_message = f"异常事件 {target['id']} 已人工确认。"
            self.state.alarm = {"level": "normal", "message": "正常"}
            return True

    def _add_event(self, event_input: EventInput) -> None:
        event: EventRecord = {
            "id": uuid4().hex[:8],
            "time": datetime.now().isoformat(timespec="seconds"),
            "type": event_input.event_type,
            "tag_id": event_input.tag_id,
            "item": event_input.item,
            "zone": event_input.zone,
            "expected_zone": event_input.expected_zone,
            "priority": event_input.priority,
            "status": event_input.status,
            "message": event_input.message,
        }
        self.state.events.insert(0, event)
        self.state.events = self.state.events[:20]
