from __future__ import annotations

import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inspection_robot.config import load_shelf_manifest, load_tag_map, load_warehouse_map
from inspection_robot.core.store import InspectionStore
from inspection_robot.robot.sensors import RobotHardwareError
from inspection_robot.runtime import RobotRuntime, RobotRuntimeConfig


def main() -> int:
    shelf_id = os.environ.get("RUNTIME_SHELF", "A1").strip().upper() or "A1"
    max_steps = int(os.environ.get("RUNTIME_MAX_STEPS", "0")) or None
    config = RobotRuntimeConfig(
        step_seconds=float(os.environ.get("ROBOT_STEP_SECONDS", "0.12")),
        scan_timeout_seconds=float(os.environ.get("SCAN_TIMEOUT_SECONDS", "4")),
    )
    store = InspectionStore(
        load_tag_map(ROOT),
        warehouse_map=load_warehouse_map(ROOT),
        shelf_manifest=load_shelf_manifest(ROOT),
        root=ROOT,
    )
    runtime = RobotRuntime(store, store.warehouse_map, store.shelf_manifest, config=config)
    print(f"runtime waypoint test: shelf={shelf_id}, max_steps={max_steps or 'none'}", flush=True)
    try:
        runtime.run_patrol(shelf_order=[shelf_id], max_steps=max_steps)
    except KeyboardInterrupt:
        runtime.stop()
        print("stopped by user", flush=True)
    except RobotHardwareError as exc:
        print(f"hardware error: {exc}", flush=True)
        return 2
    snapshot = store.snapshot()
    print(json.dumps({"task_status": snapshot["task_status"], "events": snapshot["events"][:5]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
