from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from types import ModuleType

from .sensors import RobotHardwareError


DEFAULT_SPEED = int(os.environ.get("ROBOT_SLOW_SPEED", "22"))
DEFAULT_STEP_SECONDS = float(os.environ.get("ROBOT_STEP_SECONDS", "0.14"))
COMMAND_REPEAT = max(1, int(os.environ.get("ROBOT_COMMAND_REPEAT", "2")))
COMMAND_REPEAT_GAP_SECONDS = float(os.environ.get("ROBOT_COMMAND_REPEAT_GAP_SECONDS", "0.02"))
VENDOR_MOTION_PATHS = (
    Path("/home/pi/project_demo/lib"),
    Path("/home/pi/project_demo/04.Car_motion_control"),
)


def move_forward_slow(speed: int | None = None, duration_seconds: float | None = None) -> None:
    _call_motion("move_forward", speed, duration_seconds)


def move_backward_slow(speed: int | None = None, duration_seconds: float | None = None) -> None:
    _call_motion("move_backward", speed, duration_seconds)


def strafe_left_slow(speed: int | None = None, duration_seconds: float | None = None) -> None:
    _call_motion("move_left", speed, duration_seconds)


def strafe_right_slow(speed: int | None = None, duration_seconds: float | None = None) -> None:
    _call_motion("move_right", speed, duration_seconds)


def rotate_left_slow(speed: int | None = None, duration_seconds: float | None = None) -> None:
    _call_motion("rotate_left", speed, duration_seconds)


def rotate_right_slow(speed: int | None = None, duration_seconds: float | None = None) -> None:
    _call_motion("rotate_right", speed, duration_seconds)


def stop() -> None:
    module = _motion_module()
    for name in ("stop_robot", "stop", "car_stop", "motor_stop"):
        func = getattr(module, name, None)
        if callable(func):
            func()
            return
    fallback = getattr(module, "move_forward", None)
    if callable(fallback):
        fallback(0)
        return
    raise RobotHardwareError("McLumk_Wheel_Sports does not expose a stop function")


def _call_motion(name: str, speed: int | None, duration_seconds: float | None) -> None:
    module = _motion_module()
    func = getattr(module, name, None)
    if not callable(func):
        raise RobotHardwareError(f"McLumk_Wheel_Sports is missing {name}()")
    for index in range(COMMAND_REPEAT):
        func(_speed(speed))
        if index < COMMAND_REPEAT - 1:
            time.sleep(max(0.0, COMMAND_REPEAT_GAP_SECONDS))
    time.sleep(_duration(duration_seconds))


def _speed(speed: int | None) -> int:
    value = DEFAULT_SPEED if speed is None else int(speed)
    return max(0, min(value, 100))


def _duration(duration_seconds: float | None) -> float:
    if duration_seconds is None:
        return DEFAULT_STEP_SECONDS
    return max(0.0, float(duration_seconds))


def _motion_module() -> ModuleType:
    try:
        import McLumk_Wheel_Sports  # type: ignore[import-not-found]
    except ImportError as exc:
        _add_vendor_paths()
        try:
            import McLumk_Wheel_Sports  # type: ignore[import-not-found,no-redef]
        except ImportError as retry_exc:
            searched = ", ".join(str(path) for path in VENDOR_MOTION_PATHS)
            raise RobotHardwareError(
                "McLumk_Wheel_Sports is not available. Run this on the RASPBOT image, "
                f"or add the vendor library to PYTHONPATH. Searched: {searched}"
            ) from retry_exc
    return McLumk_Wheel_Sports


def _add_vendor_paths() -> None:
    for path in VENDOR_MOTION_PATHS:
        if path.exists() and str(path) not in sys.path:
            sys.path.insert(0, str(path))
