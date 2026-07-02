from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_TAG_MAP: dict[str, dict[str, str]] = {
    "1": {"name": "Apple", "zone": "A区", "expected_zone": "A区"},
    "2": {"name": "Medicine Box", "zone": "B区", "expected_zone": "B区"},
    "3": {"name": "Book", "zone": "C区", "expected_zone": "C区"},
    "4": {"name": "Bottle", "zone": "A区", "expected_zone": "B区"},
}


def load_tag_map(root: Path) -> dict[str, dict[str, str]]:
    path = root / "config" / "tag_map.json"
    if not path.exists():
        return DEFAULT_TAG_MAP

    with path.open("r", encoding="utf-8") as file:
        data: Any = json.load(file)

    tag_map: dict[str, dict[str, str]] = {}
    for tag_id, info in data.items():
        tag_map[str(tag_id)] = {
            "name": str(info["name"]),
            "zone": str(info["zone"]),
            "expected_zone": str(info["expected_zone"]),
        }
    return tag_map
