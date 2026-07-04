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
    blocked_distance_mm = int(os.environ.get("BLOCKED_DISTANCE_MM", "160"))
    clear_distance_mm = int(os.environ.get("CLEAR_DISTANCE_MM", "240"))
    wait_seconds = float(os.environ.get("OBSTACLE_WAIT_SECONDS", "6"))
    turn_seconds = float(os.environ.get("ROBOT_TURN_90_SECONDS", "0.75"))
    turn_speed = int(os.environ.get("ROBOT_TURN_SPEED", "18"))
    settle_seconds = float(os.environ.get("ROBOT_ACTION_SETTLE_SECONDS", "0.35"))
    avoidance_speed = int(os.environ.get("AVOIDANCE_SPEED", "14"))
    body_seconds = float(os.environ.get("AVOIDANCE_BODY_SECONDS", "1.00"))
    side_clearance_bodies = float(os.environ.get("AVOIDANCE_SIDE_CLEARANCE_BODIES", "1.0"))
    parallel_bodies = float(os.environ.get("AVOIDANCE_PARALLEL_BODIES", "2.0"))
    return_bodies = float(os.environ.get("AVOIDANCE_RETURN_BODIES", "2.0"))
    blocked_count = 0
    print(
        "obstacle avoidance test: "
        f"blocked<{blocked_distance_mm}mm, clear>={clear_distance_mm}mm, wait={wait_seconds}s, "
        f"right detour body={body_seconds}s speed={avoidance_speed}, "
        f"steps={side_clearance_bodies:g}/{parallel_bodies:g}/{return_bodies:g} bodies",
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
                    print("obstacle remains: right-side detour, then restore original heading", flush=True)
                    if _drive_right_detour(
                        clear_distance_mm,
                        turn_speed,
                        turn_seconds,
                        settle_seconds,
                        avoidance_speed,
                        body_seconds,
                        side_clearance_bodies,
                        parallel_bodies,
                        return_bodies,
                    ):
                        blocked_count = 0
                        alarm.clear_alarm()
                    else:
                        print("still blocked during detour: keeping stopped", flush=True)
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


def _drive_right_detour(
    clear_distance_mm: int,
    turn_speed: int,
    turn_seconds: float,
    settle_seconds: float,
    avoidance_speed: int,
    body_seconds: float,
    side_clearance_bodies: float,
    parallel_bodies: float,
    return_bodies: float,
) -> bool:
    steps = [
        ("turn right 90", lambda: motion.rotate_right_slow(speed=turn_speed, duration_seconds=turn_seconds)),
        (
            f"forward {side_clearance_bodies:g} body to the right side",
            lambda: motion.move_forward_slow(speed=avoidance_speed, duration_seconds=body_seconds * max(0.0, side_clearance_bodies)),
        ),
        ("turn left 90 to original heading", lambda: motion.rotate_left_slow(speed=turn_speed, duration_seconds=turn_seconds)),
        (
            f"forward {parallel_bodies:g} bodies parallel past obstacle",
            lambda: motion.move_forward_slow(speed=avoidance_speed, duration_seconds=body_seconds * max(0.0, parallel_bodies)),
        ),
        ("turn left 90 back toward patrol line", lambda: motion.rotate_left_slow(speed=turn_speed, duration_seconds=turn_seconds)),
        (
            f"forward {return_bodies:g} bodies return to patrol line",
            lambda: motion.move_forward_slow(speed=avoidance_speed, duration_seconds=body_seconds * max(0.0, return_bodies)),
        ),
        ("turn right 90 restore heading", lambda: motion.rotate_right_slow(speed=turn_speed, duration_seconds=turn_seconds)),
    ]
    for label, action in steps:
        print(label, flush=True)
        action()
        motion.stop()
        time.sleep(max(0.0, settle_seconds))
        distance = sensors.read_distance_mm()
        print(f"after {label}: distance_mm={distance}", flush=True)
        if distance is not None and distance < clear_distance_mm:
            return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
