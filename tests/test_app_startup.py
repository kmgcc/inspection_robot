from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inspection_robot.web import create_app as real_create_app


class AppStartupTest(unittest.TestCase):
    def test_robot_mode_startup_never_autostarts_patrol(self) -> None:
        module_name = "inspection_robot_app_startup_test"
        module_path = ROOT / "app.py"

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                "os.environ",
                {"RUN_MODE": "robot", "AUTO_START_RUNTIME": "1", "PORT": "5999"},
                clear=False,
            ):
                with mock.patch("inspection_robot.web.create_app") as create_app:
                    create_app.side_effect = lambda _root: real_create_app(Path(tmp))
                    spec = importlib.util.spec_from_file_location(module_name, module_path)
                    self.assertIsNotNone(spec)
                    self.assertIsNotNone(spec.loader)
                    module = importlib.util.module_from_spec(spec)
                    sys.modules.pop(module_name, None)
                    spec.loader.exec_module(module)

        self.assertEqual(module.app.config["RUN_MODE"], "robot")
        self.assertNotIn("ROBOT_RUNTIME", module.app.config)
        snapshot = module.app.config["INSPECTION_STORE"].snapshot()
        self.assertEqual(snapshot["task_status"], "IDLE")
        self.assertFalse(snapshot["hardware_connected"])


if __name__ == "__main__":
    unittest.main()
