from __future__ import annotations

import importlib
import math
import os
import re
import threading
import time
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from .frame_source import get_shared_capture, read_camera_frame
from .stability import DetectionStabilityTracker, StabilityConfig
from .tag_detector_types import CameraFrameError


TAG_FAMILY = "TAG36H11"
Point = tuple[float, float]
Corners = tuple[Point, Point, Point, Point]
_PADDLE_OCR: Any | None = None
_PADDLE_OCR_UNAVAILABLE = False
_PADDLE_OCR_LOCK = threading.Lock()


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


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# Only AprilTag identity is kept by default. OCR (heavy on the Pi, contends for
# the GIL with the control loop) and colour attribute labelling are disabled
# unless explicitly re-enabled with ENABLE_OCR=1 / ENABLE_COLOR=1. Identity is
# 100% tag-driven, so turning these off does not affect shelf/item recognition.
_OCR_ENABLED = _env_flag("ENABLE_OCR", False)
_COLOR_ENABLED = _env_flag("ENABLE_COLOR", False)
_EMPTY_OCR = OcrResult(None)


def ocr_enabled() -> bool:
    """Whether OCR text extraction is active (ENABLE_OCR)."""

    return _OCR_ENABLED


def color_enabled() -> bool:
    """Whether colour attribute labelling is active (ENABLE_COLOR)."""

    return _COLOR_ENABLED


@dataclass(frozen=True, slots=True)
class ImageClassResult:
    class_name: str
    confidence: float
    source: str = "opencv_shape"


@dataclass(frozen=True, slots=True)
class ObjectPresenceResult:
    bbox: tuple[int, int, int, int]
    center: Point
    confidence: float


_SEMANTIC_IMAGE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("水杯", "CUP"),
    ("杯", "CUP"),
    ("CUP", "CUP"),
    ("瓶", "BOTTLE"),
    ("BOTTLE", "BOTTLE"),
    ("盒", "BOX"),
    ("箱", "BOX"),
    ("BOX", "BOX"),
    ("药", "MEDICINE_BOX"),
    ("电池", "BATTERY"),
    ("BATTERY", "BATTERY"),
)


def iter_tag_ids(device: int = 0, cooldown_seconds: float = 1.5) -> Iterator[str]:
    """Yield stable shelf/item tag ids from the side camera."""

    for detection in iter_detections(device=device, cooldown_seconds=cooldown_seconds):
        tag_id = detection.get("tag_id")
        if tag_id is not None:
            yield str(tag_id)


def iter_detections(
    device: int = 0,
    cooldown_seconds: float = 1.5,
    idle_timeout_seconds: float | None = None,
    *,
    stability_enabled: bool = False,
    stability_min_frames: int = 3,
    stability_max_center_shift_px: float = 10.0,
    stability_max_corner_shift_px: float = 14.0,
    stability_max_angle_delta_deg: float = 8.0,
    image_classifier_enabled: bool = False,
    vision_state_machine_enabled: bool = False,
    vote_frames: int = 3,
    require_consensus: bool = True,
    yield_all_detections: bool = False,
) -> Iterator[dict[str, object]]:
    """Yield tag/OCR/color/image evidence from the side camera."""

    cv2, Detector = _load_vision_dependencies()
    capture = get_shared_capture(device, cv2)
    if not capture.isOpened():
        raise VisionDependencyError(f"camera device {device} could not be opened")

    detector = Detector(families="tag36h11")
    last_seen: dict[str, float] = {}
    idle_started = time.monotonic()
    tracker = (
        DetectionStabilityTracker(
            StabilityConfig(
                min_stable_frames=stability_min_frames,
                max_center_shift_px=stability_max_center_shift_px,
                max_corner_shift_px=stability_max_corner_shift_px,
                max_angle_delta_deg=stability_max_angle_delta_deg,
            )
        )
        if stability_enabled
        else None
    )
    state_machine = None
    if vision_state_machine_enabled:
        from .state_machine import VisionStateMachine

        state_machine = VisionStateMachine()
    while True:
        detections = _read_stable_detections(
            capture,
            detector,
            cv2,
            vote_frames=vote_frames,
            tracker=tracker,
            image_classifier_enabled=image_classifier_enabled,
            require_consensus=require_consensus,
        )
        if not detections:
            if idle_timeout_seconds is not None and time.monotonic() - idle_started >= idle_timeout_seconds:
                return
            time.sleep(0.05)
            continue
        idle_started = time.monotonic()
        now = time.monotonic()
        if yield_all_detections:
            for detection in detections:
                tag_id = _detection_group_key(detection)
                if now - last_seen.get(tag_id, 0.0) < cooldown_seconds:
                    continue
                last_seen[tag_id] = now
                if state_machine is not None:
                    state = state_machine.run_until_done(detection)
                    detection = dict(detection)
                    detection["vision_state"] = state.value
                yield detection
            continue
        tag_id, _ = Counter(_detection_group_key(item) for item in detections).most_common(1)[0]
        if now - last_seen.get(tag_id, 0.0) < cooldown_seconds:
            continue
        last_seen[tag_id] = now
        for detection in detections:
            if _detection_group_key(detection) == tag_id:
                if state_machine is not None:
                    state = state_machine.run_until_done(detection)
                    detection = dict(detection)
                    detection["vision_state"] = state.value
                yield detection
                break


