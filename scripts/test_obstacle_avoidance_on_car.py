from __future__ import annotations

import os
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inspection_robot.robot import alarm, motion, sensors
from inspection_robot.robot.sensors import RobotHardwareError


def main() -> int:
    blocked_distance_mm = int(os.environ.get("BLOCKED_DISTANCE_MM", "200"))
    clear_distance_mm = int(os.environ.get("CLEAR_DISTANCE_MM", "280"))
    wait_seconds = float(os.environ.get("OBSTACLE_WAIT_SECONDS", "6"))
    blocked_count = 0
    print(
        "obstacle avoidance test: "
        f"blocked<{blocked_distance_mm}mm, clear>={clear_distance_mm}mm, wait={wait_seconds}s",
        flush=True,
    )
    try:
        while True:
            distance = sensors.read_distance_mm()
            print(f"distance_mm={distance}", flush=True)
            if distance is not None and distance < blocked_distance_mm:
                blocked_count += 1
            else:
                blocked_count = 0
            if blocked_count >= 3:
                print("obstacle confirmed: stop and wait", flush=True)
                motion.stop()
                alarm.show_obstacle_wait()
                if _wait_until_clear(clear_distance_mm, wait_seconds):
                    print("obstacle cleared: resume normal signal", flush=True)
                    alarm.clear_alarm()
                    blocked_count = 0
                else:
                    print("obstacle remains: turn left, stop, re-check before any forward move", flush=True)
                    motion.rotate_left_slow(duration_seconds=float(os.environ.get("ROBOT_TURN_90_SECONDS", "0.45")))
                    motion.stop()
                    distance = sensors.read_distance_mm()
                    print(f"after turn distance_mm={distance}", flush=True)
                    if distance is not None and distance >= clear_distance_mm:
                        motion.move_forward_slow(duration_seconds=float(os.environ.get("AVOIDANCE_BODY_SECONDS", "0.25")))
                        motion.stop()
                        motion.rotate_right_slow(duration_seconds=float(os.environ.get("ROBOT_TURN_90_SECONDS", "0.45")))
                        motion.stop()
                        blocked_count = 0
                        alarm.clear_alarm()
                    else:
                        print("still blocked after turn: keeping stopped", flush=True)
                        motion.stop()
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("stopped by user", flush=True)
    except RobotHardwareError as exc:
        print(f"hardware error: {exc}", flush=True)
        return 2
    finally:
        try:
            motion.stop()
            alarm.clear_alarm()
        except RobotHardwareError:
            pass
    return 0


def _wait_until_clear(clear_distance_mm: int, wait_seconds: float) -> bool:
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        time.sleep(0.25)
        distance = sensors.read_distance_mm()
        print(f"wait distance_mm={distance}", flush=True)
        if distance is not None and distance >= clear_distance_mm:
            return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
