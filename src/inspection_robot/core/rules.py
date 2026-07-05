from __future__ import annotations

from collections import Counter
from collections.abc import Mapping

from ..config import JsonValue, ShelfManifest, TagInfo, TagMap
from .events import EventRecord, make_event


def evaluate_shelf_scan(
    shelf_id: str,
    detected_items: list[str],
    manifest: ShelfManifest,
    tag_map: TagMap,
    *,
    source: str = "core",
    frame_id: str | None = None,
    skip_missing: bool = False,
) -> list[EventRecord]:
    detected_items = normalize_detected_item_ids(detected_items, tag_map)
    expected = set(manifest.get(shelf_id, {"expected_items": []})["expected_items"])
    counts = Counter(detected_items)
    item_lookup = _items_by_item_id(tag_map)
    events: list[EventRecord] = []

    if not detected_items and expected and not skip_missing:
        return [
            make_event(
                "scan_failed",
                item="-",
                shelf_id=shelf_id,
                expected_shelf=shelf_id,
                priority=2,
                status="waiting_confirm",
                message=f"{shelf_id} 货架本次扫描未识别到有效物品，建议重试或人工复核。",
                source=source,
                frame_id=frame_id,
            )
        ]

    if skip_missing:
        for item_id in counts:
            tag_entry = item_lookup.get(item_id)
            if tag_entry is None:
                continue
            tag_id, info = tag_entry
            events.append(_normal_item(tag_id, info, shelf_id, source, frame_id))
        return events or [_first_pass_observed(shelf_id, source, frame_id)]

    for item_id in counts:
        tag_entry = item_lookup.get(item_id)
        if tag_entry is None:
            events.append(_unknown_item(item_id, shelf_id, source, frame_id))
            continue
        tag_id, info = tag_entry
        expected_shelf = info.get("expected_shelf")
        if expected_shelf is None:
            events.append(_normal_item(tag_id, info, shelf_id, source, frame_id))
        elif expected_shelf != shelf_id:
            events.append(
                make_event(
                    "wrong_shelf",
                    tag_id=tag_id,
                    item=info["name"],
                    shelf_id=shelf_id,
                    expected_shelf=expected_shelf,
                    priority=max(int(info.get("priority", 1)), 2),
                    status="waiting_confirm",
                    message=f"{shelf_id} 货架识别到应在 {expected_shelf} 的 {info['name']}。",
                    source=source,
                    frame_id=frame_id,
                )
            )
        if counts[item_id] > 1:
            events.append(
                make_event(
                    "duplicate_item",
                    tag_id=tag_id,
                    item=info["name"],
                    shelf_id=shelf_id,
                    expected_shelf=expected_shelf,
                    priority=max(int(info.get("priority", 1)), 2),
                    status="waiting_confirm",
                    message=f"{shelf_id} 货架重复识别到 {info['name']}。",
                    source=source,
                    frame_id=frame_id,
                )
            )

    missing_items = sorted(expected - set(counts))
    if not skip_missing:
        for item_id in missing_items:
            tag_entry = item_lookup.get(item_id)
            tag_id, info = tag_entry if tag_entry is not None else (
                item_id,
                {"name": item_id, "kind": "item", "item_id": item_id, "expected_shelf": shelf_id, "priority": 2},
            )
            events.append(
                make_event(
                    "missing_item",
                    tag_id=tag_id,
                    item=str(info["name"]),
                    shelf_id=shelf_id,
                    expected_shelf=shelf_id,
                    priority=max(int(info.get("priority", 1)), 2),
                    status="warning",
                    message=f"{shelf_id} 货架缺少 {info['name']}。",
                    source=source,
                    frame_id=frame_id,
                    evidence={"identity_kind": "item", "change": "missing", "item_id": str(info.get("item_id", item_id))},
                )
            )

    if events:
        return events
    if skip_missing and missing_items:
        return [
            make_event(
                "first_pass_observed",
                item="-",
                shelf_id=shelf_id,
                expected_shelf=shelf_id,
                priority=1,
                status="info",
                message=f"第 1 轮已观察 {shelf_id} 货架，暂不做缺货判断。",
                source=source,
                frame_id=frame_id,
            )
        ]
    return [_shelf_scanned(shelf_id, source, frame_id)]


def normalize_detected_item_ids(detected_items: list[str], tag_map: TagMap) -> list[str]:
    item_ids = {
        str(info["item_id"])
        for info in tag_map.values()
        if str(info.get("kind", "item")) == "item" and "item_id" in info
    }
    shelf_ids = {
        str(info["shelf_id"]).strip().upper()
        for info in tag_map.values()
        if str(info.get("kind")) == "shelf" and info.get("shelf_id") is not None
    }
    normalized: list[str] = []
    for raw_item in detected_items:
        item_id = _item_id_from_detected_token(str(raw_item).strip(), tag_map, item_ids, shelf_ids)
        if item_id is not None:
            normalized.append(item_id)
    return normalized