def iter_detections_with_state(**kwargs: object) -> Iterator[dict[str, object]]:
    kwargs["vision_state_machine_enabled"] = True
    yield from iter_detections(**kwargs)


def _read_stable_detections(
    capture: Any,
    detector: Any,
    cv2: Any,
    vote_frames: int = 3,
    *,
    tracker: DetectionStabilityTracker | None = None,
    image_classifier_enabled: bool = False,
    require_consensus: bool = True,
) -> list[dict[str, object]]:
    detections: list[dict[str, object]] = []
    for _ in range(max(1, vote_frames)):
        ok, frame = capture.read()
        if not ok or frame is None:
            continue
        frame_detections = _detect_frame(frame, detector, cv2, image_classifier_enabled=image_classifier_enabled)
        if tracker is None:
            detections.extend(frame_detections)
        else:
            for detection in frame_detections:
                stable = tracker.update(detection)
                if stable is not None:
                    detections.append(stable)
        time.sleep(0.02)
    if not detections:
        return []
    if not require_consensus:
        return detections
    counts = Counter(_detection_group_key(item) for item in detections)
    stable_ids = {tag_id for tag_id, count in counts.items() if count >= 2}
    if not stable_ids:
        stable_ids = {counts.most_common(1)[0][0]}
    return [item for item in detections if _detection_group_key(item) in stable_ids]


