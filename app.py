#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
import os
import sys


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inspection_robot.web import create_app


app = create_app(ROOT)
RUN_MODE = os.environ.get("RUN_MODE", "simulate").strip().lower()
app.config["RUN_MODE"] = RUN_MODE
app.config["INSPECTION_STORE"].record_run_mode(RUN_MODE, False)

# 禁止自动启动巡逻，必须在网页手动点击"开始巡逻"按钮
# 如需启动时自动巡逻，设置环境变量 AUTO_START_RUNTIME=1
if RUN_MODE == "robot" and os.environ.get("AUTO_START_RUNTIME", "0").strip().lower() in {"1", "true", "yes"}:
    from inspection_robot.runtime import start_background_runtime

    app.config["ROBOT_RUNTIME"] = start_background_runtime(
        app.config["INSPECTION_STORE"],
        app.config["WAREHOUSE_MAP"],
        app.config["SHELF_MANIFEST"],
    )
    print(f"⚠️  AUTO_START_RUNTIME=1: 巡逻已自动启动（测试模式下建议设为 0）")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
