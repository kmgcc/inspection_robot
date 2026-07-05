from __future__ import annotations

import base64
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import tag_detector
from .frame_source import read_camera_frame
from .tag_detector_types import CameraFrameError


_SIMULATED_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAYEBQYFBAYGBQYHBwYIChAKCgkJChQODwwQFxQYGBcUFhYaHSUfGhsjHBYWICwgIyYnKSopGR8tMC0oMCUoKSj/2wBDAQcHBwoIChMKChMoGhYaKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCj/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD6cooooA//2Q=="
)
_LATEST_LOCK = threading.Lock()
_LATEST: dict[str, object] = {
    "ok": True,
    "source": "simulate",
    "frame_id": "simulate",
    "detections": [],
    "error": None,
    "fps": None,
    "latency_ms": None,
    "updated_at": None,
}
_PIL_FONT: Any | None = None
_PIL_FONT_UNAVAILABLE = False
_FONT_PATHS = (
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
)
_COLOR_LABELS_CN = {
    "BLACK": "黑色",
    "WHITE": "白色",
    "GRAY": "灰色",
    "RED": "红色",
    "YELLOW": "黄色",
    "GREEN": "绿色",
    "BLUE": "蓝色",
    "PURPLE": "紫色",
    "ORANGE": "橙色",
}
_IMAGE_CLASS_LABELS_CN = {
    "CUP": "水杯",
    "BOTTLE": "瓶",
    "BOX": "盒",
    "CARD": "卡片",
    "CYLINDER": "圆柱",
    "MEDICINE_BOX": "药盒",
    "BATTERY": "电池",
}


def generate_mjpeg_frames(
    *,
    device: int = 0,
    fps: int = 8,
    width: int = 640,
    height: int = 360,
    simulate: bool = False,
    image_classifier_enabled: bool = False,
) -> Any:
    delay = 1.0 / max(1, int(fps))
    if simulate:
        _set_latest("simulate", "simulate", [], None, fps=None, latency_ms=None)
        while True:
            yield _mjpeg_chunk(_SIMULATED_JPEG)
            time.sleep(delay)

    try:
        cv2, Detector = tag_detector._load_vision_dependencies()
        detector = Detector(families="tag36h11")
    except tag_detector.VisionDependencyError as exc:
        _set_latest("camera", "error", [], str(exc), fps=None, latency_ms=None)
        while True:
            yield _mjpeg_chunk(_SIMULATED_JPEG)
            time.sleep(delay)

    frame_index = 0
    previous_frame_at: float | None = None
    while True:
        frame_index += 1
        frame_id = f"video-{frame_index}"
        started_at = time.monotonic()
        try:
            frame = read_camera_frame(device, cv2)
            if width > 0 and height > 0:
                frame = cv2.resize(frame, (int(width), int(height)))
            detections = tag_detector._detect_frame(
                frame,
                detector,
                cv2,
                image_classifier_enabled=image_classifier_enabled,
            )
            now = time.monotonic()
            measured_fps = None if previous_frame_at is None else round(1.0 / max(0.001, now - previous_frame_at), 2)
            previous_frame_at = now
            latency_ms = round((now - started_at) * 1000.0, 1)
            _set_latest("camera", frame_id, detections, None, fps=measured_fps, latency_ms=latency_ms)
            rendered = _draw_detections(frame, detections, cv2)
            ok, encoded = cv2.imencode(".jpg", rendered, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if not ok:
                raise CameraFrameError("failed to encode video frame")
            yield _mjpeg_chunk(encoded.tobytes())
        except (CameraFrameError, tag_detector.VisionDependencyError) as exc:
            _set_latest("camera", frame_id, [], str(exc), fps=None, latency_ms=None)
            yield _mjpeg_chunk(_SIMULATED_JPEG)
        time.sleep(delay)


def latest_video_detections(*, simulate: bool = False) -> dict[str, object]:
    if simulate:
        _set_latest("simulate", "simulate", [], None, fps=None, latency_ms=None)
    with _LATEST_LOCK:
        return dict(_LATEST)


def _set_latest(
    source: str,
    frame_id: str,
    detections: list[Mapping[str, object]],
    error: str | None,
    *,
    fps: float | None,
    latency_ms: float | None,
) -> None:
    payload = {
        "ok": error is None,
        "source": source,
        "frame_id": frame_id,
        "detections": [_json_safe(detection) for detection in detections],
        "error": error,
        "fps": fps,
        "latency_ms": latency_ms,
        "updated_at": time.time(),
    }
    with _LATEST_LOCK:
        _LATEST.clear()
        _LATEST.update(payload)


def _mjpeg_chunk(jpeg: bytes) -> bytes:
    return b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpeg)).encode("ascii") + b"\r\n\r\n" + jpeg + b"\r\n"