def _detect_frame(
    frame: Any,
    detector: Any,
    cv2: Any,
    *,
    image_classifier_enabled: bool = False,
    ocr_enabled: bool | None = None,
    color_enabled: bool | None = None,
) -> list[dict[str, object]]:
    ocr_on = _OCR_ENABLED if ocr_enabled is None else bool(ocr_enabled)
    color_on = _COLOR_ENABLED if color_enabled is None else bool(color_enabled)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    raw_detections = detector.detect(gray)
    detections: list[dict[str, object]] = []
    excluded_bboxes: list[tuple[int, int, int, int]] = []
    for raw in raw_detections:
        tag_id = str(getattr(raw, "tag_id"))
        center = _normalize_center(getattr(raw, "center", None))
        corners = _normalize_corners(getattr(raw, "corners", None))
        bbox = _corners_to_bbox(corners)
        if bbox is not None:
            excluded_bboxes.append(bbox)
        angle_deg = _angle_degrees(corners)
        ocr = _try_ocr_text(frame, cv2, center=center, corners=corners) if ocr_on else _EMPTY_OCR
        image_class = _classify_image_region(frame, center, corners, cv2) if image_classifier_enabled else None
        image_class = _class_with_ocr_semantics(image_class, ocr)
        detections.append(
            {
                "tag_id": tag_id,
                "marker_family": TAG_FAMILY,
                "ocr_text": ocr.text,
                "color": _dominant_color_name(frame, center, cv2) if color_on else None,
                "image_class": image_class.class_name if image_class is not None else None,
                "image_class_confidence": image_class.confidence if image_class is not None else None,
                "image_class_source": image_class.source if image_class is not None else None,
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
    object_regions = _detect_object_presence_regions(frame, cv2, exclude_bboxes=excluded_bboxes, max_results=2)
    if not object_regions:
        return detections
    synthetic_detections = [
        _synthetic_detection_from_object(
            frame,
            cv2,
            object_presence,
            image_classifier_enabled=image_classifier_enabled,
            ocr_enabled=ocr_on,
            color_enabled=color_on,
        )
        for object_presence in object_regions
    ]
    return detections + synthetic_detections


def _synthetic_detection_from_object(
    frame: Any,
    cv2: Any,
    object_presence: ObjectPresenceResult,
    *,
    image_classifier_enabled: bool,
    ocr_enabled: bool | None = None,
    color_enabled: bool | None = None,
) -> dict[str, object]:
    ocr_on = _OCR_ENABLED if ocr_enabled is None else bool(ocr_enabled)
    color_on = _COLOR_ENABLED if color_enabled is None else bool(color_enabled)
    box_corners = _bbox_to_corners(object_presence.bbox)
    ocr = (
        _try_ocr_text(frame, cv2, center=object_presence.center, corners=box_corners)
        if ocr_on
        else _EMPTY_OCR
    )
    image_class = _classify_image_region(frame, object_presence.center, box_corners, cv2) if image_classifier_enabled else None
    image_class = _class_with_ocr_semantics(image_class, ocr)
    color = (
        _dominant_color_name_for_region(frame, box_corners, cv2) or _dominant_color_name(frame, object_presence.center, cv2)
        if color_on
        else None
    )
    return {
        "tag_id": None,
        "marker_family": None,
        "ocr_text": ocr.text,
        "color": color,
        "image_class": image_class.class_name if image_class is not None else None,
        "image_class_confidence": image_class.confidence if image_class is not None else None,
        "image_class_source": image_class.source if image_class is not None else None,
        "confidence": object_presence.confidence,
        "center": _points_to_json(object_presence.center),
        "corners": _corners_to_json(box_corners),
        "bbox": [object_presence.bbox[0], object_presence.bbox[1], object_presence.bbox[2], object_presence.bbox[3]],
        "angle_deg": None,
        "hamming": None,
        "goodness": None,
        "ocr_confidence": ocr.confidence,
        "processed": False,
        "source": "synthetic_untagged",
    }


def detect_object_presence(
    frame: Any,
    cv2: Any,
    roi: object = None,
    *,
    detector: str = "opencv",
    model_path: str = "",
    min_area_ratio: float = 0.015,
) -> bool:
    return _detect_object_presence_region(
        frame,
        cv2,
        roi,
        detector=detector,
        model_path=model_path,
        min_area_ratio=min_area_ratio,
    ) is not None


def _detect_object_presence_region(
    frame: Any,
    cv2: Any,
    roi: object = None,
    *,
    detector: str = "opencv",
    model_path: str = "",
    min_area_ratio: float = 0.015,
) -> ObjectPresenceResult | None:
    regions = _detect_object_presence_regions(
        frame,
        cv2,
        roi,
        detector=detector,
        model_path=model_path,
        min_area_ratio=min_area_ratio,
        max_results=1,
    )
    return regions[0] if regions else None


def _detect_object_presence_regions(
    frame: Any,
    cv2: Any,
    roi: object = None,
    *,
    detector: str = "opencv",
    model_path: str = "",
    min_area_ratio: float = 0.015,
    exclude_bboxes: list[tuple[int, int, int, int]] | None = None,
    max_results: int = 3,
) -> list[ObjectPresenceResult]:
    normalized = str(detector or "opencv").strip().lower()
    if normalized in {"yolov5_lite_cpu", "hailo_yolo"} and not str(model_path or "").strip():
        normalized = "opencv"
    if normalized not in {"opencv", "yolov5_lite_cpu", "hailo_yolo"}:
        normalized = "opencv"
    crop, offset_x, offset_y = _presence_roi_with_offset(frame, roi)
    regions: list[ObjectPresenceResult] = []
    try:
        height, width = crop.shape[:2]
        if height <= 0 or width <= 0:
            return []
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 45, 145)
        contours: list[Any] = []
        contours.extend(cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2])
        _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        contours.extend(cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2])
        frame_area = float(width * height)
        min_area = max(80.0, frame_area * max(0.0, float(min_area_ratio)))
        for contour in sorted(contours, key=cv2.contourArea, reverse=True):
            area = float(cv2.contourArea(contour))
            if area < min_area or area > frame_area * 0.92:
                continue
            x, y, box_w, box_h = cv2.boundingRect(contour)
            if box_w <= 2 or box_h <= 2:
                continue
            box_area = float(box_w * box_h)
            if box_area > frame_area * 0.58:
                continue
            aspect = box_w / max(1.0, float(box_h))
            extent = area / max(1.0, float(box_w * box_h))
            if 0.2 <= aspect <= 5.0 and extent >= 0.18:
                x0 = int(x + offset_x)
                y0 = int(y + offset_y)
                candidate_bbox = (x0, y0, int(box_w), int(box_h))
                if _bbox_overlaps_any(candidate_bbox, exclude_bboxes or [], min_iou=0.12):
                    continue
                if _bbox_overlaps_any(candidate_bbox, [item.bbox for item in regions], min_iou=0.45):
                    continue
                confidence = round(min(0.95, max(0.35, area / max(1.0, min_area * 4.0))), 3)
                regions.append(ObjectPresenceResult(candidate_bbox, (x0 + box_w / 2.0, y0 + box_h / 2.0), confidence))
                if len(regions) >= max(1, max_results):
                    break
        if len(regions) < max(1, max_results):
            for color_region in _color_anchor_presence_regions(
                crop,
                cv2,
                offset_x=offset_x,
                offset_y=offset_y,
                exclude_bboxes=(exclude_bboxes or []) + [item.bbox for item in regions],
                max_results=max(1, max_results) - len(regions),
            ):
                regions.append(color_region)
    except Exception:
        return []
    return regions


