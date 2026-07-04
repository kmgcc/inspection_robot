from __future__ import annotations

import threading
from typing import Any

from .tag_detector_types import CameraFrameError


class SharedCameraCapture:
    def __init__(self, device: int, cv2: Any) -> None:
        self.device = int(device)
        self.cv2 = cv2
        self._lock = threading.Lock()
        self._capture: Any | None = None

    def isOpened(self) -> bool:
        try:
            capture = self._ensure_capture()
            return bool(capture.isOpened())
        except CameraFrameError:
            return False

    def read(self) -> tuple[bool, Any | None]:
        with self._lock:
            try:
                capture = self._ensure_capture_locked()
                ok, frame = capture.read()
                if ok and frame is not None:
                    return True, frame
                self._release_locked()
                capture = self._ensure_capture_locked()
                return capture.read()
            except CameraFrameError:
                return False, None

    def release(self) -> None:
        return

    def close(self) -> None:
        with self._lock:
            self._release_locked()

    def _ensure_capture(self) -> Any:
        with self._lock:
            return self._ensure_capture_locked()

    def _ensure_capture_locked(self) -> Any:
        if self._capture is not None and self._capture.isOpened():
            return self._capture
        capture = self.cv2.VideoCapture(self.device)
        if not capture.isOpened():
            try:
                capture.release()
            finally:
                self._capture = None
            raise CameraFrameError(f"camera device {self.device} could not be opened")
        self._capture = capture
        return capture

    def _release_locked(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None


_CAPTURE_LOCK = threading.Lock()
_CAPTURES: dict[int, SharedCameraCapture] = {}


def get_shared_capture(device: int, cv2: Any) -> SharedCameraCapture:
    key = int(device)
    with _CAPTURE_LOCK:
        capture = _CAPTURES.get(key)
        if capture is None or capture.cv2 is not cv2:
            capture = SharedCameraCapture(key, cv2)
            _CAPTURES[key] = capture
        return capture


def read_camera_frame(device: int, cv2: Any) -> Any:
    ok, frame = get_shared_capture(device, cv2).read()
    if not ok or frame is None:
        raise CameraFrameError(f"camera device {int(device)} did not return a frame")
    return frame


def release_shared_cameras() -> None:
    with _CAPTURE_LOCK:
        captures = list(_CAPTURES.values())
        _CAPTURES.clear()
    for capture in captures:
        capture.close()
