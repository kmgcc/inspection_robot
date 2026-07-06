from __future__ import annotations

import json
import logging
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

from .audio import audio_debug_status, start_audio_cue, start_spoken_message
from .config import load_shelf_manifest, load_tag_map, load_warehouse_map
from .core.planner import PlanningError, plan_patrol_route
from .robot import gimbal, motion
from .robot.sensors import RobotHardwareError
from .state import InspectionStore
from .test_mode import CalibrationStore, TestSessionManager
from .vision.video_stream import generate_mjpeg_frames, latest_video_detections


logger = logging.getLogger(__name__)
VALID_MANUAL_COMMANDS = {"forward", "backward", "stop", "turn_left_90", "turn_right_90"}


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

    calibration_store = CalibrationStore(project_root)
    app.config["CALIBRATION_STORE"] = calibration_store

    @app.get("/")
    def index():
        return render_template("dashboard.html")

    @app.get("/api/status")
    def api_status():
        runtime = _ensure_runtime() if _robot_mode_enabled() else app.config.get("ROBOT_RUNTIME")
        refresher = getattr(runtime, "refresh_motion_sensor", None)
        if _robot_mode_enabled() and callable(refresher):
            refresher()
        return jsonify(store.snapshot())

    @app.get("/api/video_feed")
    def api_video_feed():
        runtime = app.config.get("ROBOT_RUNTIME")
        config = getattr(runtime, "config", None)
        stream = generate_mjpeg_frames(
            device=int(getattr(config, "camera_device", 0)),
            fps=int(getattr(config, "video_fps", 8)),
            width=int(getattr(config, "video_width", 640)),
            height=int(getattr(config, "video_height", 360)),
            simulate=not _robot_mode_enabled(),
            image_classifier_enabled=bool(getattr(config, "image_classifier_enabled", False)),
        )
        return Response(stream, mimetype="multipart/x-mixed-replace; boundary=frame")

    @app.get("/api/video/detections")
    def api_video_detections():
        return jsonify(latest_video_detections(simulate=not _robot_mode_enabled()))

    @app.post("/api/start")
    def api_start():
        if _robot_mode_enabled():
            _stop_test_session()
            runtime = _ensure_runtime()
            runtime.start()
            store.record_run_mode("robot", True)
        else:
            store.start()
        return jsonify({"ok": True})

    @app.post("/api/stop")
    def api_stop():
        _stop_test_session()
        runtime = app.config.get("ROBOT_RUNTIME")
        if runtime is not None:
            runtime.stop()
        else:
            store.stop()
        try:
            motion.request_stop()
        except RobotHardwareError:
            pass
        return jsonify({"ok": True})

    @app.post("/api/reset")
    def api_reset():
        store.reset()
        return jsonify({"ok": True})

    @app.post("/api/simulate/tag/<tag_id>")
    def api_simulate_tag(tag_id: str):
        if not tag_id.isdigit() or len(tag_id) > 10:
            return jsonify({"ok": False, "error": f"invalid tag id: {tag_id}"}), 400
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
        return jsonify({"ok": True, "route": _json_safe(route)})

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
                "tag_id": "46",
                "kind": "item",
                "item_id": "item_46",
                "marker_family": "TAG36H11",
                "color": "BLUE",
                "ocr_text": "手机 46",
                "image_class": "PHONE",
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
                    "tag_id": "46",
                    "kind": "item",
                    "item_id": "item_46",
                    "marker_family": "TAG36H11",
                    "color": "BLUE",
                    "ocr_text": "手机 46",
                    "image_class": "PHONE",
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

    @app.post("/api/cycle/confirm")
    def api_cycle_confirm():
        runtime = app.config.get("ROBOT_RUNTIME")
        confirmer = getattr(runtime, "confirm_camera_cycle_fallback", None)
        if callable(confirmer):
            next_cycle = confirmer()
        else:
            current_cycle = int(store.snapshot().get("patrol_cycle", 1))
            store.confirm()
            next_cycle = current_cycle + 1
            store.record_cycle(next_cycle, next_cycle <= 1)
        return jsonify({"ok": True, "patrol_cycle": next_cycle})

    @app.post("/api/audio/play")
    def api_audio_play():
        payload_json = request.get_json(silent=True) or {}
        cue = str(payload_json.get("cue") or "default")
        payload, status = start_audio_cue(project_root, cue)
        store.record_audio_cue(cue, f"网页调试播放：{cue}", None if status == 200 else str(payload.get("error")))
        return jsonify(payload), status

    @app.post("/api/audio/announce")
    def api_audio_announce():
        payload = request.get_json(silent=True) or {}
        cue = str(payload.get("cue") or "default")
        message = str(payload.get("message") or f"音频提示：{cue}")
        if cue.strip().lower() == "spoken":
            result, status = start_spoken_message(project_root, message)
        else:
            result, status = start_audio_cue(project_root, cue)
        store.record_audio_cue(cue, message, None if status == 200 else str(result.get("error")))
        return jsonify({**result, "queued": status == 200, "message": message}), status

    @app.get("/api/audio/status")
    def api_audio_status():
        return jsonify(audio_debug_status(project_root))

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
        if command not in VALID_MANUAL_COMMANDS:
            return jsonify({"ok": False, "error": f"unknown command: {command}"}), 400
        if not _robot_mode_enabled():
            store.record_robot_status("IDLE", "当前为 simulate mode；手动控制不会发送到底盘。请用 RUN_MODE=robot 启动小车端服务。")
            return jsonify({"ok": False, "error": "当前服务是 simulate mode，请用 RUN_MODE=robot 启动小车端服务。"}), 409
        runtime = _ensure_runtime()
        payload = request.get_json(silent=True) or {}
        config = getattr(runtime, "config", None)
        cal = app.config["CALIBRATION_STORE"].load()
        default_speed = cal.get("straight_speed") or getattr(config, "patrol_speed", 22)
        default_dur = getattr(config, "step_seconds", 0.14)
        speed = _int_payload(payload, "speed", int(default_speed))
        duration = _float_payload(payload, "duration_seconds", float(default_dur))
        try:
            _stop_test_session()
            # Manual override parks the chassis and clears the patrol stop flag,
            # so closed-loop 90 degree turns can run without self-aborting.
            runtime.request_manual_override()
            try:
                _run_manual_command(command, speed=speed, duration_seconds=duration, runtime=runtime)
            finally:
                runtime.release_manual_override()
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
        cal = app.config["CALIBRATION_STORE"].load()
        direction = str(payload.get("direction") or "right").strip().lower()
        default_speed = cal.get("turn_speed") or getattr(config, "turn_speed", 30)
        if direction in {"left", "ccw"}:
            default_dur = cal.get("turn_ccw90_seconds") or getattr(config, "turn_90_seconds", 0.62)
        else:
            default_dur = cal.get("turn_cw90_seconds") or getattr(config, "turn_90_seconds", 0.62)
        speed = _int_payload(payload, "speed", int(default_speed))
        duration = _float_payload(payload, "duration_seconds", float(default_dur))
        if direction not in {"left", "right", "cw", "ccw"}:
            return jsonify({"ok": False, "error": f"unknown turn direction: {direction}"}), 400
        try:
            # Use the same manual-override handshake as /api/control so the
            # patrol loop cannot race the calibration turn.
            runtime.request_manual_override()
            try:
                active_motion = getattr(runtime, "motion", motion)
                if direction in {"left", "ccw"}:
                    active_motion.rotate_left_slow(speed=speed, duration_seconds=duration)
                else:
                    active_motion.rotate_right_slow(speed=speed, duration_seconds=duration)
                active_motion.stop()
                settler = getattr(runtime, "_settle", None)
                if callable(settler):
                    settler()
            finally:
                runtime.release_manual_override()
        except RobotHardwareError as exc:
            store.record_run_mode("robot", False)
            store.record_robot_status("ERROR", str(exc))
            return jsonify({"ok": False, "error": str(exc)}), 500
        store.record_run_mode("robot", True)
        store.record_robot_status("MANUAL_CONTROL", f"90 degree calibration: {direction}, speed={speed}, duration={duration}")
        return jsonify({"ok": True, "direction": direction, "speed": speed, "duration_seconds": duration})

    @app.post("/api/calibration/heading_polarity")
    def api_calibration_heading_polarity():
        """On-car heading-hold polarity self-check (requires RUN_MODE=robot).

        Briefly rotates the car and differential-steers it, then reports whether
        the straight-line correction is corrective and the recommended
        MPU6050_YAW_SIGN / HEADING_HOLD_INVERT to lock in. Needs clear floor.
        """
        if not _robot_mode_enabled():
            store.record_robot_status("IDLE", "极性自检需要 RUN_MODE=robot。")
            return jsonify({"ok": False, "error": "RUN_MODE=robot required for heading polarity self-check"}), 409
        runtime = _ensure_runtime()
        checker = getattr(runtime, "run_heading_polarity_selfcheck", None)
        if not callable(checker):
            return jsonify({"ok": False, "error": "runtime does not support heading polarity self-check"}), 500
        payload = request.get_json(silent=True) or {}
        kwargs: dict[str, object] = {}
        if "turn_speed" in payload:
            kwargs["turn_speed"] = _int_payload(payload, "turn_speed", 0)
        if "forward_speed" in payload:
            kwargs["forward_speed"] = _int_payload(payload, "forward_speed", 0)
        if "seconds" in payload:
            kwargs["seconds"] = _float_payload(payload, "seconds", 0.6)
        try:
            result = checker(**kwargs)
        except RobotHardwareError as exc:
            store.record_run_mode("robot", False)
            store.record_robot_status("ERROR", str(exc))
            return jsonify({"ok": False, "error": str(exc)}), 500
        store.record_run_mode("robot", True)
        # A failed polarity verdict is a diagnostic result, not an HTTP error;
        # the ``ok`` field in the body conveys pass/fail.
        return jsonify(result)

    @app.get("/api/export.csv")
    def api_export_csv():
        prefix = "\ufeff" if str(request.args.get("bom", "")).strip().lower() in {"1", "true", "yes"} else ""
        return Response(
            prefix + store.export_events_csv(),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=inspection_events.csv"},
        )

    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    # ------------------------------------------------------------------ #
    # 标定参数 API
    # ------------------------------------------------------------------ #

    @app.get("/api/calibration")
    def api_calibration_get():
        """读取当前标定参数（任何模式均可读）。"""
        cal = app.config["CALIBRATION_STORE"].load()
        return jsonify({"ok": True, "calibration": cal})

    @app.post("/api/calibration")
    def api_calibration_post():
        """部分更新并持久化标定参数。"""
        payload = request.get_json(silent=True) or {}
        updated = app.config["CALIBRATION_STORE"].update(payload)
        return jsonify({"ok": True, "calibration": updated})

    # ------------------------------------------------------------------ #
    # 运动测试 API（需要 RUN_MODE=robot）
    # ------------------------------------------------------------------ #

    def _ensure_test_session() -> TestSessionManager:
        session = app.config.get("TEST_SESSION")
        if session is None:
            runtime = app.config.get("ROBOT_RUNTIME")
            motion_adapter = getattr(runtime, "motion", motion) if runtime else motion
            sensor_adapter = getattr(runtime, "sensors", None)
            from .robot import sensors as default_sensors
            session = TestSessionManager(
                motion_adapter=motion_adapter,
                sensor_adapter=sensor_adapter if sensor_adapter is not None else default_sensors,
            )
            app.config["TEST_SESSION"] = session
        return session

    @app.post("/api/test/stop")
    def api_test_stop():
        """立即停止所有测试电机输出（任何模式均可调用）。"""
        session = app.config.get("TEST_SESSION")
        if session is not None:
            session.stop()
        else:
            # 尝试直接停止电机（兜底）
            try:
                motion.request_stop()
            except RobotHardwareError:
                pass
        return jsonify({"ok": True, "stopped": True})

    @app.get("/api/test/status")
    def api_test_status():
        """返回当前测试状态 + 传感器读数。"""
        session = _ensure_test_session()
        if _robot_mode_enabled():
            try:
                session.read_sensors_now()
            except Exception as exc:
                logger.warning("sensor read failed: %s", exc)
            status = session.get_status()
        else:
            # 模拟模式：返回假数据以配合UI效果预览
            status = session.get_status()
            if status.get("line_sensor") is None:
                status["line_sensor"] = [1, 0, 0, 1]
                status["line_description"] = "模拟居中 (白, 黑, 黑, 白)"
                status["distance_mm"] = 280
        return jsonify({"ok": True, **status})

    @app.post("/api/test/straight")
    def api_test_straight():
        """直行速度测试（前进或后退，固定时长）。"""
        if not _robot_mode_enabled():
            return jsonify({"ok": False, "error": "运动测试需要 RUN_MODE=robot，请在小车上启动。"}), 409
        payload = request.get_json(silent=True) or {}
        cal = app.config["CALIBRATION_STORE"].load()
        direction = str(payload.get("direction") or "forward").strip().lower()
        speed = _int_payload(payload, "speed", int(cal.get("straight_speed", 22)))
        duration = _float_payload(payload, "duration_seconds", float(cal.get("straight_step_seconds", 2.0)))
        session = _ensure_test_session()
        try:
            _stop_runtime()
            session.run_straight_test(direction, speed, duration)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except RobotHardwareError as exc:
            store.record_robot_status("ERROR", str(exc))
            return jsonify({"ok": False, "error": str(exc)}), 500
        return jsonify({"ok": True, "direction": direction, "speed": speed, "duration_seconds": duration})

    @app.post("/api/test/turn")
    def api_test_turn():
        """原地转向测试（CW顺时针 / CCW逆时针）。"""
        if not _robot_mode_enabled():
            return jsonify({"ok": False, "error": "运动测试需要 RUN_MODE=robot，请在小车上启动。"}), 409
        payload = request.get_json(silent=True) or {}
        cal = app.config["CALIBRATION_STORE"].load()
        direction = str(payload.get("direction") or "cw").strip().lower()
        # 支持 cw/ccw/right/left
        if direction in {"right", "cw"}:
            direction = "cw"
            default_dur = float(cal.get("turn_cw90_seconds", 0.62))
        else:
            direction = "ccw"
            default_dur = float(cal.get("turn_ccw90_seconds", 0.62))
        speed = _int_payload(payload, "speed", int(cal.get("turn_speed", 30)))
        duration = _float_payload(payload, "duration_seconds", default_dur)
        session = _ensure_test_session()
        try:
            _stop_runtime()
            session.run_turn_test(direction, speed, duration)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except RobotHardwareError as exc:
            store.record_robot_status("ERROR", str(exc))
            return jsonify({"ok": False, "error": str(exc)}), 500
        return jsonify({"ok": True, "direction": direction, "speed": speed, "duration_seconds": duration})

    @app.post("/api/test/line_follow/start")
    def api_test_line_follow_start():
        """开始寻线测试（持续运行，直到 /api/test/stop）。"""
        if not _robot_mode_enabled():
            return jsonify({"ok": False, "error": "运动测试需要 RUN_MODE=robot，请在小车上启动。"}), 409
        payload = request.get_json(silent=True) or {}
        cal = app.config["CALIBRATION_STORE"].load()
        speed = _int_payload(payload, "speed", int(cal.get("line_follow_speed", 22)))
        step_seconds = _float_payload(payload, "step_seconds", float(cal.get("line_follow_step_seconds", 0.14)))
        session = _ensure_test_session()
        try:
            _stop_runtime()
            session.run_line_follow_test(speed, step_seconds)
        except RobotHardwareError as exc:
            store.record_robot_status("ERROR", str(exc))
            return jsonify({"ok": False, "error": str(exc)}), 500
        return jsonify({"ok": True, "speed": speed, "step_seconds": step_seconds})

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

    def _json_safe(value: object) -> object:
        try:
            return json.loads(json.dumps(value, ensure_ascii=False))
        except (TypeError, ValueError):
            return []

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
        cal = app.config["CALIBRATION_STORE"].load()
        turn_speed = cal.get("turn_speed") or getattr(config, "turn_speed", speed)
        if command == "stop":
            active_motion.stop()
        elif command == "forward":
            active_motion.move_forward_slow(speed=speed, duration_seconds=duration_seconds)
            active_motion.stop()
        elif command == "backward":
            active_motion.move_backward_slow(speed=speed, duration_seconds=duration_seconds)
            active_motion.stop()
        elif command == "turn_left_90":
            turner = getattr(runtime, "turn_90_closed_loop", None)
            if callable(turner):
                turner("left", speed=turn_speed, duration_seconds=cal.get("turn_ccw90_seconds", 0.62))
            else:
                active_motion.rotate_left_slow(speed=turn_speed, duration_seconds=cal.get("turn_ccw90_seconds", 0.62))
                active_motion.stop()
        elif command == "turn_right_90":
            turner = getattr(runtime, "turn_90_closed_loop", None)
            if callable(turner):
                turner("right", speed=turn_speed, duration_seconds=cal.get("turn_cw90_seconds", 0.62))
            else:
                active_motion.rotate_right_slow(speed=turn_speed, duration_seconds=cal.get("turn_cw90_seconds", 0.62))
                active_motion.stop()
        else:
            raise ValueError(f"unknown manual command: {command}")

    def _run_heading_held_forward(runtime: object, active_motion: object, *, speed: int, duration_seconds: float) -> None:
        forwarder = getattr(runtime, "_forward_step", None)
        if not callable(forwarder):
            active_motion.move_forward_slow(speed=speed, duration_seconds=duration_seconds)
            active_motion.stop()
            return
        recalibrate = getattr(runtime, "_zupt_recalibrate", None)
        if callable(recalibrate):
            recalibrate("manual_forward_start")
        resetter = getattr(runtime, "_reset_heading_guard", None)
        if callable(resetter):
            resetter()
        forwarder(speed=speed, duration_seconds=duration_seconds, settle_seconds=0.0)

    def _clear_motion_stop(runtime: object | None = None) -> None:
        active_motion = getattr(runtime, "motion", motion) if runtime is not None else motion
        clearer = getattr(active_motion, "clear_stop", None)
        if callable(clearer):
            clearer()
            return
        motion.clear_stop()

    def _stop_test_session() -> None:
        session = app.config.get("TEST_SESSION")
        if session is not None:
            session.stop()

    def _stop_runtime() -> None:
        runtime = app.config.get("ROBOT_RUNTIME")
        if runtime is not None:
            runtime.stop()

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
            "item_46",
        )
        unknown_item = f"unknown_{shelf_id.lower()}"
        items: list[str] = []
        if expected:
            items.append(expected[0])
        if wrong_item not in items:
            items.append(wrong_item)
        items.append(unknown_item)
        return items

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
