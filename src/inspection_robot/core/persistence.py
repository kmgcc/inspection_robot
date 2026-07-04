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
    for event in events:
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
    events: list[EventRecord] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            events.append(_normalize_event(item))
        except (TypeError, ValueError):
            continue
    return events


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
        "id": _text(raw.get("id") or fallback["id"]),
        "time": _text(raw.get("time") or fallback["time"]),
        "type": _text(raw.get("type") or "system"),
        "tag_id": _optional_text(raw.get("tag_id")),
        "item": _text(raw.get("item") or "-"),
        "zone": _text(raw.get("zone") or "-"),
        "expected_zone": _optional_text(raw.get("expected_zone")),
        "priority": max(normalized_priority, 1),
        "status": _text(raw.get("status") or "info"),
        "message": _text(raw.get("message") or ""),
        "shelf_id": _optional_text(raw.get("shelf_id")),
        "expected_shelf": _optional_text(raw.get("expected_shelf")),
        "target": _optional_text(raw.get("target")),
        "source": _text(raw.get("source") or "core"),
        "frame_id": _optional_text(raw.get("frame_id")),
        "marker_family": _optional_text(raw.get("marker_family")),
        "ocr_text": _optional_text(raw.get("ocr_text")),
        "color": _optional_text(raw.get("color")),
        "image_class": _optional_text(raw.get("image_class")),
        "evidence": raw.get("evidence") if isinstance(raw.get("evidence"), dict) else None,
    }


def _optional_text(value: JsonValue) -> str | None:
    if value is None:
        return None
    return _text(value)


def _text(value: JsonValue) -> str:
    if isinstance(value, (dict, list)):
        raise ValueError("event scalar field must not be an object or list")
    return str(value)
