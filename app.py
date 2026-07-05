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

# 开机只允许启动网页服务；小车必须静止等待网页手动点击"开始巡逻"。
# 巡逻 runtime 只能由 /api/start 显式启动。


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
