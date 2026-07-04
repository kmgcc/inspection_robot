from __future__ import annotations

import sys
import unittest
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inspection_robot.vision import tag_detector


class VisionDetectorTest(unittest.TestCase):
    def test_detect_frame_includes_metadata_from_dt_apriltag_like_detection(self) -> None:
        frame = np.zeros((120, 120, 3), dtype=np.uint8)
        raw = FakeRawDetection(
            tag_id=101,
            center=(60.0, 50.0),
            corners=((40.0, 30.0), (80.0, 30.0), (80.0, 70.0), (40.0, 70.0)),
            decision_margin=82.0,
            hamming=0,
            goodness=0.91,
        )

        detections = tag_detector._detect_frame(frame, FakeDetector([raw]), cv2)

        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0]["tag_id"], "101")
        self.assertEqual(detections[0]["center"], [60.0, 50.0])
        self.assertEqual(detections[0]["corners"], [[40.0, 30.0], [80.0, 30.0], [80.0, 70.0], [40.0, 70.0]])
        self.assertEqual(detections[0]["angle_deg"], 0.0)
        self.assertEqual(detections[0]["hamming"], 0)
        self.assertEqual(detections[0]["goodness"], 0.91)
        self.assertFalse(detections[0]["processed"])
        self.assertIn("confidence", detections[0])

    def test_opencv_fallback_detection_carries_corners_and_angle(self) -> None:
        detection = tag_detector._OpenCVArucoDetection(
            tag_id=7,
            center=(10.0, 12.0),
            corners=((5.0, 6.0), (15.0, 6.0), (15.0, 18.0), (5.0, 18.0)),
            angle_deg=0.0,
        )

        self.assertEqual(detection.corners[2], (15.0, 18.0))
        self.assertEqual(detection.angle_deg, 0.0)

    def test_hsv_color_detection_handles_primary_colors_and_uncertain_neutral(self) -> None:
        red = np.full((40, 40, 3), (0, 0, 255), dtype=np.uint8)
        green = np.full((40, 40, 3), (0, 255, 0), dtype=np.uint8)
        low_saturation = np.full((40, 40, 3), (215, 218, 220), dtype=np.uint8)

        self.assertEqual(tag_detector._dominant_color_name(red, None, cv2), "RED")
        self.assertEqual(tag_detector._dominant_color_name(green, None, cv2), "GREEN")
        self.assertIsNone(tag_detector._dominant_color_name(low_saturation, None, cv2))

    def test_ocr_result_rejects_low_confidence_without_guessing(self) -> None:
        frame = np.full((80, 120, 3), 255, dtype=np.uint8)
        fake_module = FakeTesseractModule({"text": ["ITEM-01"], "conf": ["42"]})
        previous = sys.modules.get("pytesseract")
        sys.modules["pytesseract"] = fake_module
        try:
            result = tag_detector._try_ocr_text(
                frame,
                cv2,
                center=(60.0, 40.0),
                corners=((40.0, 30.0), (80.0, 30.0), (80.0, 50.0), (40.0, 50.0)),
                min_confidence=60.0,
            )
        finally:
            if previous is None:
                del sys.modules["pytesseract"]
            else:
                sys.modules["pytesseract"] = previous

        self.assertIsNone(result.text)
        self.assertEqual(result.confidence, 42.0)


class FakeRawDetection:
    def __init__(
        self,
        *,
        tag_id: int,
        center: tuple[float, float],
        corners: tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]],
        decision_margin: float,
        hamming: int,
        goodness: float,
    ) -> None:
        self.tag_id = tag_id
        self.center = center
        self.corners = np.array(corners, dtype=np.float32)
        self.decision_margin = decision_margin
        self.hamming = hamming
        self.goodness = goodness


class FakeDetector:
    def __init__(self, detections: list[FakeRawDetection]) -> None:
        self.detections = detections

    def detect(self, _: np.ndarray) -> list[FakeRawDetection]:
        return self.detections


class FakeTesseractModule(ModuleType):
    def __init__(self, data: Mapping[str, list[str]]) -> None:
        super().__init__("pytesseract")
        self.data = data

    def image_to_data(self, *_: object, **__: object) -> Mapping[str, list[str]]:
        return self.data

    def image_to_string(self, *_: object, **__: object) -> str:
        return "ITEM-01"
