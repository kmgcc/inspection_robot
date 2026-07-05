from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from .config_defaults import DEFAULT_SHELF_MANIFEST, DEFAULT_TAG_MAP, DEFAULT_WAREHOUSE_MAP
from .config_types import ConfigError, JsonValue, ShelfManifest, ShelfPoint, TagInfo, TagMap, WarehouseMap


VALID_KINDS: Final = {"item", "shelf", "zone", "pose"}
VALID_DIRECTIONS: Final = {"N", "E", "S", "W"}
DEFAULT_PRIORITY: Final = 1
DEFAULT_MARKER_FAMILY: Final = "TAG36H11"


def load_tag_map(root: Path) -> TagMap:
    path = root / "config" / "tag_map.json"
    if not path.exists():
        return _copy_tag_map(DEFAULT_TAG_MAP)
    return validate_tag_map(_read_json(path))


def load_warehouse_map(root: Path) -> WarehouseMap:
    path = root / "config" / "warehouse_map.json"
    if not path.exists():
        return _copy_warehouse_map(DEFAULT_WAREHOUSE_MAP)
    return validate_warehouse_map(_read_json(path))


def load_shelf_manifest(root: Path) -> ShelfManifest:
    path = root / "config" / "shelf_manifest.json"
    if not path.exists():
        return _copy_shelf_manifest(DEFAULT_SHELF_MANIFEST)
    return validate_shelf_manifest(_read_json(path))


def validate_tag_map(data: JsonValue) -> TagMap:
    if not isinstance(data, dict):
        raise ConfigError("tag_map.json must contain a JSON object keyed by tag id")
    tag_map: TagMap = {}
    for tag_id, raw_info in data.items():
        tag_key = str(tag_id).strip()
        if not tag_key:
            raise ConfigError("tag id must not be empty")
        if not isinstance(raw_info, dict):
            raise ConfigError(f"tag {tag_key} must be an object")
        tag_map[tag_key] = _normalize_tag(tag_key, raw_info)
    if not tag_map:
        raise ConfigError("tag_map.json must define at least one tag")
    return tag_map


def validate_warehouse_map(data: JsonValue) -> WarehouseMap:
    if not isinstance(data, dict):
        raise ConfigError("warehouse_map.json must contain an object")
    grid_size = _required_int_pair(data, "warehouse_map", "grid_size")
    if grid_size[0] <= 0 or grid_size[1] <= 0:
        raise ConfigError("warehouse_map grid_size must contain positive integers")
    start = _required_cell(data, "warehouse_map", "start", grid_size)
    start_heading = _direction(data.get("start_heading", "E"), "warehouse_map start_heading")
    home = _required_cell(data, "warehouse_map", "home", grid_size)
    forbidden_cells = _cell_list(data.get("forbidden_cells", []), "forbidden_cells", grid_size)
    shelf_points = _shelf_points(data.get("shelf_points"), grid_size)
    return {
        "grid_size": grid_size,
        "start": start,
        "start_heading": start_heading,
        "home": home,
        "forbidden_cells": forbidden_cells,
        "shelf_points": shelf_points,
    }


def validate_shelf_manifest(data: JsonValue) -> ShelfManifest:
    if not isinstance(data, dict):
        raise ConfigError("shelf_manifest.json must contain an object")
    manifest: ShelfManifest = {}
    for shelf_id, raw_entry in data.items():
        shelf_key = str(shelf_id).strip()
        if not shelf_key:
            raise ConfigError("shelf id must not be empty")
        if not isinstance(raw_entry, dict):
            raise ConfigError(f"shelf {shelf_key} must be an object")
        raw_items = raw_entry.get("expected_items")
        if not isinstance(raw_items, list):
            raise ConfigError(f"shelf {shelf_key} expected_items must be a list")
        items = [str(item).strip() for item in raw_items if str(item).strip()]
        manifest[shelf_key] = {"expected_items": items}
    if not manifest:
        raise ConfigError("shelf_manifest.json must define at least one shelf")
    return manifest


