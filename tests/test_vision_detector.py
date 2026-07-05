from __future__ import annotations

import sys
import unittest
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType

try:
    import cv2
    import numpy as np
except ImportError as exc:  # pragma: no cover - depends on local optional wheels
    raise unittest.SkipTest(f"optional OpenCV/numpy vision tests skipped: {exc}") from exc


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inspection_robot.vision import tag_detector
from inspection_robot.vision.stability import DetectionStabilityTracker, StabilityConfig
from inspection_robot.vision.state_machine import VisionState, VisionStateMachine
from inspection_robot.vision.video_stream import _draw_detections


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

    def test_stability_tracker_accepts_stable_frames_and_marks_repeats(self) -> None:
        tracker = DetectionStabilityTracker(
            StabilityConfig(
                min_stable_frames=3,
                max_center_shift_px=5.0,
                max_corner_shift_px=5.0,
                max_angle_delta_deg=4.0,
            )
        )
        detection = {
            "tag_id": "101",
            "center": [60.0, 50.0],
            "corners": [[40.0, 30.0], [80.0, 30.0], [80.0, 70.0], [40.0, 70.0]],
            "angle_deg": 0.0,
        }

        self.assertIsNone(tracker.update(dict(detection)))
        self.assertIsNone(tracker.update({**detection, "center": [61.0, 50.5]}))
        stable = tracker.update({**detection, "center": [61.5, 51.0]})
        repeat = tracker.update({**detection, "center": [61.8, 51.1]})

        self.assertIsNotNone(stable)
        self.assertEqual(stable["stable_frames"], 3)  # type: ignore[index]
        self.assertFalse(stable["processed"])  # type: ignore[index]
        self.assertTrue(repeat["processed"])  # type: ignore[index]

    def test_stability_tracker_filters_large_jitter(self) -> None:
        tracker = DetectionStabilityTracker(StabilityConfig(min_stable_frames=2, max_center_shift_px=3.0))

        self.assertIsNone(tracker.update({"tag_id": "101", "center": [10.0, 10.0]}))
        self.assertIsNone(tracker.update({"tag_id": "101", "center": [40.0, 10.0]}))

    def test_vision_state_machine_reaches_done_without_driving_motion(self) -> None:
        machine = VisionStateMachine()
        detection = {"tag_id": "101", "center": [60.0, 50.0], "stable": True}

        state = machine.run_until_done(detection)

        self.assertEqual(state, VisionState.DONE)
        self.assertIn("101", machine.processed_tags)
        self.assertEqual(machine.history[0].current, VisionState.SEARCHING)

    def test_optional_image_classifier_detects_simple_card_shape(self) -> None:
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        cv2.rectangle(frame, (25, 30), (75, 70), (255, 255, 255), -1)

        result = tag_detector._classify_image_region(
            frame,
            center=(50.0, 50.0),
            corners=((30.0, 30.0), (70.0, 30.0), (70.0, 70.0), (30.0, 70.0)),
            cv2=cv2,
        )

        self.assertIsNotNone(result)
        self.assertIn(result.class_name, {"CARD", "BOX"})

    def test_object_presence_detects_simple_card_without_model(self) -> None:
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        cv2.rectangle(frame, (50, 35), (110, 85), (255, 255, 255), -1)

        self.assertTrue(tag_detector.detect_object_presence(frame, cv2, detector="opencv"))
        self.assertTrue(tag_detector.detect_object_presence(frame, cv2, detector="yolov5_lite_cpu", model_path=""))

    def test_detect_frame_emits_synthetic_evidence_without_apriltag(self) -> None:
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        cv2.rectangle(frame, (50, 35), (110, 85), (255, 255, 255), -1)
        original = tag_detector._try_ocr_text
        tag_detector._try_ocr_text = lambda *_args, **_kwargs: tag_detector.OcrResult("A1-中文", 91.0, True)  # type: ignore[assignment]
        try:
            detections = tag_detector._detect_frame(
                frame,
                FakeDetector([]),
                cv2,
                image_classifier_enabled=True,
                ocr_enabled=True,
                color_enabled=True,
            )
        finally:
            tag_detector._try_ocr_text = original  # type: ignore[assignment]

        self.assertEqual(len(detections), 1)
        self.assertIsNone(detections[0]["tag_id"])
        self.assertEqual(detections[0]["source"], "synthetic_untagged")
        self.assertEqual(detections[0]["ocr_text"], "A1-中文")
        bbox = detections[0]["bbox"]
        self.assertLessEqual(bbox[0], 50)
        self.assertLessEqual(bbox[1], 35)
        self.assertGreaterEqual(bbox[0] + bbox[2], 110)
        self.assertGreaterEqual(bbox[1] + bbox[3], 85)
        self.assertAlmostEqual(detections[0]["center"][0], bbox[0] + bbox[2] / 2.0)
        self.assertAlmostEqual(detections[0]["center"][1], bbox[1] + bbox[3] / 2.0)
        self.assertIsNotNone(detections[0]["corners"])
        self.assertIsNotNone(detections[0]["image_class"])

    def test_detect_frame_keeps_untagged_card_when_apriltag_is_present(self) -> None:
        frame = np.zeros((140, 220, 3), dtype=np.uint8)
        cv2.rectangle(frame, (20, 35), (105, 105), (255, 255, 255), -1)
        cv2.rectangle(frame, (25, 40), (42, 100), (0, 190, 0), -1)
        raw = FakeRawDetection(
            tag_id=101,
            center=(170.0, 70.0),
            corners=((145.0, 45.0), (195.0, 45.0), (195.0, 95.0), (145.0, 95.0)),
            decision_margin=82.0,
            hamming=0,
            goodness=0.91,
        )
        original = tag_detector._try_ocr_text
        tag_detector._try_ocr_text = lambda *_args, **_kwargs: tag_detector.OcrResult("水杯物品20", 0.92, True)  # type: ignore[assignment]
        try:
            detections = tag_detector._detect_frame(
                frame,
                FakeDetector([raw]),
                cv2,
                image_classifier_enabled=True,
                ocr_enabled=True,
                color_enabled=True,
            )
        finally:
            tag_detector._try_ocr_text = original  # type: ignore[assignment]

        synthetic = [item for item in detections if item.get("source") == "synthetic_untagged"]
        self.assertGreaterEqual(len(synthetic), 1)
        self.assertEqual(synthetic[0]["ocr_text"], "水杯物品20")
        self.assertEqual(synthetic[0]["image_class"], "CUP")
        self.assertEqual(synthetic[0]["color"], "GREEN")

    def test_snapshot_detection_can_keep_all_single_frame_tags(self) -> None:
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        raws = [
            FakeRawDetection(
                tag_id=118,
                center=(40.0, 50.0),
                corners=((20.0, 30.0), (60.0, 30.0), (60.0, 70.0), (20.0, 70.0)),
                decision_margin=82.0,
                hamming=0,
                goodness=0.91,
            ),
            FakeRawDetection(
                tag_id=7,
                center=(110.0, 50.0),
                corners=((90.0, 30.0), (130.0, 30.0), (130.0, 70.0), (90.0, 70.0)),
                decision_margin=80.0,
                hamming=0,
                goodness=0.9,
            ),
        ]

        detections = tag_detector._read_stable_detections(
            FakeCapture([frame]),
            FakeDetector(raws),
            cv2,
            vote_frames=1,
            require_consensus=False,
        )

        self.assertEqual({item["tag_id"] for item in detections}, {"118", "7"})

    def test_video_overlay_draws_bbox_and_multimodal_labels(self) -> None:
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        detection = {
            "bbox": [30, 25, 70, 45],
            "ocr_text": "水杯物品20",
            "color": "BLUE",
            "image_class": "CUP",
            "source": "synthetic_untagged",
        }

        rendered = _draw_detections(frame, [detection], cv2)

        self.assertGreater(int(rendered.sum()), int(frame.sum()))

    def test_ocr_cleaning_keeps_chinese_letters_digits_and_dashes(self) -> None:
        self.assertEqual(tag_detector._clean_ocr_text(" 库位-a1_02! "), "库位-A1_02")

    def test_ocr_falls_back_to_tesseract_when_paddle_unavailable(self) -> None:
        frame = np.full((80, 120, 3), 255, dtype=np.uint8)
        fake_module = FakeTesseractModule({"text": ["ITEM-01"], "conf": ["92"]})
        previous_tesseract = sys.modules.get("pytesseract")
        previous_paddle_unavailable = tag_detector._PADDLE_OCR_UNAVAILABLE
        tag_detector._PADDLE_OCR_UNAVAILABLE = True
        sys.modules["pytesseract"] = fake_module
        try:
            result = tag_detector._try_ocr_text(frame, cv2)
        finally:
            tag_detector._PADDLE_OCR_UNAVAILABLE = previous_paddle_unavailable
            if previous_tesseract is None:
                del sys.modules["pytesseract"]
            else:
                sys.modules["pytesseract"] = previous_tesseract

        self.assertEqual(result.text, "ITEM-01")
        self.assertEqual(result.confidence, 92.0)


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


class FakeCapture:
    def __init__(self, frames: list[np.ndarray]) -> None:
        self.frames = list(frames)

    def read(self) -> tuple[bool, np.ndarray | None]:
        if not self.frames:
            return False, None
        return True, self.frames.pop(0)


class FakeTesseractModule(ModuleType):
    def __init__(self, data: Mapping[str, list[str]]) -> None:
        super().__init__("pytesseract")
        self.data = data

    def image_to_data(self, *_: object, **__: object) -> Mapping[str, list[str]]:
        return self.data

    def image_to_string(self, *_: object, **__: object) -> str:
        return "ITEM-01"
