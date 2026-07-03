from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inspection_robot.core import rules
from inspection_robot.core.events import make_event


MANIFEST = {
    "A1": {"expected_items": ["item_01", "item_02", "item_03"]},
    "A2": {"expected_items": ["item_04"]},
}
TAG_MAP = {
    "1": {
        "name": "Apple",
        "kind": "item",
        "item_id": "item_01",
        "expected_shelf": "A1",
        "marker_family": "TAG36H11",
        "expected_color": "RED",
        "expected_ocr": "ITEM-01",
        "expected_image_class": "BOTTLE",
        "priority": 1,
        "zone": "A区",
        "expected_zone": "A区",
    },
    "2": {
        "name": "Box",
        "kind": "item",
        "item_id": "item_02",
        "expected_shelf": "A1",
        "marker_family": "TAG36H11",
        "expected_color": "BLUE",
        "expected_ocr": "ITEM-02",
        "expected_image_class": "BOX",
        "priority": 1,
        "zone": "A区",
        "expected_zone": "A区",
    },
    "3": {
        "name": "Tape",
        "kind": "item",
        "item_id": "item_03",
        "expected_shelf": "A1",
        "marker_family": "TAG36H11",
        "expected_color": "GREEN",
        "expected_ocr": "ITEM-03",
        "expected_image_class": "CUBE",
        "priority": 1,
        "zone": "A区",
        "expected_zone": "A区",
    },
    "4": {
        "name": "Medicine",
        "kind": "item",
        "item_id": "item_04",
        "expected_shelf": "A2",
        "marker_family": "TAG36H11",
        "expected_color": "YELLOW",
        "expected_ocr": "ITEM-04",
        "expected_image_class": "BOX",
        "priority": 2,
        "zone": "B区",
        "expected_zone": "B区",
    },
    "101": {
        "name": "A1",
        "kind": "shelf",
        "shelf_id": "A1",
        "marker_family": "TAG36H11",
        "ocr_label": "A1",
        "priority": 1,
        "zone": "A区",
        "expected_zone": "A区",
    },
}


class RulesTest(unittest.TestCase):
    def test_make_event_always_contains_contract_fields(self) -> None:
        event = make_event("system", message="ok", shelf_id="A1")

        self.assertIn("shelf_id", event)
        self.assertIn("source", event)
        self.assertIn("evidence", event)
        self.assertEqual(event["type"], "system")
        self.assertEqual(event["status"], "info")
        self.assertEqual(event["shelf_id"], "A1")

    def test_shelf_scan_returns_normal_events_for_complete_shelf(self) -> None:
        events = rules.evaluate_shelf_scan("A1", ["item_01", "item_02", "item_03"], MANIFEST, TAG_MAP)

        self.assertEqual([event["type"] for event in events], ["shelf_scanned"])
        self.assertEqual(events[0]["status"], "normal")
        self.assertEqual(events[0]["shelf_id"], "A1")

    def test_shelf_scan_reports_missing_duplicate_wrong_and_unknown_items(self) -> None:
        events = rules.evaluate_shelf_scan("A1", ["item_01", "item_02", "item_02", "item_04", "item_99"], MANIFEST, TAG_MAP)
        event_types = [event["type"] for event in events]

        self.assertIn("missing_item", event_types)
        self.assertIn("duplicate_item", event_types)
        self.assertIn("wrong_shelf", event_types)
        self.assertIn("unknown_item", event_types)
        self.assertTrue(all(event["status"] == "waiting_confirm" for event in events))

    def test_empty_shelf_scan_reports_scan_failed_when_missing_detection_is_enabled(self) -> None:
        events = rules.evaluate_shelf_scan("A1", [], MANIFEST, TAG_MAP, frame_id="empty-1")

        self.assertEqual([event["type"] for event in events], ["scan_failed"])
        self.assertEqual(events[0]["status"], "waiting_confirm")
        self.assertEqual(events[0]["frame_id"], "empty-1")

    def test_detection_evidence_reports_mismatched_item_evidence(self) -> None:
        events = rules.evaluate_detection_evidence(
            "A1",
            [{"tag_id": "1", "marker_family": "TAG36H11", "color": "BLUE", "ocr_text": "ITEM-01"}],
            MANIFEST,
            TAG_MAP,
        )

        self.assertIn("evidence_mismatch", [event["type"] for event in events])
        mismatch = next(event for event in events if event["type"] == "evidence_mismatch")
        self.assertEqual(mismatch["tag_id"], "1")
        self.assertEqual(mismatch["color"], "BLUE")

    def test_detection_evidence_preserves_untagged_visual_evidence(self) -> None:
        events = rules.evaluate_detection_evidence(
            "A1",
            [{"ocr_text": "ITEM-01", "color": "RED", "image_class": "BOTTLE", "confidence": 0.72}],
            MANIFEST,
            TAG_MAP,
            frame_id="untagged-1",
        )

        self.assertEqual([event["type"] for event in events], ["untagged_evidence"])
        self.assertEqual(events[0]["status"], "waiting_confirm")
        self.assertEqual(events[0]["ocr_text"], "ITEM-01")
        self.assertEqual(events[0]["evidence"], {"ocr_text": "ITEM-01", "color": "RED", "image_class": "BOTTLE", "confidence": 0.72})

    def test_detection_evidence_reports_shelf_ocr_mismatch(self) -> None:
        events = rules.evaluate_detection_evidence(
            "A1",
            [{"tag_id": "101", "marker_family": "TAG36H11", "ocr_text": "A2"}],
            MANIFEST,
            TAG_MAP,
        )

        self.assertEqual(events[0]["type"], "evidence_mismatch")
        self.assertEqual(events[0]["shelf_id"], "A1")


if __name__ == "__main__":
    unittest.main()
