from __future__ import annotations

from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

from .config import load_shelf_manifest, load_tag_map, load_warehouse_map
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

    @app.get("/")
    def index():
        return render_template("dashboard.html")

    @app.get("/api/status")
    def api_status():
        return jsonify(store.snapshot())

    @app.post("/api/start")
    def api_start():
        store.start()
        return jsonify({"ok": True})

    @app.post("/api/stop")
    def api_stop():
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

    @app.post("/api/confirm")
    def api_confirm():
        payload = request.get_json(silent=True) or {}
        confirmed = store.confirm(payload.get("event_id"))
        return jsonify({"ok": True, "confirmed": confirmed})

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

    return app