def _draw_detections(frame: Any, detections: list[Mapping[str, object]], cv2: Any) -> Any:
    rendered = frame.copy()
    for detection in detections:
        points = _detection_points(detection)
        if points:
            for index, point in enumerate(points):
                cv2.line(rendered, point, points[(index + 1) % len(points)], (0, 255, 255), 2)
            label_origin = points[0]
        else:
            bbox = _detection_bbox(detection)
            if bbox is not None:
                x, y, width, height = bbox
                cv2.rectangle(rendered, (x, y), (x + width, y + height), (0, 255, 255), 2)
                label_origin = (x, y)
            else:
                center = detection.get("center")
                if not isinstance(center, list) or len(center) < 2:
                    continue
                label_origin = (int(float(center[0])), int(float(center[1])))
                cv2.circle(rendered, label_origin, 8, (0, 255, 255), 2)
        label_lines = _detection_label_lines(detection)
        if not label_lines:
            continue
        x = max(4, min(label_origin[0], rendered.shape[1] - 120))
        y = max(18, label_origin[1] - 8)
        rendered = _draw_label_lines(rendered, label_lines, (x, y), cv2)
    return rendered


def _detection_points(detection: Mapping[str, object]) -> list[tuple[int, int]]:
    corners = detection.get("corners")
    if not isinstance(corners, list) or len(corners) != 4:
        return []
    points: list[tuple[int, int]] = []
    for point in corners:
        if not isinstance(point, list) or len(point) < 2:
            return []
        try:
            points.append((int(float(point[0])), int(float(point[1]))))
        except (TypeError, ValueError):
            return []
    return points


def _detection_bbox(detection: Mapping[str, object]) -> tuple[int, int, int, int] | None:
    bbox = detection.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        return None
    try:
        x, y, width, height = [int(float(value)) for value in bbox]
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return x, y, width, height


def _detection_label_lines(detection: Mapping[str, object]) -> list[str]:
    tag_id = detection.get("tag_id")
    source = detection.get("source")
    first = f"TAG {tag_id}" if tag_id not in (None, "") else "OBJECT"
    if source:
        first = f"{first} {source}"
    lines = [first]
    ocr_text = detection.get("ocr_text")
    color = detection.get("color")
    image_class = detection.get("image_class")
    if ocr_text:
        prefix = "文字" if _contains_non_ascii(str(ocr_text)) else "OCR"
        lines.append(f"{prefix} {ocr_text}")
    if color or image_class:
        use_chinese = _contains_non_ascii(str(ocr_text or ""))
        parts = []
        if color:
            parts.append(f"颜色 {_COLOR_LABELS_CN.get(str(color), str(color))}" if use_chinese else f"COLOR {color}")
        if image_class:
            parts.append(
                f"图像 {_IMAGE_CLASS_LABELS_CN.get(str(image_class), str(image_class))}"
                if use_chinese
                else f"IMG {image_class}"
            )
        lines.append(" ".join(parts))
    return lines


def _draw_label_lines(frame: Any, lines: list[str], origin: tuple[int, int], cv2: Any) -> Any:
    x, y = origin
    for line in lines:
        if _contains_non_ascii(line):
            rendered = _draw_label_lines_with_pil(frame, [line], (x, y), cv2)
            if rendered is not None:
                frame = rendered
            else:
                cv2.putText(frame, line, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (20, 20, 20), 3, cv2.LINE_AA)
                cv2.putText(frame, line, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
        else:
            cv2.putText(frame, line, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (20, 20, 20), 3, cv2.LINE_AA)
            cv2.putText(frame, line, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
        y += 18
    return frame


def _draw_label_lines_with_pil(frame: Any, lines: list[str], origin: tuple[int, int], cv2: Any) -> Any | None:
    font = _load_pil_font()
    if font is None:
        return None
    try:
        from PIL import Image, ImageDraw

        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(image)
        x, y = origin
        for line in lines:
            draw.text((x, y - 14), line, font=font, fill=(255, 230, 0), stroke_width=2, stroke_fill=(20, 20, 20))
            y += 19
        return cv2.cvtColor(_pil_to_array(image), cv2.COLOR_RGB2BGR)
    except Exception:
        return None


def _load_pil_font() -> Any | None:
    global _PIL_FONT, _PIL_FONT_UNAVAILABLE
    if _PIL_FONT is not None:
        return _PIL_FONT
    if _PIL_FONT_UNAVAILABLE:
        return None
    try:
        from PIL import ImageFont

        for font_path in _FONT_PATHS:
            if Path(font_path).exists():
                _PIL_FONT = ImageFont.truetype(font_path, 16)
                return _PIL_FONT
        _PIL_FONT = ImageFont.load_default()
        return _PIL_FONT
    except Exception:
        _PIL_FONT_UNAVAILABLE = True
        return None


def _pil_to_array(image: Any) -> Any:
    import numpy as np

    return np.asarray(image)


def _contains_non_ascii(value: str) -> bool:
    return any(ord(char) > 127 for char in value)


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)
