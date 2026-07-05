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

from inspection_robot.config import TagMap
from inspection_robot.config import DEFAULT_SHELF_MANIFEST, DEFAULT_TAG_MAP, DEFAULT_WAREHOUSE_MAP
from inspection_robot.core.events import make_event
from inspection_robot.core.store import InspectionStore
from inspection_robot.state import InspectionStore as StateInspectionStore


def sample_tag_map() -> TagMap:
    return {
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


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def make_store(self) -> InspectionStore:
        return InspectionStore(
            sample_tag_map(),
            warehouse_map=DEFAULT_WAREHOUSE_MAP,
            shelf_manifest=DEFAULT_SHELF_MANIFEST,
            root=self.root,
        )

    def test_state_import_reexports_core_store(self) -> None:
        self.assertIs(StateInspectionStore, InspectionStore)

    def test_record_scan_result_is_persisted_and_loaded_on_startup(self) -> None:
        store = self.make_store()
        store.start()
        store.record_scan_result("A1", ["item_01", "item_02", "item_03"], frame_id="frame-1")
        snapshot = store.snapshot()

        self.assertEqual(snapshot["task_status"], "NORMAL_LOGGED")
        self.assertIn(snapshot["events"][0]["type"], {"normal_item", "shelf_scanned"})
        events_path = self.root / "data" / "events.json"
        self.assertTrue(events_path.exists())

        reloaded = self.make_store()
        self.assertEqual(reloaded.snapshot()["events"][0]["type"], snapshot["events"][0]["type"])

    def test_record_scan_result_deduplicates_repeated_item_reads(self) -> None:
        store = InspectionStore(
            sample_tag_map(),
            warehouse_map=DEFAULT_WAREHOUSE_MAP,
            shelf_manifest={"A1": {"expected_items": ["item_01"]}},
            root=self.root,
        )
        store.start()

        events = store.record_scan_result("A1", ["item_01", "item_01"], frame_id="repeat-item")
        snapshot = store.snapshot()
        shelf = next(item for item in snapshot["shelves"] if item["shelf_id"] == "A1")

        self.assertEqual(snapshot["scan"]["detected_items"], ["item_01"])
        self.assertEqual([item["item_id"] for item in shelf["items"]], ["item_01"])
        self.assertNotIn("duplicate_item", [event["type"] for event in events])

    def test_record_scan_result_filters_shelf_tokens_from_items(self) -> None:
        store = InspectionStore(
            DEFAULT_TAG_MAP,
            warehouse_map=DEFAULT_WAREHOUSE_MAP,
            shelf_manifest={"A1": {"expected_items": []}},
            root=self.root,
        )
        store.start()

        events = store.record_scan_result("A1", ["A2", "110", "item_07"], frame_id="shelf-token")
        snapshot = store.snapshot()
        shelf = next(item for item in snapshot["shelves"] if item["shelf_id"] == "A1")

        self.assertEqual(snapshot["scan"]["detected_items"], ["item_07"])
        self.assertEqual([item["item_id"] for item in shelf["items"]], ["item_07"])
        self.assertFalse(any(event.get("tag_id") == "110" or event.get("item") == "A2" for event in events))

    def test_record_detection_evidence_deduplicates_repeated_item_frames(self) -> None:
        store = InspectionStore(
            sample_tag_map(),
            warehouse_map=DEFAULT_WAREHOUSE_MAP,
            shelf_manifest={"A1": {"expected_items": ["item_01"]}},
            root=self.root,
        )
        store.start()

        events = store.record_detection_evidence(
            "A1",
            [
                {"tag_id": "1", "kind": "item", "item_id": "item_01", "confidence": 0.72},
                {"tag_id": "1", "kind": "item", "item_id": "item_01", "ocr_text": "ITEM-01", "confidence": 0.91},
            ],
            frame_id="repeat-frame",
        )
        snapshot = store.snapshot()

        self.assertEqual(snapshot["scan"]["detected_items"], ["item_01"])
        self.assertEqual(len(snapshot["scan"]["detections"]), 1)
        self.assertEqual(snapshot["scan"]["detections"][0]["ocr_text"], "ITEM-01")
        self.assertNotIn("duplicate_item", [event["type"] for event in events])

    def test_detection_evidence_and_confirm_close_one_waiting_event(self) -> None:
        store = self.make_store()
        store.start()
        store.record_detection_evidence("A1", [{"tag_id": "404"}], frame_id="frame-404")
        store.record_scan_result("A1", ["item_01", "item_04"])
        before = store.snapshot()
        waiting_ids = [event["id"] for event in before["events"] if event["status"] == "waiting_confirm"]

        confirmed = store.confirm(waiting_ids[0])
        after = store.snapshot()

        self.assertTrue(confirmed)
        self.assertEqual(after["events"][0]["type"], "manual_confirm")
        self.assertTrue(any(event["id"] == waiting_ids[0] and event["status"] == "confirmed" for event in after["events"]))
        still_waiting = [event for event in after["events"] if event["status"] == "waiting_confirm"]
        self.assertEqual(len(still_waiting), len(waiting_ids) - 1)
        self.assertFalse(store.confirm(waiting_ids[0]))

    def test_new_status_fields_are_updated_by_path_pose_and_shelf_methods(self) -> None:
        store = self.make_store()
        store.start()
        store.record_path([(0, 0), (1, 0), (2, 0)], status="active")
        store.record_pose(1, 0, "E")
        store.record_shelf_arrival("A1")
        snapshot = store.snapshot()

        self.assertEqual(snapshot["task_status"], "ALIGNING_SHELF")
        self.assertEqual(snapshot["current_shelf"], "A1")
        self.assertEqual(snapshot["current_target"], "A1_SCAN")
        self.assertEqual(snapshot["pose"], {"x": 1, "y": 0, "heading": "E"})
        self.assertEqual(snapshot["path"]["waypoints"], [[0, 0], [1, 0], [2, 0]])

    def test_record_scan_start_marks_scan_window_active(self) -> None:
        store = self.make_store()
        store.start()
        store.record_shelf_arrival("A1")

        store.record_scan_start("A1", target="A1_SCAN", frame_id="scan-1")
        snapshot = store.snapshot()

        self.assertEqual(snapshot["task_status"], "SCANNING_SHELF")
        self.assertEqual(snapshot["scan"]["active"], True)
        self.assertEqual(snapshot["scan"]["shelf_id"], "A1")
        self.assertEqual(snapshot["scan"]["frame_id"], "scan-1")
        self.assertTrue(any(event["type"] == "shelf_aligned" for event in snapshot["events"]))

    def test_first_pass_records_items_then_later_inventory_diff_warns(self) -> None:
        store = InspectionStore(
            sample_tag_map(),
            warehouse_map=DEFAULT_WAREHOUSE_MAP,
            shelf_manifest={"A1": {"expected_items": []}},
            root=self.root,
        )
        store.start()
        store.record_cycle(1, skip_shortage_detection=True)
        store.record_scan_result("A1", ["item_01", "item_02"], frame_id="learn-1")
        first = store.snapshot()

        shelf = next(item for item in first["shelves"] if item["shelf_id"] == "A1")
        self.assertEqual({item["item_id"] for item in shelf["items"]}, {"item_01", "item_02"})
        self.assertFalse(any(event["status"] == "waiting_confirm" for event in first["events"]))

        store.record_cycle(2, skip_shortage_detection=False)
        store.record_scan_result("A1", ["item_02", "item_03"], frame_id="diff-1")
        second = store.snapshot()
        event_types = [event["type"] for event in second["events"]]
        shelf = next(item for item in second["shelves"] if item["shelf_id"] == "A1")
        item_states = {item["item_id"]: item["status"] for item in shelf["items"]}

        self.assertIn("added_item", event_types)
        self.assertIn("missing_item", event_types)
        self.assertEqual(next(event for event in second["events"] if event["type"] == "missing_item")["status"], "warning")
        self.assertEqual(next(event for event in second["events"] if event["type"] == "added_item")["status"], "info")
        self.assertEqual(item_states["item_01"], "missing")
        self.assertEqual(item_states["item_03"], "added")
        self.assertEqual(second["task_status"], "ABNORMAL_ALARM")

    def test_first_pass_empty_shelf_is_normal_then_second_pass_addition_warns(self) -> None:
        store = InspectionStore(
            sample_tag_map(),
            warehouse_map=DEFAULT_WAREHOUSE_MAP,
            shelf_manifest={"A1": {"expected_items": []}},
            root=self.root,
        )
        store.start()
        store.record_cycle(1, skip_shortage_detection=True)
        first_events = store.record_scan_result("A1", [], frame_id="learn-empty")
        first = store.snapshot()

        shelf = next(item for item in first["shelves"] if item["shelf_id"] == "A1")
        self.assertEqual(shelf["items"], [])
        self.assertEqual(shelf["status"], "normal")
        self.assertTrue(shelf["inventory_observed"])
        self.assertFalse(any(event["type"] == "missing_item" for event in first_events))

        store.record_cycle(2, skip_shortage_detection=False)
        second_events = store.record_scan_result("A1", ["item_01"], frame_id="diff-added")
        second = store.snapshot()

        self.assertIn("added_item", [event["type"] for event in second_events])
        shelf = next(item for item in second["shelves"] if item["shelf_id"] == "A1")
        self.assertEqual(shelf["items"][0]["status"], "added")

    def test_confirm_inventory_change_marks_event_and_shelf_resolved(self) -> None:
        store = InspectionStore(
            sample_tag_map(),
            warehouse_map=DEFAULT_WAREHOUSE_MAP,
            shelf_manifest={"A1": {"expected_items": []}},
            root=self.root,
        )
        store.start()
        store.record_cycle(1, skip_shortage_detection=True)
        store.record_scan_result("A1", ["item_01", "item_02"], frame_id="learn")
        store.record_cycle(2, skip_shortage_detection=False)
        store.record_scan_result("A1", ["item_02"], frame_id="missing")
        before = store.snapshot()
        missing = next(event for event in before["events"] if event["type"] == "missing_item")

        self.assertTrue(store.confirm(missing["id"]))
        after = store.snapshot()
        shelf = next(item for item in after["shelves"] if item["shelf_id"] == "A1")

        self.assertEqual(next(event for event in after["events"] if event["id"] == missing["id"])["status"], "confirmed")
        self.assertEqual(shelf["status"], "normal")
        self.assertFalse(any(item.get("status") == "missing" for item in shelf["items"]))

    def test_confirm_added_item_accepts_it_as_present_baseline(self) -> None:
        store = InspectionStore(
            sample_tag_map(),
            warehouse_map=DEFAULT_WAREHOUSE_MAP,
            shelf_manifest={"A1": {"expected_items": []}},
            root=self.root,
        )
        store.start()
        store.record_cycle(1, skip_shortage_detection=True)
        store.record_scan_result("A1", [], frame_id="learn-empty")
        store.record_cycle(2, skip_shortage_detection=False)
        store.record_scan_result("A1", ["item_01"], frame_id="added")
        before = store.snapshot()
        added = next(event for event in before["events"] if event["type"] == "added_item")

        self.assertTrue(store.confirm(added["id"]))
        store.record_scan_result("A1", ["item_01"], frame_id="still-present")
        after = store.snapshot()
        shelf = next(item for item in after["shelves"] if item["shelf_id"] == "A1")

        self.assertEqual(shelf["items"][0]["status"], "present")
        self.assertEqual(len([event for event in after["events"] if event["type"] == "added_item"]), 1)

    def test_motion_updates_do_not_clear_waiting_confirmation(self) -> None:
        store = InspectionStore(
            sample_tag_map(),
            warehouse_map=DEFAULT_WAREHOUSE_MAP,
            shelf_manifest={"A1": {"expected_items": ["item_01"]}},
            root=self.root,
        )
        store.start()
        store.record_cycle(2, skip_shortage_detection=False)
        store.record_scan_result("A1", [], frame_id="empty-1")

        store.record_pose(1, 0, "E")
        store.record_forbidden_zone("black-tape-end", False)
        store.record_obstacle(None, False)
        snapshot = store.snapshot()

        self.assertEqual(snapshot["task_status"], "WAIT_CONFIRM")
        self.assertEqual(snapshot["alarm"]["level"], "warning")
        self.assertEqual(snapshot["pose"], {"x": 1, "y": 0, "heading": "E"})

    def test_finish_run_only_changes_state_after_patrol_started(self) -> None:
        store = self.make_store()
        store.finish_run()
        self.assertEqual(store.snapshot()["events"], [])

        store.start()
        store.finish_run()
        snapshot = store.snapshot()

        self.assertEqual(snapshot["task_status"], "FINISHED")

    def test_obstacle_status_stop_and_robot_status_transitions(self) -> None:
        store = self.make_store()
        store.start()
        store.record_obstacle(320, True)
        self.assertEqual(store.snapshot()["task_status"], "OBSTACLE_WAIT")

        store.record_obstacle(None, False)
        self.assertEqual(store.snapshot()["task_status"], "PATROLLING")

        store.record_robot_status("TAG_DETECTED", "tag seen")
        self.assertEqual(store.snapshot()["task_status"], "TAG_DETECTED")
        self.assertEqual(store.snapshot()["last_message"], "tag seen")

        store.stop()
        self.assertEqual(store.snapshot()["task_status"], "STOPPED")

    def test_event_log_keeps_latest_thousand_events(self) -> None:
        store = self.make_store()
        for index in range(1005):
            store._append_event_locked(make_event("system", event_id=f"evt-{index}", message=str(index)))

        snapshot = store.snapshot()

        self.assertEqual(len(snapshot["events"]), 1000)
        self.assertEqual(snapshot["events"][0]["id"], "evt-1004")
        self.assertEqual(snapshot["events"][-1]["id"], "evt-5")
        self.assertEqual(store.state.events[0]["id"], "evt-5")
        self.assertEqual(store.state.events[-1]["id"], "evt-1004")

    def test_write_failure_preserves_in_memory_event_and_reports_error(self) -> None:
        store = self.make_store()
        store.events_path = self.root
        store.start()
        store.record_scan_result("A1", ["item_01", "item_02", "item_03"])
        snapshot = store.snapshot()

        self.assertEqual(len(snapshot["events"]), 3)
        self.assertIn("写入", snapshot["last_message"])

    def test_export_events_csv_uses_stable_header(self) -> None:
        store = self.make_store()
        store.start()
        store.record_scan_result("A1", ["item_01", "item_02", "item_03"])
        header = store.export_events_csv().splitlines()[0]

        self.assertEqual(header, "事件ID,时间,类型,标签ID,物品,区域,货架,期望货架,颜色,OCR,图像类别,优先级,状态,来源,说明")

    def test_old_events_file_is_loaded(self) -> None:
        events_path = self.root / "data" / "events.json"
        events_path.parent.mkdir()
        events_path.write_text(
            json.dumps(
                [
                    {
                        "id": "old-1",
                        "time": "2026-07-02T10:00:00",
                        "type": "normal_item",
                        "tag_id": "1",
                        "item": "Apple",
                        "zone": "A区",
                        "expected_zone": "A区",
                        "shelf_id": "A1",
                        "expected_shelf": "A1",
                        "source": "test",
                        "priority": 1,
                        "status": "normal",
                        "message": "old",
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        store = self.make_store()

        self.assertEqual(store.snapshot()["events"][0]["id"], "old-1")

    def test_load_events_removes_stale_temp_file(self) -> None:
        events_path = self.root / "data" / "events.json"
        events_path.parent.mkdir()
        events_path.write_text("[]", encoding="utf-8")
        temp_path = events_path.parent / f"{events_path.name}.tmp"
        temp_path.write_text("[", encoding="utf-8")

        self.make_store()

        self.assertFalse(temp_path.exists())

    def test_load_events_skips_single_corrupt_record(self) -> None:
        events_path = self.root / "data" / "events.json"
        events_path.parent.mkdir()
        events_path.write_text(
            json.dumps(
                [
                    {"id": "ok-1", "time": "t", "type": "system", "priority": 1, "status": "info"},
                    {"id": "bad-1", "type": {"not": "a scalar"}, "priority": object()},
                    {"id": "ok-2", "time": "t", "type": "system", "priority": 1, "status": "info"},
                ],
                default=lambda _: {"not": "json-scalar"},
            ),
            encoding="utf-8",
        )

        store = self.make_store()

        self.assertEqual([event["id"] for event in store.snapshot()["events"]], ["ok-2", "ok-1"])

    def test_motion_sensor_updates_are_not_persisted_as_events(self) -> None:
        store = self.make_store()

        store.record_motion_sensor({"ok": True, "source": "test"})

        self.assertEqual(store.snapshot()["motion_sensor"]["ok"], True)
        self.assertFalse((self.root / "data" / "events.json").exists())


if __name__ == "__main__":
    unittest.main()