def _color_anchor_presence_regions(
    frame: Any,
    cv2: Any,
    *,
    offset_x: int = 0,
    offset_y: int = 0,
    exclude_bboxes: list[tuple[int, int, int, int]] | None = None,
    max_results: int = 3,
) -> list[ObjectPresenceResult]:
    try:
        height, width = frame.shape[:2]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, (0, 80, 50), (179, 255, 255))
        contours = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]
    except Exception:
        return []
    regions: list[ObjectPresenceResult] = []
    min_y = int(height * 0.30)
    for contour in sorted(contours, key=cv2.contourArea, reverse=True):
        area = float(cv2.contourArea(contour))
        if area < 220.0:
            continue
        x, y, box_w, box_h = cv2.boundingRect(contour)
        if y < min_y or box_w < 6 or box_h < 12:
            continue
        if box_w > width * 0.45 or box_h > height * 0.65:
            continue
        if box_h >= box_w * 1.35:
            card_x = x - 24
            card_y = y - 18
            card_w = max(150, min(230, box_w * 7))
            card_h = max(110, int(box_h * 1.35))
        else:
            card_w = 150
            card_h = 120
            card_x = x + box_w // 2 - card_w // 2
            card_y = y + box_h // 2 - card_h // 2
        x0 = max(0, min(card_x, width - 1))
        y0 = max(0, min(card_y, height - 1))
        x1 = max(0, min(card_x + card_w, width))
        y1 = max(0, min(card_y + card_h, height))
        if x1 <= x0 or y1 <= y0:
            continue
        candidate_bbox = (x0 + offset_x, y0 + offset_y, x1 - x0, y1 - y0)
        if _bbox_overlaps_any(candidate_bbox, exclude_bboxes or [], min_iou=0.18):
            continue
        if _bbox_overlaps_any(candidate_bbox, [item.bbox for item in regions], min_iou=0.35):
            continue
        confidence = round(min(0.88, max(0.42, area / 2400.0)), 3)
        regions.append(
            ObjectPresenceResult(
                candidate_bbox,
                (candidate_bbox[0] + candidate_bbox[2] / 2.0, candidate_bbox[1] + candidate_bbox[3] / 2.0),
                confidence,
            )
        )
        if len(regions) >= max(1, max_results):
            break
    return regions


