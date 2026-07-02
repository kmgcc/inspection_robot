from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inspection_robot.config import DEFAULT_WAREHOUSE_MAP
from inspection_robot.core.planner import PlanningError, plan_path, plan_patrol_route


class PlannerTest(unittest.TestCase):
    def test_plan_path_avoids_forbidden_cells(self) -> None:
        path = plan_path((0, 0), (3, 1), (8, 6), {(1, 0), (1, 1)})

        self.assertEqual(path[0], (0, 0))
        self.assertEqual(path[-1], (3, 1))
        self.assertNotIn((1, 0), path)
        self.assertNotIn((1, 1), path)

    def test_plan_path_replans_around_temporary_blocked_cells(self) -> None:
        normal_path = plan_path((0, 0), (3, 0), (4, 3), set())
        blocked_path = plan_path((0, 0), (3, 0), (4, 3), set(), temporary_blocked_cells={(1, 0)})

        self.assertIn((1, 0), normal_path)
        self.assertNotIn((1, 0), blocked_path)
        self.assertEqual(blocked_path[-1], (3, 0))

    def test_plan_path_raises_clear_error_when_unreachable(self) -> None:
        with self.assertRaisesRegex(PlanningError, "unreachable"):
            plan_path((0, 0), (2, 0), (3, 1), {(1, 0)})

    def test_plan_patrol_route_returns_scan_targets(self) -> None:
        route = plan_patrol_route(DEFAULT_WAREHOUSE_MAP, ["A1", "A2"])

        self.assertEqual([step["target"] for step in route if step["action"] == "scan"], ["A1_SCAN", "A2_SCAN"])
        self.assertEqual(route[-1]["target"], "HOME")


if __name__ == "__main__":
    unittest.main()
