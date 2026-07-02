from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inspection_robot.config import load_shelf_manifest, load_tag_map, load_warehouse_map


class ConfigContractTest(unittest.TestCase):
    def test_repository_configs_satisfy_shared_contract(self) -> None:
        warehouse_map = load_warehouse_map(ROOT)
        manifest = load_shelf_manifest(ROOT)
        tag_map = load_tag_map(ROOT)

        self.assertEqual(len(warehouse_map["start"]), 2)
        self.assertGreaterEqual(len(warehouse_map["forbidden_cells"]), 1)
        self.assertGreaterEqual(len(warehouse_map["shelf_points"]), 2)
        self.assertGreaterEqual(len(manifest), 2)

        shelf_tags = [int(tag_id) for tag_id, info in tag_map.items() if info["kind"] == "shelf"]
        item_tags = [int(tag_id) for tag_id, info in tag_map.items() if info["kind"] == "item"]

        self.assertGreaterEqual(len(shelf_tags), 1)
        self.assertGreaterEqual(len(item_tags), 2)
        self.assertTrue(all(101 <= tag_id <= 120 for tag_id in shelf_tags))
        self.assertTrue(all(1 <= tag_id <= 50 for tag_id in item_tags))
        self.assertTrue(
            any(
                info.get("expected_color") or info.get("expected_ocr") or info.get("expected_image_class")
                for info in tag_map.values()
                if info["kind"] == "item"
            )
        )


if __name__ == "__main__":
    unittest.main()
