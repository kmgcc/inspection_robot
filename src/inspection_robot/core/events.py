from __future__ import annotations

from datetime import datetime
from typing import TypeAlias, TypedDict
from uuid import uuid4


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

EVENT_TYPES = {
    "runtime_started",
    "runtime_stopped",
    "manual_control",
    "motion_debug",
    "gimbal_initialized",
    "shelf_detected",
    "item_detected",
    "first_pass_observed",
    "cycle_started",
    "cycle_completed",
    "boundary_full_black",
    "boundary_turn",
    "unexpected_boundary",
    "obstacle_avoidance_started",
    "obstacle_avoidance_step",
    "obstacle_avoidance_nested",
    "audio_cue",
    "light_cue",
    "path_planned",
    "path_step",
    "path_replanned",
    "forbidden_zone_detected",
    "obstacle_wait",
    "obstacle_clear",
    "shelf_arrived",
    "shelf_aligned",
    "shelf_scanned",
    "scan_failed",
    "normal_item",
    "added_item",
    "unknown_item",
    "untagged_evidence",
    "wrong_shelf",
    "missing_item",
    "duplicate_item",
    "evidence_mismatch",
    "manual_confirm",
    "llm_summary",
    "system",
    "normal_tag",
    "unknown_tag",
    "wrong_zone",
    "missing_tag",
    "duplicate_tag",
}
EVENT_STATUSES = {"normal", "waiting_confirm", "confirmed", "info", "warning", "error"}
CSV_HEADER = [
    "事件ID",
    "时间",
    "类型",
    "标签ID",
    "物品",
    "区域",
    "货架",
    "期望货架",
    "颜色",
    "OCR",
    "图像类别",
    "优先级",
    "状态",
    "来源",
    "说明",
]


class EventRecord(TypedDict, total=False):
    id: str
    time: str
    type: str
    tag_id: str | None
    item: str
    zone: str
    expected_zone: str | None
    priority: int
    status: str
    message: str
    shelf_id: str | None
    expected_shelf: str | None
    target: str | None
    source: str
    frame_id: str | None
    marker_family: str | None
    ocr_text: str | None
    color: str | None
    image_class: str | None
    evidence: dict[str, JsonValue] | None


def make_event(
    event_type: str,
    *,
    tag_id: str | None = None,
    item: str = "-",
    zone: str = "-",
    expected_zone: str | None = None,
    priority: int = 1,
    status: str = "info",
    message: str = "",
    shelf_id: str | None = None,
    expected_shelf: str | None = None,
    target: str | None = None,
    source: str = "core",
    frame_id: str | None = None,
    marker_family: str | None = None,
    ocr_text: str | None = None,
    color: str | None = None,
    image_class: str | None = None,
    evidence: dict[str, JsonValue] | None = None,
    event_id: str | None = None,
    event_time: str | None = None,
) -> EventRecord:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unsupported event type: {event_type}")
    if status not in EVENT_STATUSES:
        raise ValueError(f"unsupported event status: {status}")
    if priority < 1:
        raise ValueError("event priority must be a positive integer")

    return {
        "id": event_id or f"evt-{uuid4().hex[:8]}",
        "time": event_time or datetime.now().isoformat(timespec="seconds"),
        "type": event_type,
        "tag_id": tag_id,
        "item": item,
        "zone": zone or "-",
        "expected_zone": expected_zone,
        "priority": int(priority),
        "status": status,
        "message": message,
        "shelf_id": shelf_id,
        "expected_shelf": expected_shelf,
        "target": target,
        "source": source,
        "frame_id": frame_id,
        "marker_family": marker_family,
        "ocr_text": ocr_text,
        "color": color,
        "image_class": image_class,
        "evidence": evidence,
    }
