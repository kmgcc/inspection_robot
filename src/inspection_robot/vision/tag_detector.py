from __future__ import annotations

import importlib
import math
import re
import time
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from .frame_source import get_shared_capture


TAG_FAMILY = "TAG36H11"
Point = tuple[float, float]
Corners = tuple[Point, Point, Point, Point]


@dataclass(frozen=True, slots=True)
class VisionDependencyError(RuntimeError):
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class OcrResult:
    text: str | None
    confidence: float | None = None
    processed: bool = False


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
    capture = get_shared_capture(device, cv2)
    if not capture.isOpened():
        raise VisionDependencyError(f"camera device {device} could not be opened")

    detector = Detector(families="tag36h11")
    last_seen: dict[str, float] = {}
    idle_started = time.monotonic()
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
    detections: list[dict[str, object]] = []
    for raw in raw_detections:
        tag_id = str(getattr(raw, "tag_id"))
        center = _normalize_center(getattr(raw, "center", None))
        corners = _normalize_corners(getattr(raw, "corners", None))
        angle_deg = _angle_degrees(corners)
        ocr = _try_ocr_text(frame, cv2, center=center, corners=corners)
        detections.append(
            {
                "tag_id": tag_id,
                "marker_family": TAG_FAMILY,
                "ocr_text": ocr.text,
                "color": _dominant_color_name(frame, center, cv2),
                "image_class": None,
                "confidence": _confidence(raw),
                "center": _points_to_json(center),
                "corners": _corners_to_json(corners),
                "angle_deg": angle_deg,
                "hamming": _optional_int(getattr(raw, "hamming", None)),
                "goodness": _optional_float(getattr(raw, "goodness", None)),
                "ocr_confidence": ocr.confidence,
                "processed": False,
            }
        )
    return detections


def _dominant_color_name(frame: Any, center: Any, cv2: Any | None = None) -> str | None:
    try:
        height, width = frame.shape[:2]
        if center is None:
            x0, x1 = width // 3, width * 2 // 3
            y0, y1 = height // 3, height * 2 // 3
        else:
            point = _normalize_center(center)
            if point is None:
                return None
            x = int(point[0])
            y = int(point[1])
            half = 45
            x0, x1 = max(0, x - half), min(width, x + half)
            y0, y1 = max(0, y - half), min(height, y + half)
        crop = frame[y0:y1, x0:x1]
        if crop.size == 0:
            return None
        if cv2 is None:
            cv2, _ = _load_cv2_only()
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        h, s, v = [float(value) for value in hsv.reshape(-1, 3).mean(axis=0)]
    except (AttributeError, TypeError, ValueError, IndexError):
        return None

    if v < 45:
        return "BLACK"
    if s < 24:
        if v > 245:
            return "WHITE"
        if 70 <= v <= 185:
            return "GRAY"
        return None
    if h <= 10 or h >= 170:
        return "RED"
    if 18 <= h <= 38:
        return "YELLOW"
    if 39 <= h <= 88:
        return "GREEN"
    if 90 <= h <= 130:
        return "BLUE"
    if 131 <= h <= 165:
        return "PURPLE"
    if 11 <= h < 18:
        return "ORANGE"
    return None


def _try_ocr_text(
    frame: Any,
    cv2: Any,
    *,
    center: Any = None,
    corners: Any = None,
    min_confidence: float = 55.0,
) -> OcrResult:
    try:
        pytesseract = importlib.import_module("pytesseract")
    except ImportError:
        return OcrResult(None, None, False)

    try:
        roi = _ocr_roi(frame, center=center, corners=corners)
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        denoised = cv2.GaussianBlur(gray, (3, 3), 0)
        processed = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
        data_kwargs: dict[str, object] = {"config": "--psm 7"}
        output = getattr(pytesseract, "Output", None)
        if output is not None and hasattr(output, "DICT"):
            data_kwargs["output_type"] = output.DICT
        data = pytesseract.image_to_data(processed, **data_kwargs)
        result = _ocr_from_data(data, min_confidence=min_confidence)
        if result.confidence is not None or result.text is not None:
            return OcrResult(result.text, result.confidence, True)
        text = pytesseract.image_to_string(processed, config="--psm 7")
    except Exception:
        return OcrResult(None, None, False)
    cleaned = _clean_ocr_text(text)
    return OcrResult(cleaned, None, True)


def _ocr_from_data(data: object, *, min_confidence: float) -> OcrResult:
    if not isinstance(data, Mapping):
        return OcrResult(None, None, True)
    raw_text = data.get("text")
    raw_conf = data.get("conf")
    if not isinstance(raw_text, list) or not isinstance(raw_conf, list):
        return OcrResult(None, None, True)

    best_text: str | None = None
    best_conf: float | None = None
    for text, confidence in zip(raw_text, raw_conf):
        parsed_conf = _parse_confidence(confidence)
        cleaned = _clean_ocr_text(str(text))
        if parsed_conf is None:
            continue
        if best_conf is None or parsed_conf > best_conf:
            best_conf = parsed_conf
            best_text = cleaned
    if best_conf is None:
        return OcrResult(None, None, True)
    if best_conf < min_confidence:
        return OcrResult(None, best_conf, True)
    return OcrResult(best_text, best_conf, True)


