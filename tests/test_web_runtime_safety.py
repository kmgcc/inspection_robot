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


class WebRuntimeSafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.app = create_app(self.root)
        self.client = self.app.test_client()
        self.app.config["RUN_MODE"] = "robot"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_start_stops_active_motion_test_before_autonomous_runtime(self) -> None:
        calls: list[str] = []
        self.app.config["ROBOT_RUNTIME"] = FakeRuntime(calls)
        self.app.config["TEST_SESSION"] = FakeTestSession(calls)

        response = self.client.post("/api/start")

        self.assertEqual(response.status_code, 200)
        self.assertLess(calls.index("test_stop"), calls.index("runtime_start"))

    def test_line_follow_test_stops_autonomous_runtime_before_motor_test(self) -> None:
        calls: list[str] = []
        self.app.config["ROBOT_RUNTIME"] = FakeRuntime(calls)
        self.app.config["TEST_SESSION"] = FakeTestSession(calls)

        response = self.client.post("/api/test/line_follow/start", json={"speed": 6, "step_seconds": 0.02})

        self.assertEqual(response.status_code, 200)
        self.assertLess(calls.index("runtime_stop"), calls.index("test_line_follow"))

    def test_unknown_manual_control_command_does_not_stop_runtime(self) -> None:
        calls: list[str] = []
        self.app.config["ROBOT_RUNTIME"] = FakeRuntime(calls)
        self.app.config["TEST_SESSION"] = FakeTestSession(calls)

        response = self.client.post("/api/control/not_a_real_command", json={"speed": 10})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(calls, [])


class FakeRuntime:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def start(self) -> None:
        self.calls.append("runtime_start")

    def stop(self) -> None:
        self.calls.append("runtime_stop")


class FakeTestSession:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def stop(self) -> None:
        self.calls.append("test_stop")

    def run_line_follow_test(self, speed: int, step_seconds: float) -> None:
        self.calls.append("test_line_follow")


if __name__ == "__main__":
    unittest.main()