def detect_object_presence_from_camera(
    *,
    device: int = 0,
    detector: str = "opencv",
    model_path: str = "",
    roi: object = None,
    min_area_ratio: float = 0.015,
) -> bool:
    cv2, _ = _load_cv2_only()
    try:
        frame = read_camera_frame(device, cv2)
    except CameraFrameError:
        return False
    return detect_object_presence(
        frame,
        cv2,
        roi,
        detector=detector,
        model_path=model_path,
        min_area_ratio=min_area_ratio,
    )


def _presence_roi(frame: Any, roi: object = None) -> Any:
    return _presence_roi_with_offset(frame, roi)[0]


def _presence_roi_with_offset(frame: Any, roi: object = None) -> tuple[Any, int, int]:
    if roi is None or roi == "":
        return frame, 0, 0
    try:
        height, width = frame.shape[:2]
        if isinstance(roi, str):
            parts = [float(part.strip()) for part in roi.replace(";", ",").split(",") if part.strip()]
        elif isinstance(roi, Mapping):
            parts = [float(roi[key]) for key in ("x", "y", "w", "h")]
        else:
            parts = [float(value) for value in roi]  # type: ignore[operator]
        if len(parts) != 4:
            return frame, 0, 0
        x, y, w, h = parts
        if all(0.0 <= value <= 1.0 for value in parts):
            x0 = int(x * width)
            y0 = int(y * height)
            x1 = int((x + w) * width)
            y1 = int((y + h) * height)
        else:
            x0 = int(x)
            y0 = int(y)
            x1 = int(x + w)
            y1 = int(y + h)
        x0, x1 = max(0, min(x0, width)), max(0, min(x1, width))
        y0, y1 = max(0, min(y0, height)), max(0, min(y1, height))
        if x1 <= x0 or y1 <= y0:
            return frame, 0, 0
        crop = frame[y0:y1, x0:x1]
        return (crop, x0, y0) if getattr(crop, "size", 0) else (frame, 0, 0)
    except Exception:
        return frame, 0, 0


def _detection_group_key(detection: Mapping[str, object]) -> str:
    tag_id = detection.get("tag_id")
    if tag_id is None:
        return "OBJ"
    return str(tag_id)


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

    return _hsv_to_color_name(h, s, v)


def _dominant_color_name_for_region(frame: Any, corners: Corners | None, cv2: Any) -> str | None:
    bbox = _corners_to_bbox(corners)
    if bbox is None:
        return None
    x, y, width, height = bbox
    try:
        frame_height, frame_width = frame.shape[:2]
        x0 = max(0, min(x, frame_width))
        y0 = max(0, min(y, frame_height))
        x1 = max(0, min(x + width, frame_width))
        y1 = max(0, min(y + height, frame_height))
        if x1 <= x0 or y1 <= y0:
            return None
        crop = frame[y0:y1, x0:x1]
        if getattr(crop, "size", 0) == 0:
            return None
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        saturated = hsv[(hsv[:, :, 1] >= 90) & (hsv[:, :, 2] >= 55)]
        if len(saturated) < 12:
            return None
        h, s, v = [float(value) for value in saturated.mean(axis=0)]
    except Exception:
        return None
    return _hsv_to_color_name(h, s, v)


def _hsv_to_color_name(h: float, s: float, v: float) -> str | None:
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
    if 39 <= h <= 96:
        return "GREEN"
    if 97 <= h <= 130:
        return "BLUE"
    if 131 <= h <= 165:
        return "PURPLE"
    if 11 <= h < 18:
        return "ORANGE"
    return None


def _class_with_ocr_semantics(image_class: ImageClassResult | None, ocr: OcrResult) -> ImageClassResult | None:
    text = ocr.text or ""
    normalized = text.upper()
    for keyword, class_name in _SEMANTIC_IMAGE_KEYWORDS:
        if keyword.upper() in normalized:
            confidence = ocr.confidence if ocr.confidence is not None else 0.72
            if confidence > 1.0:
                confidence = confidence / 100.0
            return ImageClassResult(class_name, round(max(0.4, min(float(confidence), 0.95)), 3), "ocr_keyword")
    return image_class


