from __future__ import annotations

from .config_types import ShelfManifest, TagMap, WarehouseMap


DEFAULT_TAG_MAP: TagMap = {
    "1": {"name": "Red Bottle", "kind": "item", "item_id": "item_01", "expected_shelf": "A1", "marker_family": "TAG36H11", "expected_color": "RED", "expected_ocr": "ITEM-01", "expected_image_class": "BOTTLE", "priority": 1, "zone": "A区", "expected_zone": "A1"},
    "2": {"name": "Blue Box", "kind": "item", "item_id": "item_02", "expected_shelf": "A1", "marker_family": "TAG36H11", "expected_color": "BLUE", "expected_ocr": "ITEM-02", "expected_image_class": "BOX", "priority": 1, "zone": "A区", "expected_zone": "A1"},
    "3": {"name": "Green Cube", "kind": "item", "item_id": "item_03", "expected_shelf": "A2", "marker_family": "TAG36H11", "expected_color": "GREEN", "expected_ocr": "ITEM-03", "expected_image_class": "CUBE", "priority": 1, "zone": "A区", "expected_zone": "A2"},
    "4": {"name": "Yellow Medicine Box", "kind": "item", "item_id": "item_04", "expected_shelf": "A3", "marker_family": "TAG36H11", "expected_color": "YELLOW", "expected_ocr": "ITEM-04", "expected_image_class": "BOX", "priority": 2, "zone": "A区", "expected_zone": "A3"},
    "5": {"name": "White Sensor", "kind": "item", "item_id": "item_05", "expected_shelf": "A4", "marker_family": "TAG36H11", "expected_ocr": "ITEM-05", "expected_image_class": "MODULE", "priority": 1, "zone": "A区", "expected_zone": "A4"},
    "6": {"name": "Black Cable", "kind": "item", "item_id": "item_06", "expected_shelf": "B1", "marker_family": "TAG36H11", "expected_color": "BLACK", "expected_ocr": "ITEM-06", "expected_image_class": "COIL", "priority": 1, "zone": "B区", "expected_zone": "B1"},
    "7": {"name": "Gray Tool", "kind": "item", "item_id": "item_07", "expected_shelf": "B2", "marker_family": "TAG36H11", "expected_color": "GRAY", "expected_ocr": "ITEM-07", "expected_image_class": "TOOL", "priority": 1, "zone": "B区", "expected_zone": "B2"},
    "8": {"name": "Orange Battery", "kind": "item", "item_id": "item_08", "expected_shelf": "B3", "marker_family": "TAG36H11", "expected_color": "ORANGE", "expected_ocr": "ITEM-08", "expected_image_class": "BATTERY", "priority": 2, "zone": "B区", "expected_zone": "B3"},
    "9": {"name": "Purple Label", "kind": "item", "item_id": "item_09", "expected_shelf": "B4", "marker_family": "TAG36H11", "expected_color": "PURPLE", "expected_ocr": "ITEM-09", "expected_image_class": "CARD", "priority": 1, "zone": "B区", "expected_zone": "B4"},
    "10": {"name": "Silver Screw", "kind": "item", "item_id": "item_10", "expected_shelf": "B4", "marker_family": "TAG36H11", "expected_color": "SILVER", "expected_ocr": "ITEM-10", "expected_image_class": "CYLINDER", "priority": 1, "zone": "B区", "expected_zone": "B4"},
    "101": {"name": "A1", "kind": "shelf", "shelf_id": "A1", "marker_family": "TAG36H11", "ocr_label": "A1", "priority": 1, "zone": "A区", "expected_zone": "A1"},
    "102": {"name": "A2", "kind": "shelf", "shelf_id": "A2", "marker_family": "TAG36H11", "ocr_label": "A2", "priority": 1, "zone": "A区", "expected_zone": "A2"},
    "103": {"name": "A3", "kind": "shelf", "shelf_id": "A3", "marker_family": "TAG36H11", "ocr_label": "A3", "priority": 1, "zone": "A区", "expected_zone": "A3"},
    "104": {"name": "A4", "kind": "shelf", "shelf_id": "A4", "marker_family": "TAG36H11", "ocr_label": "A4", "priority": 1, "zone": "A区", "expected_zone": "A4"},
    "105": {"name": "B1", "kind": "shelf", "shelf_id": "B1", "marker_family": "TAG36H11", "ocr_label": "B1", "priority": 1, "zone": "B区", "expected_zone": "B1"},
    "106": {"name": "B2", "kind": "shelf", "shelf_id": "B2", "marker_family": "TAG36H11", "ocr_label": "B2", "priority": 1, "zone": "B区", "expected_zone": "B2"},
    "107": {"name": "B3", "kind": "shelf", "shelf_id": "B3", "marker_family": "TAG36H11", "ocr_label": "B3", "priority": 1, "zone": "B区", "expected_zone": "B3"},
    "108": {"name": "B4", "kind": "shelf", "shelf_id": "B4", "marker_family": "TAG36H11", "ocr_label": "B4", "priority": 1, "zone": "B区", "expected_zone": "B4"},
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
        "B4": {"scan_pose": [7, 4, "W"], "safe_side": "E"},
        "B3": {"scan_pose": [5, 4, "W"], "safe_side": "E"},
        "B2": {"scan_pose": [3, 4, "W"], "safe_side": "E"},
        "B1": {"scan_pose": [1, 4, "W"], "safe_side": "E"},
    },
}

DEFAULT_SHELF_MANIFEST: ShelfManifest = {
    "A1": {"expected_items": ["item_01", "item_02"]},
    "A2": {"expected_items": ["item_03"]},
    "A3": {"expected_items": ["item_04"]},
    "A4": {"expected_items": ["item_05"]},
    "B4": {"expected_items": ["item_09", "item_10"]},
    "B3": {"expected_items": ["item_08"]},
    "B2": {"expected_items": ["item_07"]},
    "B1": {"expected_items": ["item_06"]},
}
