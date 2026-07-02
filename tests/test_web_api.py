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
    "current_shelf",
    "current_target",
    "pose",
    "path",
    "forbidden_zones",
    "shelves",
    "scan",
    "llm_summary",
}


class WebApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.app = create_app(self.root)
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_health_start_simulate_confirm_and_export_flow(self) -> None:
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.get_json(), {"ok": True})

        start = self.client.post("/api/start")
        self.assertEqual(start.status_code, 200)
        self.assertEqual(start.get_json(), {"ok": True})

        normal = self.client.post("/api/simulate/tag/1")
        self.assertEqual(normal.status_code, 200)
        status = self.client.get("/api/status")
        payload = status.get_json()
        self.assertTrue(CONTRACT_FIELDS.issubset(payload))
        self.assertEqual(payload["events"][0]["type"], "normal_item")
        self.assertEqual(payload["events"][0]["tag_id"], "1")

        wrong = self.client.post("/api/simulate/tag/4")
        self.assertEqual(wrong.status_code, 200)
        payload = self.client.get("/api/status").get_json()
        self.assertEqual(payload["events"][0]["type"], "wrong_shelf")
        self.assertEqual(payload["events"][0]["status"], "waiting_confirm")

        confirm = self.client.post("/api/confirm")
        self.assertEqual(confirm.status_code, 200)
        self.assertEqual(confirm.get_json(), {"ok": True, "confirmed": True})
        payload = self.client.get("/api/status").get_json()
        self.assertEqual(payload["task_status"], "CONFIRMED")
        self.assertEqual(payload["events"][0]["type"], "manual_confirm")
        self.assertEqual(payload["events"][1]["status"], "confirmed")

        export = self.client.get("/api/export.csv")
        self.assertEqual(export.status_code, 200)
        csv_text = export.data.decode("utf-8-sig")
        self.assertEqual(
            csv_text.splitlines()[0],
            "事件ID,时间,类型,标签ID,物品,区域,货架,期望货架,颜色,OCR,图像类别,优先级,状态,来源,说明",
        )


if __name__ == "__main__":
    unittest.main()