def _ocr_roi(frame: Any, *, center: Any = None, corners: Any = None) -> Any:
    height, width = frame.shape[:2]
    normalized_corners = _normalize_corners(corners)
    if normalized_corners is not None:
        xs = [point[0] for point in normalized_corners]
        ys = [point[1] for point in normalized_corners]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        pad_x = max(12.0, (x1 - x0) * 0.35)
        pad_y = max(12.0, (y1 - y0) * 0.85)
        bounds = (
            max(0, int(x0 - pad_x)),
            min(width, int(x1 + pad_x)),
            max(0, int(y0 - pad_y)),
            min(height, int(y1 + pad_y)),
        )
    else:
        normalized_center = _normalize_center(center)
        if normalized_center is None:
            bounds = (0, width, 0, max(1, height // 3))
        else:
            x = int(normalized_center[0])
            y = int(normalized_center[1])
            half_w = max(35, width // 5)
            half_h = max(24, height // 6)
            bounds = (
                max(0, x - half_w),
                min(width, x + half_w),
                max(0, y - half_h),
                min(height, y + half_h),
            )
    x0, x1, y0, y1 = bounds
    roi = frame[y0:y1, x0:x1]
    return roi if getattr(roi, "size", 0) else frame


def _clean_ocr_text(text: str) -> str | None:
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


def _normalize_center(center: Any) -> Point | None:
    if center is None:
        return None
    try:
        return (float(center[0]), float(center[1]))
    except (TypeError, ValueError, IndexError):
        return None


def _normalize_corners(corners: Any) -> Corners | None:
    if corners is None:
        return None
    try:
        points = [(float(point[0]), float(point[1])) for point in corners]
    except (TypeError, ValueError, IndexError):
        return None
    if len(points) != 4:
        return None
    return (points[0], points[1], points[2], points[3])


def _points_to_json(point: Point | None) -> list[float] | None:
    if point is None:
        return None
    return [point[0], point[1]]


def _corners_to_json(corners: Corners | None) -> list[list[float]] | None:
    if corners is None:
        return None
    return [[point[0], point[1]] for point in corners]


def _angle_degrees(corners: Corners | None) -> float | None:
    if corners is None:
        return None
    first, second = corners[0], corners[1]
    angle = math.degrees(math.atan2(second[1] - first[1], second[0] - first[0]))
    if abs(angle) < 0.0005:
        angle = 0.0
    return round(angle, 3)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_confidence(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return parsed


def _load_vision_dependencies() -> tuple[Any, Any]:
    try:
        import cv2
    except ImportError as exc:
        raise VisionDependencyError("opencv-python is required for side-camera tag detection") from exc
    try:
        from dt_apriltags import Detector
    except ImportError:
        if _opencv_apriltag_available(cv2):
            return cv2, _OpenCVArucoAprilTagDetector
        raise VisionDependencyError("dt-apriltags or OpenCV aruco AprilTag support is required for TAG36H11 detection")
    return cv2, Detector


def _opencv_apriltag_available(cv2: Any) -> bool:
    aruco = getattr(cv2, "aruco", None)
    return (
        aruco is not None
        and hasattr(aruco, "DICT_APRILTAG_36h11")
        and hasattr(aruco, "ArucoDetector")
        and hasattr(aruco, "DetectorParameters")
        and hasattr(aruco, "getPredefinedDictionary")
    )


@dataclass(slots=True)
class _OpenCVArucoDetection:
    tag_id: int
    center: Point
    corners: Corners | None = None
    angle_deg: float | None = None
    decision_margin: float | None = None


class _OpenCVArucoAprilTagDetector:
    def __init__(self, families: str = "tag36h11") -> None:
        cv2, _ = _load_cv2_only()
        dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
        parameters = cv2.aruco.DetectorParameters()
        self._detector = cv2.aruco.ArucoDetector(dictionary, parameters)

    def detect(self, gray: Any) -> list[_OpenCVArucoDetection]:
        corners, ids, _ = self._detector.detectMarkers(gray)
        if ids is None:
            return []
        detections: list[_OpenCVArucoDetection] = []
        for marker_corners, marker_id in zip(corners, ids.flatten()):
            points = marker_corners.reshape(-1, 2)
            normalized = _normalize_corners(points)
            center_x = float(points[:, 0].mean())
            center_y = float(points[:, 1].mean())
            detections.append(
                _OpenCVArucoDetection(
                    tag_id=int(marker_id),
                    center=(center_x, center_y),
                    corners=normalized,
                    angle_deg=_angle_degrees(normalized),
                )
            )
        return detections


def _load_cv2_only() -> tuple[Any, None]:
    try:
        import cv2
    except ImportError as exc:
        raise VisionDependencyError("opencv-python is required for side-camera tag detection") from exc
    return cv2, None
