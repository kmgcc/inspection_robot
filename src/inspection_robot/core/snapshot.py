from __future__ import annotations

from .status import DashboardState, StatusSnapshot, copy_json_dict


def build_status_snapshot(state: DashboardState) -> StatusSnapshot:
    return {
        "run_id": state.run_id,
        "run_mode": state.run_mode,
        "hardware_connected": state.hardware_connected,
        "task_status": state.task_status,
        "robot_status": state.robot_status,
        "current_zone": state.current_zone,
        "current_tag": state.current_tag,
        "current_item": state.current_item,
        "current_shelf": state.current_shelf,
        "current_target": state.current_target,
        "patrol_cycle": state.patrol_cycle,
        "skip_shortage_detection": state.skip_shortage_detection,
        "pose": state.pose.copy() if state.pose is not None else None,
        "path": copy_json_dict(state.path),
        "forbidden_zones": [copy_json_dict(zone) for zone in state.forbidden_zones],
        "shelves": [copy_json_dict(shelf) for shelf in state.shelves],
        "scan": copy_json_dict(state.scan),
        "boundary": copy_json_dict(state.boundary),
        "audio": copy_json_dict(state.audio),
        "gimbal": copy_json_dict(state.gimbal),
        "topology": copy_json_dict(state.topology),
        "llm_summary": state.llm_summary,
        "last_message": state.last_message,
        "obstacle": {
            "distance_mm": state.obstacle["distance_mm"],
            "blocked": state.obstacle["blocked"],
            "waiting_seconds": state.obstacle.get("waiting_seconds", 0),
        },
        "alarm": copy_json_dict(state.alarm),
        "zones": [zone.copy() for zone in state.zones],
        "events": [event.copy() for event in state.events],
    }
