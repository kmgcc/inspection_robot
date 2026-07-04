from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inspection_robot.robot import oled_display


class OledDisplayTest(unittest.TestCase):
    def tearDown(self) -> None:
        oled_display.reset_for_test()
        sys.modules.pop("yahboom_oled", None)

    def test_update_motion_sensor_writes_yaw_line_when_vendor_library_exists(self) -> None:
        fake_module = types.ModuleType("yahboom_oled")
        fake_module.Yahboom_OLED = FakeYahboomOled
        sys.modules["yahboom_oled"] = fake_module
        FakeYahboomOled.instances.clear()

        oled_display.update_motion_sensor(
            {
                "ok": True,
                "orientation_deg": {"roll": 0.0, "pitch": 0.0, "yaw": 42.25},
                "last_turn": {"direction": "left", "error_degrees": -1.2},
            }
        )

        self.assertEqual(len(FakeYahboomOled.instances), 1)
        oled = FakeYahboomOled.instances[0]
        self.assertTrue(oled.initialized)
        self.assertIn(("clear", None), oled.calls)
        self.assertIn(("line", 3, "YAW +042.2 deg  "), oled.calls)
        self.assertIn(("line", 4, "TURN L err -1.2 "), oled.calls)
        self.assertIn(("refresh", None), oled.calls)

    def test_update_motion_sensor_disables_after_vendor_import_failure(self) -> None:
        sys.modules.pop("yahboom_oled", None)

        oled_display.update_motion_sensor({"ok": False, "orientation_deg": {"yaw": None}})

        self.assertIsNotNone(oled_display.disabled_reason())


class FakeYahboomOled:
    instances: list["FakeYahboomOled"] = []

    def __init__(self, *, debug: bool) -> None:
        self.debug = debug
        self.initialized = False
        self.calls: list[tuple[str, int | None, str | None] | tuple[str, None]] = []
        self.instances.append(self)

    def init_oled_process(self) -> None:
        self.initialized = True

    def clear(self) -> None:
        self.calls.append(("clear", None))

    def add_line(self, text: str, line: int) -> None:
        self.calls.append(("line", line, text))

    def refresh(self) -> None:
        self.calls.append(("refresh", None))


if __name__ == "__main__":
    unittest.main()