def _normalize_tag(tag_id: str, info: dict[str, JsonValue]) -> TagInfo:
    kind = _kind(info.get("kind", "item"), tag_id)
    numeric_id = _tag_number(tag_id)
    _validate_id_range(numeric_id, kind)
    normalized: TagInfo = {
        "name": _required_text(info, tag_id, "name"),
        "kind": kind,
        "priority": _priority(info.get("priority", DEFAULT_PRIORITY), tag_id),
        "marker_family": _optional_text(info, "marker_family") or DEFAULT_MARKER_FAMILY,
        "zone": _optional_text(info, "zone") or "-",
        "expected_zone": _optional_text(info, "expected_zone") or "-",
    }
    if kind == "item":
        item_id = _optional_text(info, "item_id")
        expected_shelf = _optional_text(info, "expected_shelf")
        if item_id is None and _looks_like_legacy_item(info):
            item_id = f"item_{numeric_id:02d}"
        if expected_shelf is None and _looks_like_legacy_item(info):
            expected_shelf = _legacy_shelf_from_zone(str(info["expected_zone"]))
        if item_id is None:
            raise ConfigError(f"tag {tag_id} item_id is required for item tags")
        normalized["item_id"] = item_id
        if expected_shelf is not None:
            normalized["expected_shelf"] = expected_shelf
        _copy_optional(info, normalized, "expected_color")
        _copy_optional(info, normalized, "expected_ocr")
        _copy_optional(info, normalized, "expected_image_class")
    elif kind == "shelf":
        shelf_id = _optional_text(info, "shelf_id") or normalized["name"]
        normalized["shelf_id"] = shelf_id
        normalized["ocr_label"] = _optional_text(info, "ocr_label") or shelf_id
        normalized["expected_zone"] = shelf_id
    return normalized


def _read_json(path: Path) -> JsonValue:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _required_text(info: dict[str, JsonValue], tag_id: str, field: str) -> str:
    if field not in info:
        raise ConfigError(f"tag {tag_id} is missing required field: {field}")
    value = info[field]
    if value is None:
        raise ConfigError(f"tag {tag_id} field {field} must not be null")
    text = str(value).strip()
    if not text:
        raise ConfigError(f"tag {tag_id} field {field} must not be empty")
    return text


def _optional_text(info: dict[str, JsonValue], field: str) -> str | None:
    value = info.get(field)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _priority(value: JsonValue, tag_id: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"tag {tag_id} priority must be a positive integer")
    try:
        priority = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"tag {tag_id} priority must be a positive integer") from exc
    if priority < 1:
        raise ConfigError(f"tag {tag_id} priority must be a positive integer")
    return priority


def _kind(value: JsonValue, tag_id: str) -> str:
    kind = str(value).strip().lower()
    if kind not in VALID_KINDS:
        allowed = ", ".join(sorted(VALID_KINDS))
        raise ConfigError(f"tag {tag_id} kind must be one of: {allowed}")
    return kind


def _tag_number(tag_id: str) -> int:
    try:
        return int(tag_id)
    except ValueError as exc:
        raise ConfigError(f"tag {tag_id} must be a numeric AprilTag id") from exc


def _validate_id_range(tag_id: int, kind: str) -> None:
    if kind == "item" and not 1 <= tag_id <= 50:
        raise ConfigError(f"item tag id {tag_id} must be in range 1-50")
    if kind == "shelf" and not 101 <= tag_id <= 120:
        raise ConfigError(f"shelf tag id {tag_id} must be in range 101-120")
    if kind == "pose" and not 201 <= tag_id <= 220:
        raise ConfigError(f"pose tag id {tag_id} must be in range 201-220")
    if kind == "zone" and not 301 <= tag_id <= 320:
        raise ConfigError(f"zone tag id {tag_id} must be in range 301-320")


def _looks_like_legacy_item(info: dict[str, JsonValue]) -> bool:
    return "zone" in info and "expected_zone" in info and "item_id" not in info and "expected_shelf" not in info


def _legacy_shelf_from_zone(zone: str) -> str:
    zone_prefix = zone.strip()[:1].upper()
    if zone_prefix == "A":
        return "A1"
    if zone_prefix == "B":
        return "B1"
    return "A1"


