from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias, TypedDict


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True, slots=True)
class ConfigError(ValueError):
    message: str

    def __str__(self) -> str:
        return self.message


class TagInfo(TypedDict, total=False):
    name: str
    kind: str
    item_id: str
    shelf_id: str
    expected_shelf: str
    marker_family: str
    ocr_label: str
    expected_color: str
    expected_ocr: str
    expected_image_class: str
    priority: int
    zone: str
    expected_zone: str


TagMap = dict[str, TagInfo]
ShelfManifest = dict[str, "ShelfManifestEntry"]


class ShelfManifestEntry(TypedDict):
    expected_items: list[str]


class ShelfPoint(TypedDict):
    scan_pose: list[int | str]
    safe_side: str


class WarehouseMap(TypedDict):
    grid_size: list[int]
    start: list[int]
    start_heading: str
    home: list[int]
    forbidden_cells: list[list[int]]
    shelf_points: dict[str, ShelfPoint]
