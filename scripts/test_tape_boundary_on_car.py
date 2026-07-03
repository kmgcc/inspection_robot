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
    min_black = int(os.environ.get("BOUNDARY_MIN_BLACK_SENSORS", "4"))
    deadline = time.monotonic() + seconds
    print(
        f"tape boundary test: 0 means black tape, turn only when >= {min_black} sensors are black; "
        "middle two black is line-follow center, not a turn trigger",
        flush=True,
    )
    try:
        while time.monotonic() < deadline:
            state = sensors.read_tape_boundary()
            description = sensors.describe_tape_boundary(state)
            print(json.dumps({"state": state, "description": description}, ensure_ascii=False), flush=True)
            if sensors.tape_boundary_count_detected(state, min_black=min_black):
                print("column-end black tape detected: stop and turn right 90", flush=True)
                motion.stop()
                motion.rotate_right_slow(duration_seconds=float(os.environ.get("ROBOT_TURN_90_SECONDS", "0.75")))
                motion.stop()
                time.sleep(float(os.environ.get("ROBOT_ACTION_SETTLE_SECONDS", "0.35")))
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