def _copy_optional(source: dict[str, JsonValue], target: TagInfo, field: str) -> None:
    value = _optional_text(source, field)
    if value is not None:
        target[field] = value


def _required_int_pair(data: dict[str, JsonValue], owner: str, field: str) -> list[int]:
    value = data.get(field)
    if not isinstance(value, list) or len(value) != 2:
        raise ConfigError(f"{owner} {field} must contain two integers")
    if any(isinstance(part, bool) or not isinstance(part, int) for part in value):
        raise ConfigError(f"{owner} {field} must contain two integers")
    return [int(value[0]), int(value[1])]


def _required_cell(data: dict[str, JsonValue], owner: str, field: str, grid_size: list[int]) -> list[int]:
    cell = _required_int_pair(data, owner, field)
    _ensure_in_bounds(cell, field, grid_size)
    return cell


def _cell_list(value: JsonValue, field: str, grid_size: list[int]) -> list[list[int]]:
    if not isinstance(value, list):
        raise ConfigError(f"{field} must be a list of cells")
    cells: list[list[int]] = []
    for index, raw_cell in enumerate(value):
        if not isinstance(raw_cell, list) or len(raw_cell) != 2:
            raise ConfigError(f"{field}[{index}] must contain two integers")
        cells.append(_required_cell({"cell": raw_cell}, field, "cell", grid_size))
    return cells


def _shelf_points(value: JsonValue, grid_size: list[int]) -> dict[str, ShelfPoint]:
    if not isinstance(value, dict):
        raise ConfigError("warehouse_map shelf_points must be an object")
    points: dict[str, ShelfPoint] = {}
    for shelf_id, raw_point in value.items():
        shelf_key = str(shelf_id).strip()
        if not shelf_key:
            raise ConfigError("shelf point id must not be empty")
        if not isinstance(raw_point, dict):
            raise ConfigError(f"shelf point {shelf_key} must be an object")
        raw_pose = raw_point.get("scan_pose")
        if not isinstance(raw_pose, list) or len(raw_pose) != 3:
            raise ConfigError(f"shelf point {shelf_key} scan_pose must be [x, y, heading]")
        cell = _required_cell({"scan_pose": raw_pose[:2]}, shelf_key, "scan_pose", grid_size)
        heading = _direction(raw_pose[2], f"shelf point {shelf_key} heading")
        safe_side = _direction(raw_point.get("safe_side", ""), f"shelf point {shelf_key} safe_side")
        points[shelf_key] = {"scan_pose": [cell[0], cell[1], heading], "safe_side": safe_side}
    if not points:
        raise ConfigError("warehouse_map shelf_points must define at least one shelf")
    return points


def _ensure_in_bounds(cell: list[int], field: str, grid_size: list[int]) -> None:
    if not 0 <= cell[0] < grid_size[0] or not 0 <= cell[1] < grid_size[1]:
        raise ConfigError(f"{field} cell {cell} is outside grid_size {grid_size}")


def _direction(value: JsonValue, field: str) -> str:
    direction = str(value).strip().upper()
    if direction not in VALID_DIRECTIONS:
        raise ConfigError(f"{field} must be one of N/E/S/W")
    return direction


def _copy_tag_map(tag_map: TagMap) -> TagMap:
    return {tag_id: info.copy() for tag_id, info in tag_map.items()}


def _copy_warehouse_map(warehouse_map: WarehouseMap) -> WarehouseMap:
    return {
        "grid_size": list(warehouse_map["grid_size"]),
        "start": list(warehouse_map["start"]),
        "start_heading": warehouse_map["start_heading"],
        "home": list(warehouse_map["home"]),
        "forbidden_cells": [list(cell) for cell in warehouse_map["forbidden_cells"]],
        "shelf_points": {
            shelf_id: {"scan_pose": list(point["scan_pose"]), "safe_side": point["safe_side"]}
            for shelf_id, point in warehouse_map["shelf_points"].items()
        },
    }


def _copy_shelf_manifest(manifest: ShelfManifest) -> ShelfManifest:
    return {shelf_id: {"expected_items": list(entry["expected_items"])} for shelf_id, entry in manifest.items()}
