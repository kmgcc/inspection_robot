from __future__ import annotations

from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

from .audio import start_audio_cue, start_default_audio
from .config import load_shelf_manifest, load_tag_map, load_warehouse_map
from .core.planner import PlanningError, plan_patrol_route
from .robot import gimbal, motion
from .robot.sensors import RobotHardwareError
from .state import InspectionStore


def create_app(root: Path | None = None) -> Flask:
    project_root = root or Path(__file__).resolve().parents[2]
    app = Flask(
        __name__,
        template_folder=str(project_root / "src" / "inspection_robot" / "templates"),
        static_folder=str(project_root / "src" / "inspection_robot" / "static"),
    )
    store = InspectionStore(
        load_tag_map(project_root),
        warehouse_map=load_warehouse_map(project_root),
        shelf_manifest=load_shelf_manifest(project_root),
        root=project_root,
    )
    app.config["INSPECTION_STORE"] = store
    app.config["WAREHOUSE_MAP"] = store.warehouse_map
    app.config["SHELF_MANIFEST"] = store.shelf_manifest
    app.config["RUN_MODE"] = "simulate"
    store.record_run_mode("simulate", False)

    @app.get("/")
    def index():
        return render_template("dashboard.html")

    @app.get("/api/status")
    def api_status():
        return jsonify(store.snapshot())

    @app.post("/api/start")
    def api_start():
        if _robot_mode_enabled():
            runtime = _ensure_runtime()
            runtime.start()
            store.record_run_mode("robot", True)
        else:
            store.start()
        return jsonify({"ok": True})

    @app.post("/api/stop")
    def api_stop():
        runtime = app.config.get("ROBOT_RUNTIME")
        if runtime is not None:
            runtime.stop()
        else:
            store.stop()
        return jsonify({"ok": True})

    @app.post("/api/reset")
    def api_reset():
        store.reset()
        return jsonify({"ok": True})

    @app.post("/api/simulate/tag/<tag_id>")
    def api_simulate_tag(tag_id: str):
        store.handle_tag(tag_id)
        return jsonify({"ok": True})

    @app.post("/api/demo/path")
    def api_demo_path():
        try:
            route = plan_patrol_route(store.warehouse_map, list(store.shelf_manifest))
        except PlanningError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        store.start()
        store.record_path(_flatten_route(route), status="active")
        return jsonify({"ok": True, "route": route})

    @app.post("/api/demo/obstacle")
    def api_demo_obstacle():
        payload = request.get_json(silent=True) or {}
        store.record_obstacle(_int_payload(payload, "distance_mm", 160), True)
        return jsonify({"ok": True})

    @app.post("/api/demo/obstacle/clear")
    def api_demo_obstacle_clear():
        payload = request.get_json(silent=True) or {}
        store.record_obstacle(_int_payload(payload, "distance_mm", 420), False)
        return jsonify({"ok": True})

    @app.post("/api/demo/forbidden")
    def api_demo_forbidden():
        store.record_forbidden_zone("black-tape-F1", True)
        return jsonify({"ok": True})

    @app.post("/api/demo/forbidden/clear")
    def api_demo_forbidden_clear():
        store.record_forbidden_zone("black-tape-F1", False)
        return jsonify({"ok": True})

    @app.post("/api/demo/scan/<shelf_id>/normal")
    def api_demo_scan_normal(shelf_id: str):
        normalized_shelf = _normalize_shelf_id(shelf_id)
        detections = _detections_for_expected_shelf(normalized_shelf)
        frame_id = f"demo-{normalized_shelf.lower()}-normal"
        store.record_shelf_arrival(normalized_shelf)
        store.record_scan_start(normalized_shelf, target=f"{normalized_shelf}_SCAN", frame_id=frame_id)
        store.record_detection_evidence(normalized_shelf, detections, frame_id=frame_id)
        return jsonify({"ok": True, "shelf_id": normalized_shelf, "detections": detections})

    @app.post("/api/demo/scan/<shelf_id>/abnormal")
    def api_demo_scan_abnormal(shelf_id: str):
        normalized_shelf = _normalize_shelf_id(shelf_id)
        detected_items = _abnormal_items_for_shelf(normalized_shelf)
        frame_id = f"demo-{normalized_shelf.lower()}-abnormal"
        store.record_cycle(2, False)
        store.record_shelf_arrival(normalized_shelf)
        store.record_scan_start(normalized_shelf, target=f"{normalized_shelf}_SCAN", frame_id=frame_id)
        store.record_scan_result(normalized_shelf, detected_items, frame_id=frame_id)
        return jsonify({"ok": True, "shelf_id": normalized_shelf, "detected_items": detected_items})

    @app.post("/api/demo/evidence-mismatch")
    def api_demo_evidence_mismatch():
        shelf_id = "A1"
        store.record_shelf_arrival(shelf_id)
        detections = [
            {
                "tag_id": "1",
                "kind": "item",
                "item_id": "item_01",
                "marker_family": "TAG36H11",
                "color": "BLUE",
                "ocr_text": "ITEM-99",
                "image_class": "BOX",
                "confidence": 0.71,
            }
        ]
        store.record_detection_evidence(shelf_id, detections, frame_id="demo-evidence-mismatch")
        return jsonify({"ok": True, "shelf_id": shelf_id, "detections": detections})

    @app.post("/api/demo/run")
    def api_demo_run():
        store.reset()
        try:
            route = plan_patrol_route(store.warehouse_map, list(store.shelf_manifest))
        except PlanningError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        store.start()
        store.record_path(_flatten_route(route), status="active")
        store.record_pose(1, 0, "E", source="simulate")
        store.record_shelf_arrival("A1")
        store.record_detection_evidence("A1", _detections_for_expected_shelf("A1"), frame_id="demo-a1-normal")
        store.record_obstacle(160, True)
        store.record_obstacle(420, False)
        store.record_forbidden_zone("black-tape-F1", True)
        store.record_forbidden_zone("black-tape-F1", False)
        store.record_cycle(2, False)
        store.record_shelf_arrival("A2")
        store.record_scan_result("A2", _abnormal_items_for_shelf("A2"), frame_id="demo-a2-abnormal")
        store.record_detection_evidence(
            "A2",
            [
                {
                    "tag_id": "4",
                    "kind": "item",
                    "item_id": "item_04",
                    "marker_family": "TAG36H11",
                    "color": "BLUE",
                    "ocr_text": "ITEM-04",
                    "image_class": "BOX",
                    "confidence": 0.78,
                }
            ],
            frame_id="demo-a2-evidence-mismatch",
        )
        confirmed_count = 0
        while store.confirm():
            confirmed_count += 1
        store.finish_run()
        return jsonify({"ok": True, "confirmed_count": confirmed_count})

    @app.post("/api/confirm")
    def api_confirm():
        payload = request.get_json(silent=True) or {}
        confirmed = store.confirm(payload.get("event_id"))
        return jsonify({"ok": True, "confirmed": confirmed})

    @app.post("/api/audio/play")
    def api_audio_play():
        payload, status = start_default_audio(project_root)
        return jsonify(payload), status

    @app.post("/api/audio/announce")
    def api_audio_announce():
        payload = request.get_json(silent=True) or {}
        cue = str(payload.get("cue") or "default")
        message = str(payload.get("message") or f"音频提示：{cue}")
        result, status = start_audio_cue(project_root, cue)
        store.record_audio_cue(cue, message, None if status == 200 else str(result.get("error")))
        return jsonify({**result, "queued": status == 200, "message": message}), status

    @app.post("/api/gimbal/init")
    def api_gimbal_init():
        if not _robot_mode_enabled():
            store.record_robot_status("IDLE", "当前为 simulate mode；请用 RUN_MODE=robot 在小车上启动后再初始化云台。")
            return jsonify({"ok": False, "error": "当前服务是 simulate mode，请用 RUN_MODE=robot 启动小车端服务。"}), 409
        try:
            gimbal.initialize_side_camera()
        except RobotHardwareError as exc:
            store.record_run_mode("robot", False)
            store.record_robot_status("ERROR", str(exc))
            return jsonify({"ok": False, "error": str(exc)}), 500
        store.record_run_mode("robot", True)
        store.record_gimbal_initialized(yaw=getattr(gimbal, "DEFAULT_YAW_ANGLE", None), pitch=getattr(gimbal, "DEFAULT_PITCH_ANGLE", None))
        return jsonify({"ok": True})

    @app.post("/api/control/<command>")
    def api_control(command: str):
        if not _robot_mode_enabled():
            store.record_robot_status("IDLE", "当前为 simulate mode；手动控制不会发送到底盘。请用 RUN_MODE=robot 启动小车端服务。")
            return jsonify({"ok": False, "error": "当前服务是 simulate mode，请用 RUN_MODE=robot 启动小车端服务。"}), 409
        runtime = _ensure_runtime()
        payload = request.get_json(silent=True) or {}
        config = getattr(runtime, "config", None)
        speed = _int_payload(payload, "speed", int(getattr(config, "patrol_speed", 22)))
        duration = _float_payload(payload, "duration_seconds", float(getattr(config, "step_seconds", 0.14)))
        try:
            runtime.stop()
            _run_manual_command(command, speed=speed, duration_seconds=duration, runtime=runtime)
        except (RobotHardwareError, ValueError) as exc:
            store.record_run_mode("robot", False)
            store.record_robot_status("ERROR", str(exc))
            return jsonify({"ok": False, "error": str(exc)}), 400
        store.record_run_mode("robot", True)
        status = "STOPPED" if command == "stop" else "MANUAL_CONTROL"
        store.record_robot_status(status, f"手动控制完成：{command}")
        return jsonify({"ok": True, "command": command})

    @app.post("/api/calibration/turn_90")
    def api_calibration_turn_90():
        if not _robot_mode_enabled():
            store.record_robot_status("IDLE", "90 degree calibration requires RUN_MODE=robot.")
            return jsonify({"ok": False, "error": "RUN_MODE=robot required for turn calibration"}), 409
        runtime = _ensure_runtime()
        payload = request.get_json(silent=True) or {}
        config = getattr(runtime, "config", None)
        direction = str(payload.get("direction") or "right").strip().lower()
        speed = _int_payload(payload, "speed", int(getattr(config, "turn_speed", 18)))
        duration = _float_payload(payload, "duration_seconds", float(getattr(config, "turn_90_seconds", 0.75)))
        if direction not in {"left", "right"}:
            return jsonify({"ok": False, "error": f"unknown turn direction: {direction}"}), 400
        try:
            runtime.stop()
            active_motion = getattr(runtime, "motion", motion)
            if direction == "left":
                active_motion.rotate_left_slow(speed=speed, duration_seconds=duration)
            else:
                active_motion.rotate_right_slow(speed=speed, duration_seconds=duration)
            active_motion.stop()
            settler = getattr(runtime, "_settle", None)
            if callable(settler):
                settler()
        except RobotHardwareError as exc:
            store.record_run_mode("robot", False)
            store.record_robot_status("ERROR", str(exc))
            return jsonify({"ok": False, "error": str(exc)}), 500
        store.record_run_mode("robot", True)
        store.record_robot_status("MANUAL_CONTROL", f"90 degree calibration: {direction}, speed={speed}, duration={duration}")
        return jsonify({"ok": True, "direction": direction, "speed": speed, "duration_seconds": duration})

    @app.get("/api/export.csv")
    def api_export_csv():
        return Response(
            "\ufeff" + store.export_events_csv(),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=inspection_events.csv"},
        )

    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    def _flatten_route(route: list[dict[str, object]]) -> list[tuple[int, int]]:
        waypoints: list[tuple[int, int]] = []
        for step in route:
            for cell in step.get("path", []):
                if not isinstance(cell, list) or len(cell) < 2:
                    continue
                point = (int(cell[0]), int(cell[1]))
                if not waypoints or waypoints[-1] != point:
                    waypoints.append(point)
        return waypoints

    def _int_payload(payload: dict[str, object], key: str, default: int) -> int:
        value = payload.get(key, default)
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _float_payload(payload: dict[str, object], key: str, default: float) -> float:
        value = payload.get(key, default)
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _run_manual_command(command: str, *, speed: int, duration_seconds: float, runtime: object) -> None:
        active_motion = getattr(runtime, "motion", motion)
        config = getattr(runtime, "config", None)
        turn_duration = getattr(config, "turn_90_seconds", 0.55)
        turn_speed = getattr(config, "turn_speed", speed)
        commands = {
            "stop": lambda: active_motion.stop(),
            "forward": lambda: active_motion.move_forward_slow(speed=speed, duration_seconds=duration_seconds),
            "turn_left_90": lambda: active_motion.rotate_left_slow(speed=turn_speed, duration_seconds=turn_duration),
            "turn_right_90": lambda: active_motion.rotate_right_slow(speed=turn_speed, duration_seconds=turn_duration),
        }
        action = commands.get(command)
        if action is None:
            raise ValueError(f"unknown manual command: {command}")
        action()

    def _robot_mode_enabled() -> bool:
        return str(app.config.get("RUN_MODE", "simulate")).strip().lower() == "robot"

    def _ensure_runtime() -> object:
        runtime = app.config.get("ROBOT_RUNTIME")
        if runtime is None:
            from .runtime import RobotRuntime

            runtime = RobotRuntime(store, store.warehouse_map, store.shelf_manifest)
            app.config["ROBOT_RUNTIME"] = runtime
        return runtime

    def _normalize_shelf_id(shelf_id: str) -> str:
        return shelf_id.strip().upper() or "A1"

    def _detections_for_expected_shelf(shelf_id: str) -> list[dict[str, object]]:
        expected_items = store.shelf_manifest.get(shelf_id, {"expected_items": []})["expected_items"]
        detections: list[dict[str, object]] = []
        for item_id in expected_items:
            tag_id, info = _tag_for_item(item_id)
            if tag_id is None or info is None:
                continue
            detections.append(
                {
                    "tag_id": tag_id,
                    "kind": "item",
                    "item_id": item_id,
                    "marker_family": info.get("marker_family", "TAG36H11"),
                    "color": info.get("expected_color"),
                    "ocr_text": info.get("expected_ocr"),
                    "image_class": info.get("expected_image_class"),
                    "confidence": 0.92,
                }
            )
        shelf_tag_id, shelf_info = _tag_for_shelf(shelf_id)
        if shelf_tag_id is not None and shelf_info is not None:
            detections.insert(
                0,
                {
                    "tag_id": shelf_tag_id,
                    "kind": "shelf",
                    "shelf_id": shelf_id,
                    "marker_family": shelf_info.get("marker_family", "TAG36H11"),
                    "ocr_text": shelf_info.get("ocr_label", shelf_id),
                    "confidence": 0.95,
                },
            )
        return detections

    def _abnormal_items_for_shelf(shelf_id: str) -> list[str]:
        expected = list(store.shelf_manifest.get(shelf_id, {"expected_items": []})["expected_items"])
        wrong_item = next(
            (
                str(info["item_id"])
                for info in store.tag_map.values()
                if info.get("kind") == "item" and info.get("expected_shelf") != shelf_id and "item_id" in info
            ),
            "item_01",
        )
        if not expected:
            return [wrong_item]
        first = expected[0]
        if len(expected) == 1:
            return [wrong_item, wrong_item]
        return [first, first, wrong_item]

    def _tag_for_item(item_id: str):
        for tag_id, info in store.tag_map.items():
            if info.get("kind") == "item" and info.get("item_id") == item_id:
                return tag_id, info
        return None, None

    def _tag_for_shelf(shelf_id: str):
        for tag_id, info in store.tag_map.items():
            if info.get("kind") == "shelf" and info.get("shelf_id") == shelf_id:
                return tag_id, info
        return None, None

    return app
