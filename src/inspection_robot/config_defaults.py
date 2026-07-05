from __future__ import annotations

from .config_types import ShelfManifest, TagMap, WarehouseMap


DEFAULT_TAG_MAP: TagMap = {
    "46": {"name": "手机", "kind": "item", "item_id": "item_46", "marker_family": "TAG36H11", "expected_color": "RED", "priority": 2, "zone": "高值", "expected_zone": "-"},
    "9": {"name": "耳机", "kind": "item", "item_id": "item_09", "marker_family": "TAG36H11", "expected_color": "RED", "priority": 2, "zone": "高值", "expected_zone": "-"},
    "44": {"name": "毛绒玩具", "kind": "item", "item_id": "item_44", "marker_family": "TAG36H11", "expected_color": "BLUE", "priority": 1, "zone": "常规", "expected_zone": "-"},
    "8": {"name": "衣服", "kind": "item", "item_id": "item_08", "marker_family": "TAG36H11", "expected_color": "BLUE", "priority": 1, "zone": "常规", "expected_zone": "-"},
    "7": {"name": "书本", "kind": "item", "item_id": "item_07", "marker_family": "TAG36H11", "expected_color": "BLUE", "priority": 1, "zone": "常规", "expected_zone": "-"},
    "19": {"name": "薯片", "kind": "item", "item_id": "item_19", "marker_family": "TAG36H11", "expected_color": "YELLOW", "priority": 1, "zone": "易耗", "expected_zone": "-"},
    "14": {"name": "药盒", "kind": "item", "item_id": "item_14", "marker_family": "TAG36H11", "expected_color": "YELLOW", "priority": 1, "zone": "易耗", "expected_zone": "-"},
    "47": {"name": "钥匙", "kind": "item", "item_id": "item_47", "marker_family": "TAG36H11", "expected_color": "RED", "priority": 2, "zone": "高值", "expected_zone": "-"},
    "20": {"name": "水杯", "kind": "item", "item_id": "item_20", "marker_family": "TAG36H11", "expected_color": "GREEN", "priority": 1, "zone": "低值", "expected_zone": "-"},
    "21": {"name": "工具", "kind": "item", "item_id": "item_21", "marker_family": "TAG36H11", "expected_color": "GREEN", "priority": 1, "zone": "低值", "expected_zone": "-"},
    "118": {"name": "A1", "kind": "shelf", "shelf_id": "A1", "marker_family": "TAG36H11", "ocr_label": "A1", "priority": 1, "zone": "A列", "expected_zone": "A1"},
    "110": {"name": "A2", "kind": "shelf", "shelf_id": "A2", "marker_family": "TAG36H11", "ocr_label": "A2", "priority": 1, "zone": "A列", "expected_zone": "A2"},
    "102": {"name": "A3", "kind": "shelf", "shelf_id": "A3", "marker_family": "TAG36H11", "ocr_label": "A3", "priority": 1, "zone": "A列", "expected_zone": "A3"},
    "107": {"name": "A4", "kind": "shelf", "shelf_id": "A4", "marker_family": "TAG36H11", "ocr_label": "A4", "priority": 1, "zone": "A列", "expected_zone": "A4"},
    "106": {"name": "B3", "kind": "shelf", "shelf_id": "B3", "marker_family": "TAG36H11", "ocr_label": "B3", "priority": 1, "zone": "B列", "expected_zone": "B3"},
    "108": {"name": "B2", "kind": "shelf", "shelf_id": "B2", "marker_family": "TAG36H11", "ocr_label": "B2", "priority": 1, "zone": "B列", "expected_zone": "B2"},
    "101": {"name": "B1", "kind": "shelf", "shelf_id": "B1", "marker_family": "TAG36H11", "ocr_label": "B1", "priority": 1, "zone": "B列", "expected_zone": "B1"},
}

DEFAULT_WAREHOUSE_MAP: WarehouseMap = {
    "grid_size": [8, 6],
    "start": [0, 0],
    "start_heading": "E",
    "home": [0, 0],
    "forbidden_cells": [[2, 2], [2, 3], [4, 3]],
    "shelf_points": {
        "A1": {"scan_pose": [1, 1, "E"], "safe_side": "W"},
        "A2": {"scan_pose": [3, 1, "E"], "safe_side": "W"},
        "A3": {"scan_pose": [5, 1, "E"], "safe_side": "W"},
        "A4": {"scan_pose": [7, 1, "E"], "safe_side": "W"},
        "B3": {"scan_pose": [5, 4, "W"], "safe_side": "E"},
        "B2": {"scan_pose": [3, 4, "W"], "safe_side": "E"},
        "B1": {"scan_pose": [1, 4, "W"], "safe_side": "E"},
    },
}

DEFAULT_SHELF_MANIFEST: ShelfManifest = {
    "A1": {"expected_items": []},
    "A2": {"expected_items": []},
    "A3": {"expected_items": []},
    "A4": {"expected_items": []},
    "B3": {"expected_items": []},
    "B2": {"expected_items": []},
    "B1": {"expected_items": []},
}
