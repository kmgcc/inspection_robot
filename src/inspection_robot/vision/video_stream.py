from __future__ import annotations

import base64
import threading
import time
from collections.abc import Mapping
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
}


def generate_mjpeg_frames(
    *,
    device: int = 0,
    fps: int = 8,
    width: int = 640,
    height: int = 360,
    simulate: bool = False,
) -> Any:
    delay = 1.0 / max(1, int(fps))
    if simulate:
        _set_latest("simulate", "simulate", [], None)
        while True:
            yield _mjpeg_chunk(_SIMULATED_JPEG)
            time.sleep(delay)

    try:
        cv2, Detector = tag_detector._load_vision_dependencies()
        detector = Detector(families="tag36h11")
    except tag_detector.VisionDependencyError as exc:
        _set_latest("camera", "error", [], str(exc))
        while True:
            yield _mjpeg_chunk(_SIMULATED_JPEG)
            time.sleep(delay)

    frame_index = 0
    while True:
        frame_index += 1
        frame_id = f"video-{frame_index}"
        try:
            frame = read_camera_frame(device, cv2)
            if width > 0 and height > 0:
                frame = cv2.resize(frame, (int(width), int(height)))
            detections = tag_detector._detect_frame(frame, detector, cv2)
            _set_latest("camera", frame_id, detections, None)
            rendered = _draw_detections(frame, detections, cv2)
            ok, encoded = cv2.imencode(".jpg", rendered, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if not ok:
                raise CameraFrameError("failed to encode video frame")
            yield _mjpeg_chunk(encoded.tobytes())
        except (CameraFrameError, tag_detector.VisionDependencyError) as exc:
            _set_latest("camera", frame_id, [], str(exc))
            yield _mjpeg_chunk(_SIMULATED_JPEG)
        time.sleep(delay)


def latest_video_detections(*, simulate: bool = False) -> dict[str, object]:
    if simulate:
        _set_latest("simulate", "simulate", [], None)
    with _LATEST_LOCK:
        return dict(_LATEST)


def _set_latest(source: str, frame_id: str, detections: list[Mapping[str, object]], error: str | None) -> None:
    payload = {
        "ok": error is None,
        "source": source,
        "frame_id": frame_id,
        "detections": [_json_safe(detection) for detection in detections],
        "error": error,
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
            center = detection.get("center")
            if not isinstance(center, list) or len(center) < 2:
                continue
            label_origin = (int(float(center[0])), int(float(center[1])))
            cv2.circle(rendered, label_origin, 8, (0, 255, 255), 2)
        label = f"{detection.get('tag_id', '-')}"
        if detection.get("ocr_text"):
            label = f"{label} {detection['ocr_text']}"
        label_y = max(18, label_origin[1] - 8)
        cv2.putText(rendered, label, (label_origin[0], label_y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (20, 20, 20), 3, cv2.LINE_AA)
        cv2.putText(rendered, label, (label_origin[0], label_y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
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


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)