def evaluate_detection_evidence(
    shelf_id: str,
    detections: list[Mapping[str, JsonValue]],
    manifest: ShelfManifest,
    tag_map: TagMap,
    *,
    source: str = "core",
    frame_id: str | None = None,
    skip_missing: bool = False,
) -> list[EventRecord]:
    detected_items: list[str] = []
    events: list[EventRecord] = []
    shelf_evidence_seen = False
    for detection in detections:
        tag_id = _text(detection, "tag_id")
        if tag_id is None:
            event = _untagged_evidence(detection, shelf_id, source, frame_id)
            if event is not None:
                events.append(event)
            continue
        info = tag_map.get(tag_id)
        if info is None:
            events.append(_unknown_item(tag_id, shelf_id, source, frame_id))
            continue
        kind = str(info.get("kind", "item"))
        if kind == "shelf":
            shelf_evidence_seen = True
            events.extend(_shelf_evidence_events(shelf_id, tag_id, info, detection, source, frame_id))
            continue
        if kind != "item":
            continue
        item_id = info["item_id"]
        detected_items.append(item_id)
        mismatch = _item_mismatch(info, detection)
        if mismatch:
            events.append(
                make_event(
                    "evidence_mismatch",
                    tag_id=tag_id,
                    item=info["name"],
                    shelf_id=shelf_id,
                    expected_shelf=info.get("expected_shelf"),
                    priority=max(int(info.get("priority", 1)), 2),
                    status="waiting_confirm",
                    message=f"{info['name']} 识别证据不一致：{'; '.join(mismatch)}。",
                    source=source,
                    frame_id=frame_id,
                    marker_family=_text(detection, "marker_family"),
                    ocr_text=_text(detection, "ocr_text"),
                    color=_text(detection, "color"),
                    image_class=_text(detection, "image_class"),
                    evidence={"mismatch": mismatch, "identity_kind": "item", "item_id": str(info.get("item_id", ""))},
                )
            )
    if shelf_evidence_seen and not detected_items and not events and skip_missing:
        events.append(_shelf_scanned(shelf_id, source, frame_id))
    elif detected_items or not events:
        events.extend(
            evaluate_shelf_scan(
                shelf_id,
                detected_items,
                manifest,
                tag_map,
                source=source,
                frame_id=frame_id,
                skip_missing=skip_missing,
            )
        )
    return events


def normal_tag(tag_id: str, info: TagInfo, *, current_shelf: str | None = None, source: str = "simulate") -> EventRecord | None:
    if str(info.get("kind", "item")) != "item":
        return None
    shelf_id = current_shelf or "A1"
    expected_shelf = info.get("expected_shelf")
    if expected_shelf is not None and expected_shelf != shelf_id:
        return None
    return _normal_item(tag_id, info, shelf_id, source, None)


def unknown_tag(tag_id: str, *, current_shelf: str | None = None, source: str = "simulate") -> EventRecord:
    return _unknown_item(tag_id, current_shelf or "-", source, None)


def wrong_zone(tag_id: str, info: TagInfo, *, current_shelf: str | None = None, source: str = "simulate") -> EventRecord | None:
    if str(info.get("kind", "item")) != "item":
        return None
    shelf_id = current_shelf or "A1"
    expected_shelf = info.get("expected_shelf")
    if expected_shelf is None or expected_shelf == shelf_id:
        return None
    return make_event(
        "wrong_shelf",
        tag_id=tag_id,
        item=info["name"],
        zone=info.get("zone", "-"),
        expected_zone=info.get("expected_zone"),
        shelf_id=shelf_id,
        expected_shelf=expected_shelf,
        priority=max(int(info.get("priority", 1)), 2),
        status="waiting_confirm",
        message=f"{shelf_id} 货架识别到应在 {expected_shelf} 的 {info['name']}。来源：{source}。",
        source=source,
    )


def _item_id_from_detected_token(
    token: str,
    tag_map: TagMap,
    item_ids: set[str],
    shelf_ids: set[str],
) -> str | None:
    if not token:
        return None
    info = tag_map.get(token)
    if info is not None:
        kind = str(info.get("kind", "item"))
        if kind == "item" and info.get("item_id") is not None:
            return str(info["item_id"])
        return None
    if token in item_ids:
        return token
    if token.strip().upper() in shelf_ids or _looks_like_shelf_id(token):
        return None
    try:
        numeric_id = int(token)
    except ValueError:
        return token
    if 101 <= numeric_id <= 120:
        return None
    return token


def _looks_like_shelf_id(token: str) -> bool:
    normalized = token.strip().upper()
    return len(normalized) >= 2 and normalized[0].isalpha() and normalized[1:].isdigit()


def _items_by_item_id(tag_map: TagMap) -> dict[str, tuple[str, TagInfo]]:
    return {
        info["item_id"]: (tag_id, info)
        for tag_id, info in tag_map.items()
        if str(info.get("kind", "item")) == "item" and "item_id" in info
    }


def _unknown_item(tag_id: str, shelf_id: str, source: str, frame_id: str | None) -> EventRecord:
    return make_event(
        "unknown_item",
        tag_id=tag_id,
        item="Unknown",
        shelf_id=shelf_id,
        priority=2,
        status="waiting_confirm",
        message=f"{shelf_id} 货架识别到未知标签 {tag_id}。",
        source=source,
        frame_id=frame_id,
        evidence={"identity_kind": "item"},
    )


