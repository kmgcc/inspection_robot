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
    "motion_sensor",
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

    def test_simulate_tag_rejects_non_numeric_or_overlong_ids(self) -> None:
        bad_alpha = self.client.post("/api/simulate/tag/not-a-number")
        bad_long = self.client.post("/api/simulate/tag/12345678901")
        payload = self.client.get("/api/status").get_json()

        self.assertEqual(bad_alpha.status_code, 400)
        self.assertEqual(bad_long.status_code, 400)
        self.assertEqual(payload["events"], [])

    def test_export_csv_defaults_to_utf8_without_bom_and_can_opt_in(self) -> None:
        self.client.post("/api/start")
        self.client.post("/api/simulate/tag/1")

        plain = self.client.get("/api/export.csv")
        with_bom = self.client.get("/api/export.csv?bom=1")

        self.assertFalse(plain.data.startswith(b"\xef\xbb\xbf"))
        self.assertTrue(with_bom.data.startswith(b"\xef\xbb\xbf"))

    def test_dashboard_demo_routes_cover_path_obstacle_forbidden_and_scans(self) -> None:
        path = self.client.post("/api/demo/path")
        self.assertEqual(path.status_code, 200)
        payload = self.client.get("/api/status").get_json()
        self.assertEqual(payload["task_status"], "PLAN_READY")
        self.assertGreater(len(payload["path"]["waypoints"]), 0)

        obstacle = self.client.post("/api/demo/obstacle")
        self.assertEqual(obstacle.status_code, 200)
        payload = self.client.get("/api/status").get_json()
        self.assertEqual(payload["task_status"], "OBSTACLE_WAIT")
        self.assertTrue(payload["obstacle"]["blocked"])

        obstacle_clear = self.client.post("/api/demo/obstacle/clear")
        self.assertEqual(obstacle_clear.status_code, 200)
        payload = self.client.get("/api/status").get_json()
        self.assertEqual(payload["task_status"], "PATROLLING")
        self.assertFalse(payload["obstacle"]["blocked"])

        forbidden = self.client.post("/api/demo/forbidden")
        self.assertEqual(forbidden.status_code, 200)
        payload = self.client.get("/api/status").get_json()
        self.assertEqual(payload["task_status"], "FORBIDDEN_ZONE_WAIT")
        self.assertTrue(any(zone.get("blocked") for zone in payload["forbidden_zones"]))

        forbidden_clear = self.client.post("/api/demo/forbidden/clear")
        self.assertEqual(forbidden_clear.status_code, 200)
        payload = self.client.get("/api/status").get_json()
        self.assertEqual(payload["task_status"], "MOVING")

        normal = self.client.post("/api/demo/scan/A1/normal")
        self.assertEqual(normal.status_code, 200)
        payload = self.client.get("/api/status").get_json()
        self.assertEqual(payload["current_shelf"], "A1")
        self.assertGreater(len(payload["scan"]["detections"]), 0)
        self.assertEqual(payload["events"][0]["type"], "shelf_scanned")
        self.assertIn("shelf_aligned", {event["type"] for event in payload["events"]})

        abnormal = self.client.post("/api/demo/scan/A2/abnormal")
        self.assertEqual(abnormal.status_code, 200)
        payload = self.client.get("/api/status").get_json()
        event_types = {event["type"] for event in payload["events"]}
        self.assertIn("shelf_aligned", event_types)
        self.assertIn("missing_item", event_types)
        self.assertIn("duplicate_item", event_types)
        self.assertIn("wrong_shelf", event_types)
        self.assertTrue(any(event["status"] == "waiting_confirm" for event in payload["events"]))

    def test_evidence_mismatch_and_demo_run_are_available_without_car(self) -> None:
        mismatch = self.client.post("/api/demo/evidence-mismatch")
        self.assertEqual(mismatch.status_code, 200)
        payload = self.client.get("/api/status").get_json()
        self.assertGreater(len(payload["scan"]["detections"]), 0)
        self.assertTrue(any(event["type"] == "evidence_mismatch" for event in payload["events"]))

        demo = self.client.post("/api/demo/run")
        self.assertEqual(demo.status_code, 200)
        self.assertGreater(demo.get_json()["confirmed_count"], 0)
        payload = self.client.get("/api/status").get_json()
        event_types = {event["type"] for event in payload["events"]}

        self.assertEqual(payload["task_status"], "FINISHED")
        self.assertIn("path_planned", event_types)
        self.assertIn("obstacle_wait", event_types)
        self.assertIn("forbidden_zone_detected", event_types)
        self.assertIn("shelf_scanned", event_types)
        self.assertIn("evidence_mismatch", event_types)
        self.assertIn("manual_confirm", event_types)
        self.assertFalse(any(event["status"] == "waiting_confirm" for event in payload["events"]))

    def test_video_routes_are_available_in_simulate_mode(self) -> None:
        template = (ROOT / "src" / "inspection_robot" / "templates" / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn("/api/video_feed", template)

        detections = self.client.get("/api/video/detections")
        self.assertEqual(detections.status_code, 200)
        payload = detections.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source"], "simulate")
        self.assertIn("detections", payload)
        self.assertIn("fps", payload)
        self.assertIn("latency_ms", payload)
        self.assertIn("updated_at", payload)

        feed = self.client.get("/api/video_feed", buffered=False)
        self.assertEqual(feed.status_code, 200)
        self.assertEqual(feed.mimetype, "multipart/x-mixed-replace")
        first_chunk = next(feed.response)
        self.assertIn(b"--frame", first_chunk)
        feed.close()

    def test_robot_calibration_turn_uses_payload_and_disables_backward_control(self) -> None:
        runtime = FakeRuntime()
        self.app.config["RUN_MODE"] = "robot"
        self.app.config["ROBOT_RUNTIME"] = runtime

        calibration = self.client.post(
            "/api/calibration/turn_90",
            json={"direction": "right", "speed": 17, "duration_seconds": 0.82},
        )
        self.assertEqual(calibration.status_code, 200)
        self.assertEqual(calibration.get_json()["duration_seconds"], 0.82)
        self.assertIn(("rotate_right", 17, 0.82), runtime.motion.calls)
        self.assertIn("settle", runtime.calls)

        backward = self.client.post("/api/control/backward", json={"speed": 20, "duration_seconds": 0.2})
        self.assertEqual(backward.status_code, 200)
        self.assertIn(("move_backward", 20, 0.2), runtime.motion.calls)

        manual_turn = self.client.post("/api/control/turn_right_90", json={"speed": 20, "duration_seconds": 0.2})
        self.assertEqual(manual_turn.status_code, 200)
        self.assertEqual(runtime.turns[-1], ("right", 22, 0.85))

    def test_manual_forward_uses_runtime_heading_held_forward(self) -> None:
        runtime = FakeRuntime()
        self.app.config["RUN_MODE"] = "robot"
        self.app.config["ROBOT_RUNTIME"] = runtime

        response = self.client.post("/api/control/forward", json={"speed": 20, "duration_seconds": 0.2})

        self.assertEqual(response.status_code, 200)
        self.assertIn(("zupt", "manual_forward_start"), runtime.calls)
        self.assertIn("reset_heading", runtime.calls)
        self.assertIn(("forward_step", 20, 0.2), runtime.calls)
        self.assertNotIn(("move_forward", 20, 0.2), runtime.motion.calls)

    def test_cycle_confirm_delegates_to_runtime_fallback_confirmation(self) -> None:
        runtime = FakeRuntime()
        self.app.config["RUN_MODE"] = "robot"
        self.app.config["ROBOT_RUNTIME"] = runtime

        response = self.client.post("/api/cycle/confirm")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["patrol_cycle"], 3)
        self.assertIn("confirm_cycle", runtime.calls)