def _try_ocr_text(
    frame: Any,
    cv2: Any,
    *,
    center: Any = None,
    corners: Any = None,
    min_confidence: float = 55.0,
) -> OcrResult:
    paddle_result = _try_paddle_ocr_text(frame, cv2, center=center, corners=corners)
    if paddle_result.processed and (paddle_result.text is not None or paddle_result.confidence is not None):
        return paddle_result
    try:
        pytesseract = importlib.import_module("pytesseract")
    except ImportError:
        return OcrResult(None, None, False)

    try:
        roi = _ocr_roi(frame, center=center, corners=corners)
        roi = _rectify_card_roi(roi, cv2)
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


def _try_paddle_ocr_text(frame: Any, cv2: Any, *, center: Any = None, corners: Any = None) -> OcrResult:
    global _PADDLE_OCR, _PADDLE_OCR_UNAVAILABLE
    if _PADDLE_OCR_UNAVAILABLE:
        return OcrResult(None, None, False)
    try:
        module = importlib.import_module("paddleocr")
        paddle_ocr = getattr(module, "PaddleOCR")
    except ImportError:
        _PADDLE_OCR_UNAVAILABLE = True
        return OcrResult(None, None, False)
    try:
        roi = _ocr_roi(frame, center=center, corners=corners)
        roi = _rectify_card_roi(roi, cv2)
        rgb = cv2.cvtColor(roi, cv2.COLOR_BGR2RGB)
    except Exception:
        return OcrResult(None, None, False)
    try:
        with _PADDLE_OCR_LOCK:
            if _PADDLE_OCR is None:
                try:
                    _PADDLE_OCR = paddle_ocr(lang="ch", use_angle_cls=False, show_log=False)
                except TypeError:
                    _PADDLE_OCR = paddle_ocr(lang="ch")
            try:
                result = _PADDLE_OCR.ocr(rgb, cls=False)  # type: ignore[union-attr]
            except TypeError:
                result = _PADDLE_OCR.ocr(rgb)  # type: ignore[union-attr]
    except Exception:
        return OcrResult(None, None, False)
    text, confidence = _best_paddle_text(result)
    return OcrResult(text, confidence, True)


def _best_paddle_text(result: object) -> tuple[str | None, float | None]:
    candidates: list[tuple[str, float | None]] = []
    _collect_paddle_text_candidates(result, candidates)
    if not candidates:
        return None, None
    candidates.sort(key=lambda item: -1.0 if item[1] is None else item[1], reverse=True)
    text, confidence = candidates[0]
    return _clean_ocr_text(text), confidence


def _collect_paddle_text_candidates(value: object, candidates: list[tuple[str, float | None]]) -> None:
    if isinstance(value, str):
        cleaned = _clean_ocr_text(value)
        if cleaned:
            candidates.append((cleaned, None))
        return
    if isinstance(value, tuple) and value and isinstance(value[0], str):
        confidence = _parse_confidence(value[1]) if len(value) > 1 else None
        cleaned = _clean_ocr_text(value[0])
        if cleaned:
            candidates.append((cleaned, confidence))
        return
    if isinstance(value, list):
        if len(value) >= 2 and isinstance(value[1], tuple) and value[1] and isinstance(value[1][0], str):
            _collect_paddle_text_candidates(value[1], candidates)
            return
        for item in value:
            _collect_paddle_text_candidates(item, candidates)


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


def _rectify_card_roi(roi: Any, cv2: Any) -> Any:
    try:
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)
        edges = cv2.Canny(blurred, 60, 160)
        contours_result = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours = contours_result[-2]
        if not contours:
            return roi
        height, width = roi.shape[:2]
        min_area = max(64.0, float(width * height) * 0.08)
        for contour in sorted(contours, key=cv2.contourArea, reverse=True):
            area = float(cv2.contourArea(contour))
            if area < min_area:
                continue
            perimeter = cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, 0.035 * perimeter, True)
            if len(approx) != 4:
                continue
            points = approx.reshape(4, 2).astype("float32")
            ordered = _order_quad_points(points)
            target_width = max(
                int(_point_distance(ordered[0], ordered[1])),
                int(_point_distance(ordered[2], ordered[3])),
                1,
            )
            target_height = max(
                int(_point_distance(ordered[0], ordered[3])),
                int(_point_distance(ordered[1], ordered[2])),
                1,
            )
            destination = cv2.array(
                [[0, 0], [target_width - 1, 0], [target_width - 1, target_height - 1], [0, target_height - 1]],
                dtype="float32",
            ) if hasattr(cv2, "array") else None
            if destination is None:
                import numpy as np

                destination = np.array(
                    [[0, 0], [target_width - 1, 0], [target_width - 1, target_height - 1], [0, target_height - 1]],
                    dtype="float32",
                )
            matrix = cv2.getPerspectiveTransform(ordered, destination)
            return cv2.warpPerspective(roi, matrix, (target_width, target_height))
    except Exception:
        return roi
    return roi


