from __future__ import annotations

import re
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any


TAG_FAMILY = "TAG36H11"


@dataclass(frozen=True, slots=True)
class VisionDependencyError(RuntimeError):
    message: str

    def __str__(self) -> str:
        return self.message


def iter_tag_ids(device: int = 0, cooldown_seconds: float = 1.5) -> Iterator[str]:
    """Yield stable shelf/item tag ids from the side camera."""

    for detection in iter_detections(device=device, cooldown_seconds=cooldown_seconds):
        yield str(detection["tag_id"])


def iter_detections(
    device: int = 0,
    cooldown_seconds: float = 1.5,
    idle_timeout_seconds: float | None = None,
) -> Iterator[dict[str, object]]:
    """Yield tag/OCR/color/image evidence from the side camera."""

    cv2, Detector = _load_vision_dependencies()
    capture = cv2.VideoCapture(device)
    if not capture.isOpened():
        capture.release()
        raise VisionDependencyError(f"camera device {device} could not be opened")

    detector = Detector(families="tag36h11")
    last_seen: dict[str, float] = {}
    idle_started = time.monotonic()
    try:
        while True:
            detections = _read_stable_detections(capture, detector, cv2)
            if not detections:
                if idle_timeout_seconds is not None and time.monotonic() - idle_started >= idle_timeout_seconds:
                    return
                time.sleep(0.05)
                continue
            idle_started = time.monotonic()
            tag_id, _ = Counter(str(item["tag_id"]) for item in detections).most_common(1)[0]
            now = time.monotonic()
            if now - last_seen.get(tag_id, 0.0) < cooldown_seconds:
                continue
            last_seen[tag_id] = now
            for detection in detections:
                if str(detection["tag_id"]) == tag_id:
                    yield detection
                    break
    finally:
        capture.release()


def _read_stable_detections(capture: Any, detector: Any, cv2: Any, vote_frames: int = 3) -> list[dict[str, object]]:
    detections: list[dict[str, object]] = []
    for _ in range(max(1, vote_frames)):
        ok, frame = capture.read()
        if not ok or frame is None:
            continue
        detections.extend(_detect_frame(frame, detector, cv2))
        time.sleep(0.02)
    if not detections:
        return []
    counts = Counter(str(item["tag_id"]) for item in detections)
    stable_ids = {tag_id for tag_id, count in counts.items() if count >= 2}
    if not stable_ids:
        stable_ids = {counts.most_common(1)[0][0]}
    return [item for item in detections if str(item["tag_id"]) in stable_ids]


def _detect_frame(frame: Any, detector: Any, cv2: Any) -> list[dict[str, object]]:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    raw_detections = detector.detect(gray)
    ocr_text = _try_ocr_text(frame, cv2)
    detections: list[dict[str, object]] = []
    for raw in raw_detections:
        tag_id = str(getattr(raw, "tag_id"))
        center = getattr(raw, "center", None)
        detections.append(
            {
                "tag_id": tag_id,
                "marker_family": TAG_FAMILY,
                "ocr_text": ocr_text,
                "color": _dominant_color_name(frame, center),
                "image_class": None,
                "confidence": _confidence(raw),
            }
        )
    return detections


def _dominant_color_name(frame: Any, center: Any) -> str | None:
    try:
        height, width = frame.shape[:2]
        if center is None:
            x0, x1 = width // 3, width * 2 // 3
            y0, y1 = height // 3, height * 2 // 3
        else:
            x = int(center[0])
            y = int(center[1])
            half = 45
            x0, x1 = max(0, x - half), min(width, x + half)
            y0, y1 = max(0, y - half), min(height, y + half)
        crop = frame[y0:y1, x0:x1]
        if crop.size == 0:
            return None
        b, g, r = [float(value) for value in crop.reshape(-1, 3).mean(axis=0)]
    except (AttributeError, TypeError, ValueError, IndexError):
        return None
    brightest = max(r, g, b)
    darkest = min(r, g, b)
    if brightest < 55:
        return "BLACK"
    if darkest > 205 and brightest - darkest < 35:
        return "WHITE"
    if abs(r - g) < 35 and r > 110 and b < 100:
        return "YELLOW"
    if r > g * 1.25 and r > b * 1.25:
        return "RED"
    if g > r * 1.2 and g > b * 1.2:
        return "GREEN"
    if b > r * 1.2 and b > g * 1.2:
        return "BLUE"
    if r > 110 and b > 110 and g < 100:
        return "PURPLE"
    if r > 120 and g > 70 and b < 80:
        return "ORANGE"
    return "GRAY"


def _try_ocr_text(frame: Any, cv2: Any) -> str | None:
    try:
        import pytesseract  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        height = frame.shape[0]
        roi = frame[: max(1, height // 3), :]
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        text = pytesseract.image_to_string(gray, config="--psm 7")
    except Exception:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "", text).upper()
    return cleaned or None


def _confidence(raw_detection: Any) -> float | None:
    margin = getattr(raw_detection, "decision_margin", None)
    if margin is None:
        return None
    try:
        return round(max(0.0, min(float(margin) / 100.0, 1.0)), 3)
    except (TypeError, ValueError):
        return None


def _load_vision_dependencies() -> tuple[Any, Any]:
    try:
        import cv2  # type: ignore[import-not-found]
    except ImportError as exc:
        raise VisionDependencyError("opencv-python is required for side-camera tag detection") from exc
    try:
        from dt_apriltags import Detector  # type: ignore[import-not-found]
    except ImportError as exc:
        raise VisionDependencyError("dt-apriltags is required for TAG36H11 detection") from exc
    return cv2, Detector
