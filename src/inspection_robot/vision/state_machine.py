from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class VisionState(str, Enum):
    IDLE = "IDLE"
    SEARCHING = "SEARCHING"
    ALIGNING = "ALIGNING"
    CAPTURE = "CAPTURE"
    OCR = "OCR"
    VERIFY = "VERIFY"
    DONE = "DONE"


@dataclass(slots=True)
class VisionTransition:
    previous: VisionState
    current: VisionState
    reason: str


@dataclass(slots=True)
class VisionStateMachine:
    state: VisionState = VisionState.IDLE
    processed_tags: set[str] = field(default_factory=set)
    history: list[VisionTransition] = field(default_factory=list)

    def reset(self) -> None:
        self._set_state(VisionState.IDLE, "reset")
        self.processed_tags.clear()

    def step(self, detection: Mapping[str, object] | None = None) -> VisionState:
        if self.state is VisionState.IDLE:
            return self._set_state(VisionState.SEARCHING, "start_search")
        if self.state is VisionState.SEARCHING:
            if detection is None:
                return self.state
            return self._set_state(VisionState.ALIGNING, "target_seen")
        if self.state is VisionState.ALIGNING:
            if detection is None:
                return self._set_state(VisionState.SEARCHING, "target_lost")
            if _is_aligned(detection):
                return self._set_state(VisionState.CAPTURE, "target_aligned")
            return self.state
        if self.state is VisionState.CAPTURE:
            return self._set_state(VisionState.OCR, "frame_captured")
        if self.state is VisionState.OCR:
            return self._set_state(VisionState.VERIFY, "ocr_ready")
        if self.state is VisionState.VERIFY:
            tag_id = str(detection.get("tag_id")) if detection is not None and detection.get("tag_id") is not None else None
            if tag_id:
                self.processed_tags.add(tag_id)
            return self._set_state(VisionState.DONE, "verified")
        if self.state is VisionState.DONE:
            if detection is None:
                return self._set_state(VisionState.SEARCHING, "target_departed")
            return self.state
        return self._set_state(VisionState.SEARCHING, "fallback")

    def run_until_done(self, detection: Mapping[str, object]) -> VisionState:
        if self.state is VisionState.DONE:
            self.step(None)
        guard = 0
        while self.state is not VisionState.DONE and guard < 8:
            self.step(detection)
            guard += 1
        return self.state

    def _set_state(self, state: VisionState, reason: str) -> VisionState:
        if state is self.state:
            return self.state
        previous = self.state
        self.state = state
        self.history.append(VisionTransition(previous, state, reason))
        return self.state


def _is_aligned(detection: Mapping[str, object]) -> bool:
    if detection.get("stable") is True:
        return True
    center = detection.get("center")
    if isinstance(center, (list, tuple)) and len(center) >= 2:
        try:
            float(center[0])
            float(center[1])
            return True
        except (TypeError, ValueError):
            return False
    return detection.get("tag_id") is not None
