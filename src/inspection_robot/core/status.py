from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TypeAlias

from ..config_types import ShelfManifest, WarehouseMap
from .events import EventRecord, JsonValue


ObstacleState: TypeAlias = dict[str, int | bool | None]
AlarmState: TypeAlias = dict[str, str]
ZoneState: TypeAlias = dict[str, JsonValue]
PoseState: TypeAlias = dict[str, int | str]
PathState: TypeAlias = dict[str, JsonValue]
ShelfState: TypeAlias = dict[str, JsonValue]
ForbiddenZoneState: TypeAlias = dict[str, JsonValue]
ScanState: TypeAlias = dict[str, JsonValue]
StatusSnapshot: TypeAlias = dict[str, JsonValue | list[EventRecord]]


def default_obstacle() -> ObstacleState:
    return {"distance_mm": None, "blocked": False}


def default_alarm() -> AlarmState:
    return {"level": "normal", "message": "正常"}


def default_path() -> PathState:
    return {"status": "idle", "waypoints": [], "next_index": 0}


def default_scan() -> ScanState:
    return {"active": False, "shelf_id": None, "detected_items": [], "frame_id": None, "detections": []}


@dataclass(slots=True)
class DashboardState:
    run_id: str = "local-001"
    task_status: str = "IDLE"
    robot_status: str = "待命"
    current_tag: str | None = None
    current_item: str | None = None
    current_zone: str | None = None
    current_shelf: str | None = None
    current_target: str | None = None
    pose: PoseState | None = None
    path: PathState = field(default_factory=default_path)
    forbidden_zones: list[ForbiddenZoneState] = field(default_factory=list)
    shelves: list[ShelfState] = field(default_factory=list)
    scan: ScanState = field(default_factory=default_scan)
    llm_summary: str | None = None
    last_message: str = "系统已启动，等待开始巡检。"
    obstacle: ObstacleState = field(default_factory=default_obstacle)
    alarm: AlarmState = field(default_factory=default_alarm)
    zones: list[ZoneState] = field(default_factory=list)
    events: list[EventRecord] = field(default_factory=list)


def new_dashboard_state(warehouse_map: WarehouseMap, shelf_manifest: ShelfManifest, message: str | None = None) -> DashboardState:
    state = DashboardState()
    if message is not None:
        state.last_message = message
    state.shelves = initial_shelves(shelf_manifest)
    state.forbidden_zones = [{"id": "map", "cells": [list(cell) for cell in warehouse_map["forbidden_cells"]], "blocked": False}]
    return state


def initial_shelves(manifest: ShelfManifest) -> list[ShelfState]:
    return [{"shelf_id": shelf_id, "status": "pending", "anomaly_count": 0} for shelf_id in manifest]


def copy_json_dict(data: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    return dict(data)
