from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inspection_robot.robot import motion, sensors
from inspection_robot.robot.sensors import RobotHardwareError


def main() -> int:
    seconds = float(os.environ.get("TAPE_TEST_SECONDS", "20"))
    deadline = time.monotonic() + seconds
    print("tape boundary test: 0 means black tape, 1 means white floor", flush=True)
    try:
        while time.monotonic() < deadline:
            state = sensors.read_tape_boundary()
            description = sensors.describe_tape_boundary(state)
            print(json.dumps({"state": state, "description": description}, ensure_ascii=False), flush=True)
            if sensors.tape_boundary_detected(state):
                print("black tape detected: stop and back up", flush=True)
                motion.stop()
                motion.move_backward_slow(duration_seconds=0.25)
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("stopped by user", flush=True)
    except RobotHardwareError as exc:
        print(f"hardware error: {exc}", flush=True)
        return 2
    finally:
        try:
            motion.stop()
        except RobotHardwareError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
