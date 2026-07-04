from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


Point = tuple[float, float]
Corners = tuple[Point, Point, Point, Point]


@dataclass(slots=True)
class _Track:
    center: Point | None = None
    corners: Corners | None = None
    angle_deg: float | None = None
    stable_frames: int = 0
    emitted: bool = False


@dataclass(frozen=True, slots=True)
class StabilityConfig:
    min_stable_frames: int = 3
    max_center_shift_px: float = 10.0
    max_corner_shift_px: float = 14.0
    max_angle_delta_deg: float = 8.0


class DetectionStabilityTracker:
    def __init__(self, config: StabilityConfig | None = None) -> None:
        self.config = config or StabilityConfig()
        self._tracks: dict[str, _Track] = {}

    def update(self, detection: dict[str, object]) -> dict[str, object] | None:
        tag_id = str(detection.get("tag_id") or "").strip()
        if not tag_id:
            return None

        center = _point(detection.get("center"))
        corners = _corners(detection.get("corners"))
        angle = _float(detection.get("angle_deg"))
        track = self._tracks.get(tag_id)
        if track is None:
            track = _Track()
            self._tracks[tag_id] = track

        if self._is_same_target(track, center, corners, angle):
            track.stable_frames += 1
        else:
            track.stable_frames = 1
            track.emitted = False

        track.center = center
        track.corners = corners
        track.angle_deg = angle

        if track.stable_frames < max(1, int(self.config.min_stable_frames)):
            return None

        stable = dict(detection)
        stable["stable"] = True
        stable["stable_frames"] = track.stable_frames
        stable["processed"] = track.emitted
        track.emitted = True
        return stable

    def reset(self, tag_id: str | None = None) -> None:
        if tag_id is None:
            self._tracks.clear()
            return
        self._tracks.pop(str(tag_id), None)

    def _is_same_target(
        self,
        track: _Track,
        center: Point | None,
        corners: Corners | None,
        angle: float | None,
    ) -> bool:
        if track.center is None and track.corners is None and track.angle_deg is None:
            return True
        if center is not None and track.center is not None:
            if _distance(center, track.center) > self.config.max_center_shift_px:
                return False
        if corners is not None and track.corners is not None:
            max_shift = max(_distance(point, previous) for point, previous in zip(corners, track.corners))
            if max_shift > self.config.max_corner_shift_px:
                return False
        if angle is not None and track.angle_deg is not None:
            if abs(angle - track.angle_deg) > self.config.max_angle_delta_deg:
                return False
        return True


def _point(value: object) -> Point | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        return (float(value[0]), float(value[1]))
    except (TypeError, ValueError):
        return None


def _corners(value: object) -> Corners | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    points = [_point(point) for point in value]
    if any(point is None for point in points):
        return None
    return (points[0], points[1], points[2], points[3])  # type: ignore[return-value]


def _float(value: object) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _distance(first: Point, second: Point) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])
