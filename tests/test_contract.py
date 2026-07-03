from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inspection_robot.web import create_app


CONTRACT_FIELDS = {
    "run_id",
    "task_status",
    "robot_status",
    "current_zone",
    "current_tag",
    "current_item",
    "last_message",
    "obstacle",
    "alarm",
    "zones",
    "events",
}

EXTENSION_FIELDS = {
    "current_shelf",
    "current_target",
    "pose",
    "path",
    "forbidden_zones",
    "shelves",
    "scan",
    "llm_summary",
}

EVENT_FIELDS = {
    "id",
    "time",
    "type",
    "tag_id",
    "item",
    "zone",
    "expected_zone",
    "priority",
    "status",
    "message",
}

EVENT_EXTENSION_FIELDS = {
    "shelf_id",
    "expected_shelf",
    "target",
    "source",
    "frame_id",
    "marker_family",
    "ocr_text",
    "color",
    "image_class",
    "evidence",
}


class ContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.tmp.name))
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_health_when_called_returns_ok(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True})

    def test_status_when_called_contains_contract_fields(self) -> None:
        response = self.client.get("/api/status")
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(CONTRACT_FIELDS.issubset(payload))
        self.assertTrue(EXTENSION_FIELDS.issubset(payload))
        self.assertTrue({"distance_mm", "blocked", "waiting_seconds"}.issubset(payload["obstacle"]))
        self.assertTrue({"level", "message", "light"}.issubset(payload["alarm"]))
        self.assertEqual(payload["scan"]["detections"], [])
        self.assertEqual(payload["path"]["waypoints"], [])

    def test_start_when_posted_returns_ok(self) -> None:
        response = self.client.post("/api/start")
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload, {"ok": True})

    def test_simulate_tag_when_started_records_event(self) -> None:
        self.client.post("/api/start")
        tag_response = self.client.post("/api/simulate/tag/1")
        status_response = self.client.get("/api/status")
        payload = status_response.get_json()

        self.assertEqual(tag_response.status_code, 200)
        self.assertEqual(status_response.status_code, 200)
        self.assertGreaterEqual(len(payload["events"]), 1)
        self.assertTrue(EVENT_FIELDS.issubset(payload["events"][0]))
        self.assertTrue(EVENT_EXTENSION_FIELDS.issubset(payload["events"][0]))
        self.assertEqual(payload["events"][0]["tag_id"], "1")


if __name__ == "__main__":
    unittest.main()
