from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TypeAlias

from ..config_types import ShelfManifest, WarehouseMap
from .events import EventRecord, JsonValue


ObstacleState: TypeAlias = dict[str, int | bool | None]
AlarmState: TypeAlias = dict[str, str]
BoundaryState: TypeAlias = dict[str, JsonValue]
AudioState: TypeAlias = dict[str, JsonValue]
GimbalState: TypeAlias = dict[str, JsonValue]
MotionSensorState: TypeAlias = dict[str, JsonValue]
TopologyState: TypeAlias = dict[str, JsonValue]
ZoneState: TypeAlias = dict[str, JsonValue]
PoseState: TypeAlias = dict[str, int | str]
PathState: TypeAlias = dict[str, JsonValue]
ShelfState: TypeAlias = dict[str, JsonValue]
ForbiddenZoneState: TypeAlias = dict[str, JsonValue]
ScanState: TypeAlias = dict[str, JsonValue]
StatusSnapshot: TypeAlias = dict[str, JsonValue | list[EventRecord]]


def default_obstacle() -> ObstacleState:
    return {"distance_mm": None, "blocked": False, "waiting_seconds": 0}


def default_alarm() -> AlarmState:
    return {"level": "normal", "message": "正常", "light": "green"}


def default_boundary() -> BoundaryState:
    return {"tape_state": None, "full_black": False, "kind": "none"}


def default_audio() -> AudioState:
    return {"last_cue": None, "last_message": None, "last_error": None}


def default_gimbal() -> GimbalState:
    return {"side_initialized": False, "yaw": None, "pitch": None}


def default_motion_sensor() -> MotionSensorState:
    return {
        "ok": False,
        "source": "mpu6050",
        "accel_mps2": {"x": None, "y": None, "z": None},
        "gyro_dps": {"x": None, "y": None, "z": None},
        "gyro_bias_dps": {"x": None, "y": None, "z": None},
        "orientation_deg": {"roll": None, "pitch": None, "yaw": None},
        "zero_drift_compensated": False,
        "sample_time": None,
        "last_turn": None,
        "last_error": None,
    }


def default_topology() -> TopologyState:
    return {"status": "empty", "nodes": [], "edges": [], "current_node": None}


def default_path() -> PathState:
    return {"status": "idle", "waypoints": [], "next_index": 0}


def default_scan() -> ScanState:
    return {"active": False, "shelf_id": None, "detected_items": [], "frame_id": None, "detections": []}


@dataclass(slots=True)
class DashboardState:
    run_id: str = "local-001"
    run_mode: str = "simulate"
    hardware_connected: bool = False
    task_status: str = "IDLE"
    robot_status: str = "待命"
    current_tag: str | None = None
    current_item: str | None = None
    current_zone: str | None = None
    current_shelf: str | None = None
    current_target: str | None = None
    patrol_cycle: int = 1
    skip_shortage_detection: bool = True
    pose: PoseState | None = None
    path: PathState = field(default_factory=default_path)
    forbidden_zones: list[ForbiddenZoneState] = field(default_factory=list)
    shelves: list[ShelfState] = field(default_factory=list)
    scan: ScanState = field(default_factory=default_scan)
    boundary: BoundaryState = field(default_factory=default_boundary)
    audio: AudioState = field(default_factory=default_audio)
    gimbal: GimbalState = field(default_factory=default_gimbal)
    motion_sensor: MotionSensorState = field(default_factory=default_motion_sensor)
    topology: TopologyState = field(default_factory=default_topology)
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
    return [{"shelf_id": shelf_id, "status": "pending", "anomaly_count": 0, "items": []} for shelf_id in manifest]


def copy_json_dict(data: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    return dict(data)