class FakeRuntime:
    def __init__(self) -> None:
        self.motion = FakeMotion()
        self.config = type(
            "Config",
            (),
            {
                "patrol_speed": 22,
                "step_seconds": 0.14,
                "turn_speed": 18,
                "turn_90_seconds": 0.75,
                "action_settle_seconds": 0,
            },
        )()
        self.calls: list[object] = []
        self.turns: list[tuple[str, int | None, float | None]] = []

    def stop(self) -> None:
        self.calls.append("stop")

    def _forward_step(self, *, speed: int, duration_seconds: float, settle_seconds: float | None = None) -> None:
        self.calls.append(("forward_step", speed, duration_seconds))

    def _zupt_recalibrate(self, reason: str) -> None:
        self.calls.append(("zupt", reason))

    def _reset_heading_guard(self) -> None:
        self.calls.append("reset_heading")

    def _settle(self) -> None:
        self.calls.append("settle")

    def turn_90_closed_loop(self, direction: str, *, speed: int | None = None, duration_seconds: float | None = None) -> None:
        self.turns.append((direction, speed, duration_seconds))

    def confirm_camera_cycle_fallback(self) -> int:
        self.calls.append("confirm_cycle")
        return 3


class FakeMotion:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None, float | None]] = []

    def move_forward_slow(self, *, speed: int, duration_seconds: float) -> None:
        self.calls.append(("move_forward", speed, duration_seconds))

    def move_backward_slow(self, *, speed: int, duration_seconds: float) -> None:
        self.calls.append(("move_backward", speed, duration_seconds))

    def rotate_left_slow(self, *, speed: int, duration_seconds: float) -> None:
        self.calls.append(("rotate_left", speed, duration_seconds))

    def rotate_right_slow(self, *, speed: int, duration_seconds: float) -> None:
        self.calls.append(("rotate_right", speed, duration_seconds))

    def stop(self) -> None:
        self.calls.append(("stop", None, None))


if __name__ == "__main__":
    unittest.main()
