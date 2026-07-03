from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inspection_robot.config import (
    DEFAULT_SHELF_MANIFEST,
    DEFAULT_TAG_MAP,
    DEFAULT_WAREHOUSE_MAP,
    ConfigError,
    load_shelf_manifest,
    load_tag_map,
    load_warehouse_map,
)


class ConfigTest(unittest.TestCase):
    def test_missing_files_return_normalized_default_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tag_map = load_tag_map(root)
            warehouse_map = load_warehouse_map(root)
            manifest = load_shelf_manifest(root)

        self.assertEqual(tag_map, DEFAULT_TAG_MAP)
        self.assertEqual(warehouse_map, DEFAULT_WAREHOUSE_MAP)
        self.assertEqual(manifest, DEFAULT_SHELF_MANIFEST)
        self.assertEqual(tag_map["1"]["priority"], 1)
        self.assertEqual(tag_map["1"]["kind"], "item")
        self.assertEqual(tag_map["101"]["kind"], "shelf")
        self.assertEqual(warehouse_map["grid_size"], [8, 6])
        self.assertEqual(warehouse_map["start_heading"], "E")
        self.assertIn("A1", manifest)

    def test_old_config_without_priority_or_kind_is_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "tag_map.json").write_text(
                json.dumps(
                    {
                        "1": {
                            "name": "Apple",
                            "zone": "A区",
                            "expected_zone": "A区",
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            tag_map = load_tag_map(root)

        self.assertEqual(
            tag_map["1"],
            {
                "name": "Apple",
                "zone": "A区",
                "expected_zone": "A区",
                "priority": 1,
                "kind": "item",
                "item_id": "item_01",
                "expected_shelf": "A1",
                "marker_family": "TAG36H11",
            },
        )

    def test_missing_item_identity_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "tag_map.json").write_text(
                json.dumps({"1": {"name": "Apple", "kind": "item"}}, ensure_ascii=False),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ConfigError, "item_id"):
                load_tag_map(root)

    def test_invalid_priority_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "tag_map.json").write_text(
                json.dumps(
                    {
                        "1": {
                            "name": "Apple",
                            "zone": "A区",
                            "expected_zone": "A区",
                            "priority": 0,
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ConfigError, "priority"):
                load_tag_map(root)

    def test_invalid_kind_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "tag_map.json").write_text(
                json.dumps(
                    {
                        "1": {
                            "name": "Apple",
                            "zone": "A区",
                            "expected_zone": "A区",
                            "kind": "asset",
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ConfigError, "kind"):
                load_tag_map(root)

    def test_id_ranges_are_enforced_by_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "tag_map.json").write_text(
                json.dumps(
                    {
                        "101": {
                            "name": "Wrong Item",
                            "kind": "item",
                            "item_id": "item_101",
                            "expected_shelf": "A1",
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ConfigError, "1-50"):
                load_tag_map(root)

    def test_map_and_manifest_validation_reject_invalid_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "warehouse_map.json").write_text(
                json.dumps({"grid_size": [0, 6], "start": [0, 0], "home": [0, 0], "forbidden_cells": []}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ConfigError, "grid_size"):
                load_warehouse_map(root)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "warehouse_map.json").write_text(
                json.dumps(
                    {
                        "grid_size": [3, 3],
                        "start": [0, 0],
                        "start_heading": "NE",
                        "home": [0, 0],
                        "forbidden_cells": [],
                        "shelf_points": {"A1": {"scan_pose": [1, 0, "E"], "safe_side": "W"}},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ConfigError, "start_heading"):
                load_warehouse_map(root)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "shelf_manifest.json").write_text(
                json.dumps({"A1": {"expected_items": []}}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ConfigError, "expected_items"):
                load_shelf_manifest(root)


if __name__ == "__main__":
    unittest.main()