def _classify_image_region(frame: Any, center: Any, corners: Any, cv2: Any) -> ImageClassResult | None:
    try:
        roi = _ocr_roi(frame, center=center, corners=corners)
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        contours_result = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours = contours_result[-2]
        if not contours:
            return None
        contour = max(contours, key=cv2.contourArea)
        area = float(cv2.contourArea(contour))
        if area < 80.0:
            return None
        perimeter = float(cv2.arcLength(contour, True))
        if perimeter <= 0:
            return None
        approx = cv2.approxPolyDP(contour, 0.04 * perimeter, True)
        x, y, width, height = cv2.boundingRect(contour)
        aspect = width / max(1.0, float(height))
        extent = area / max(1.0, float(width * height))
        circularity = 4.0 * math.pi * area / (perimeter * perimeter)
        if len(approx) <= 5 and extent >= 0.55 and 0.55 <= aspect <= 1.85:
            return ImageClassResult("CARD", round(min(0.88, extent), 3))
        if len(approx) >= 7 and circularity >= 0.72:
            return ImageClassResult("CYLINDER", round(min(0.95, circularity), 3))
        if extent >= 0.45:
            return ImageClassResult("BOX", round(min(0.82, extent), 3))
    except Exception:
        return None
    return None


def _order_quad_points(points: Any) -> Any:
    import numpy as np

    rect = np.zeros((4, 2), dtype="float32")
    sums = points.sum(axis=1)
    diffs = np.diff(points, axis=1)
    rect[0] = points[int(np.argmin(sums))]
    rect[2] = points[int(np.argmax(sums))]
    rect[1] = points[int(np.argmin(diffs))]
    rect[3] = points[int(np.argmax(diffs))]
    return rect


def _point_distance(first: Any, second: Any) -> float:
    return math.hypot(float(first[0]) - float(second[0]), float(first[1]) - float(second[1]))


def _clean_ocr_text(text: str) -> str | None:
    cleaned = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "", text).upper()
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


def _bbox_to_corners(bbox: tuple[int, int, int, int]) -> Corners:
    x, y, width, height = bbox
    return (
        (float(x), float(y)),
        (float(x + width), float(y)),
        (float(x + width), float(y + height)),
        (float(x), float(y + height)),
    )


def _corners_to_bbox(corners: Corners | None) -> tuple[int, int, int, int] | None:
    if corners is None:
        return None
    xs = [point[0] for point in corners]
    ys = [point[1] for point in corners]
    x0 = int(min(xs))
    y0 = int(min(ys))
    x1 = int(max(xs))
    y1 = int(max(ys))
    width = x1 - x0
    height = y1 - y0
    if width <= 0 or height <= 0:
        return None
    return x0, y0, width, height


def _bbox_overlaps_any(
    bbox: tuple[int, int, int, int],
    others: list[tuple[int, int, int, int]],
    *,
    min_iou: float,
) -> bool:
    return any(_bbox_iou(bbox, other) >= min_iou for other in others)


def _bbox_iou(first: tuple[int, int, int, int], second: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    ax1, ay1 = ax + aw, ay + ah
    bx1, by1 = bx + bw, by + bh
    inter_w = max(0, min(ax1, bx1) - max(ax, bx))
    inter_h = max(0, min(ay1, by1) - max(ay, by))
    inter_area = float(inter_w * inter_h)
    if inter_area <= 0:
        return 0.0
    union_area = float(aw * ah + bw * bh) - inter_area
    return 0.0 if union_area <= 0 else inter_area / union_area


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
