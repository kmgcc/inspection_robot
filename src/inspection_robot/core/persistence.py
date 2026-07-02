from __future__ import annotations

import csv
import json
from io import StringIO
from pathlib import Path

from .events import CSV_HEADER, EventRecord, JsonValue, make_event


def events_to_csv(events: list[EventRecord]) -> str:
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(CSV_HEADER)
    for event in reversed(events):
        writer.writerow(
            [
                event["id"],
                event["time"],
                event["type"],
                event["tag_id"],
                event["item"],
                event["zone"],
                event.get("shelf_id"),
                event.get("expected_shelf"),
                event.get("color"),
                event.get("ocr_text"),
                event.get("image_class"),
                event["priority"],
                event["status"],
                event.get("source", "core"),
                event["message"],
            ]
        )
    return output.getvalue()


def load_events(path: Path) -> list[EventRecord]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list):
        raise ValueError("events file must contain a list")
    return [_normalize_event(item) for item in data if isinstance(item, dict)]


def persist_events(path: Path, events: list[EventRecord]) -> Path:
    temp_path = path.parent / f"{path.name}.tmp"
    path.parent.mkdir(parents=True, exist_ok=True)
    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(events, file, ensure_ascii=False, indent=2)
        file.write("\n")
    temp_path.replace(path)
    return temp_path


def remove_temp(path: Path) -> None:
    if path.exists() and path.is_file():
        path.unlink()


def _normalize_event(raw: dict[str, JsonValue]) -> EventRecord:
    priority = raw.get("priority", 1)
    fallback = make_event("system")
    try:
        normalized_priority = int(priority)
    except (TypeError, ValueError):
        normalized_priority = 1
    return {
        "id": str(raw.get("id") or fallback["id"]),
        "time": str(raw.get("time") or fallback["time"]),
        "type": str(raw.get("type") or "system"),
        "tag_id": None if raw.get("tag_id") is None else str(raw.get("tag_id")),
        "item": str(raw.get("item") or "-"),
        "zone": str(raw.get("zone") or "-"),
        "expected_zone": None if raw.get("expected_zone") is None else str(raw.get("expected_zone")),
        "priority": max(normalized_priority, 1),
        "status": str(raw.get("status") or "info"),
        "message": str(raw.get("message") or ""),
        "shelf_id": None if raw.get("shelf_id") is None else str(raw.get("shelf_id")),
        "expected_shelf": None if raw.get("expected_shelf") is None else str(raw.get("expected_shelf")),
        "target": None if raw.get("target") is None else str(raw.get("target")),
        "source": str(raw.get("source") or "core"),
        "frame_id": None if raw.get("frame_id") is None else str(raw.get("frame_id")),
        "marker_family": None if raw.get("marker_family") is None else str(raw.get("marker_family")),
        "ocr_text": None if raw.get("ocr_text") is None else str(raw.get("ocr_text")),
        "color": None if raw.get("color") is None else str(raw.get("color")),
        "image_class": None if raw.get("image_class") is None else str(raw.get("image_class")),
        "evidence": raw.get("evidence") if isinstance(raw.get("evidence"), dict) else None,
    }