def _normal_item(tag_id: str, info: TagInfo, shelf_id: str, source: str, frame_id: str | None) -> EventRecord:
    expected_shelf = info.get("expected_shelf") or shelf_id
    return make_event(
        "normal_item",
        tag_id=tag_id,
        item=info["name"],
        zone=info.get("zone", "-"),
        expected_zone=info.get("expected_zone"),
        shelf_id=shelf_id,
        expected_shelf=expected_shelf,
        priority=int(info.get("priority", 1)),
        status="normal",
        message=f"{shelf_id} 货架识别到物品 {info['name']}。来源：{source}。",
        source=source,
        frame_id=frame_id,
        evidence={"identity_kind": "item", "item_id": str(info.get("item_id", ""))},
    )


def _first_pass_observed(shelf_id: str, source: str, frame_id: str | None) -> EventRecord:
    return make_event(
        "first_pass_observed",
        item="-",
        shelf_id=shelf_id,
        expected_shelf=shelf_id,
        priority=1,
        status="info",
        message=f"first pass observed {shelf_id}; shortage checks are skipped.",
        source=source,
        frame_id=frame_id,
        evidence={"identity_kind": "shelf"},
    )


def _shelf_scanned(shelf_id: str, source: str, frame_id: str | None) -> EventRecord:
    return make_event(
        "shelf_scanned",
        item="-",
        shelf_id=shelf_id,
        expected_shelf=shelf_id,
        priority=1,
        status="normal",
        message=f"{shelf_id} 货架扫描完成，未发现异常。",
        source=source,
        frame_id=frame_id,
        evidence={"identity_kind": "shelf"},
    )


def _untagged_evidence(detection: Mapping[str, JsonValue], shelf_id: str, source: str, frame_id: str | None) -> EventRecord | None:
    evidence: dict[str, JsonValue] = {}
    for field in ("marker_family", "ocr_text", "color", "image_class", "confidence"):
        value = detection.get(field)
        if value is not None:
            evidence[field] = value
    if not evidence:
        return None
    return make_event(
        "untagged_evidence",
        item="Unknown",
        shelf_id=shelf_id,
        expected_shelf=shelf_id,
        priority=2,
        status="waiting_confirm",
        message=f"{shelf_id} 货架发现无法绑定 AprilTag 的视觉证据，需人工复核。",
        source=source,
        frame_id=frame_id,
        marker_family=_text(detection, "marker_family"),
        ocr_text=_text(detection, "ocr_text"),
        color=_text(detection, "color"),
        image_class=_text(detection, "image_class"),
        evidence=evidence,
    )


def _text(detection: Mapping[str, JsonValue], field: str) -> str | None:
    value = detection.get(field)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _item_mismatch(info: TagInfo, detection: Mapping[str, JsonValue]) -> list[str]:
    mismatch: list[str] = []
    _compare_evidence(mismatch, "marker_family", info.get("marker_family"), _text(detection, "marker_family"))
    _compare_evidence(mismatch, "color", info.get("expected_color"), _text(detection, "color"))
    _compare_evidence(mismatch, "ocr", info.get("expected_ocr"), _text(detection, "ocr_text"))
    _compare_evidence(mismatch, "image", info.get("expected_image_class"), _text(detection, "image_class"))
    return mismatch


def _compare_evidence(mismatch: list[str], label: str, expected: str | None, actual: str | None) -> None:
    if expected is None or actual is None:
        return
    if _normalized_evidence(expected) != _normalized_evidence(actual):
        mismatch.append(f"{label} expected {expected}, got {actual}")


def _normalized_evidence(value: str) -> str:
    return value.strip().casefold()


def _shelf_evidence_events(
    shelf_id: str,
    tag_id: str,
    info: TagInfo,
    detection: Mapping[str, JsonValue],
    source: str,
    frame_id: str | None,
) -> list[EventRecord]:
    expected_label = info.get("ocr_label")
    ocr_text = _text(detection, "ocr_text")
    mapped_shelf = info.get("shelf_id")
    mismatch: list[str] = []
    if mapped_shelf != shelf_id:
        mismatch.append(f"tag maps to {mapped_shelf}, current shelf is {shelf_id}")
    if expected_label is not None and ocr_text is not None and _normalized_evidence(expected_label) != _normalized_evidence(ocr_text):
        mismatch.append(f"ocr expected {expected_label}, got {ocr_text}")
    if not mismatch:
        return []
    return [
        make_event(
            "evidence_mismatch",
            tag_id=tag_id,
            item=info["name"],
            shelf_id=shelf_id,
            expected_shelf=mapped_shelf,
            priority=max(int(info.get("priority", 1)), 2),
            status="waiting_confirm",
            message=f"货架 {shelf_id} 识别证据不一致：{'; '.join(mismatch)}。",
            source=source,
            frame_id=frame_id,
            marker_family=_text(detection, "marker_family"),
            ocr_text=ocr_text,
            evidence={"mismatch": mismatch},
        )
    ]
