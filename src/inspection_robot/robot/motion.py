from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from types import ModuleType

from .sensors import RobotHardwareError


DEFAULT_SPEED = int(os.environ.get("ROBOT_SLOW_SPEED", "22"))
MIN_RUNNING_SPEED = max(0, int(os.environ.get("ROBOT_MIN_SPEED", "5")))
DEFAULT_STEP_SECONDS = float(os.environ.get("ROBOT_STEP_SECONDS", "0.14"))
COMMAND_REPEAT = max(1, int(os.environ.get("ROBOT_COMMAND_REPEAT", "2")))
COMMAND_REPEAT_GAP_SECONDS = float(os.environ.get("ROBOT_COMMAND_REPEAT_GAP_SECONDS", "0.02"))
STOP_POLL_SECONDS = float(os.environ.get("ROBOT_STOP_POLL_SECONDS", "0.01"))
STOP_REPEAT = max(1, int(os.environ.get("ROBOT_STOP_REPEAT", "8")))
STOP_REPEAT_GAP_SECONDS = float(os.environ.get("ROBOT_STOP_REPEAT_GAP_SECONDS", "0.08"))
FORWARD_MOTOR_SIGNS = os.environ.get("ROBOT_FORWARD_MOTOR_SIGNS", "1,1,1,1")
VENDOR_MOTION_PATHS = (
    Path("/home/pi/project_demo/lib"),
    Path("/home/pi/project_demo/04.Car_motion_control"),
)
_ABORT = threading.Event()
_MOTION_LOCK = threading.RLock()


def move_forward_slow(speed: int | None = None, duration_seconds: float | None = None) -> None:
    _call_motion("move_forward", speed, duration_seconds)


def move_forward_corrected_slow(
    *,
    speed: int | None = None,
    correction: int = 0,
    direction: str = "right",
    duration_seconds: float | None = None,
) -> None:
    _call_forward_corrected(speed, correction, direction, duration_seconds)


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
    with _MOTION_LOCK:
        module = _motion_module()
        if _force_stop_all_motors(module):
            return
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


def request_stop() -> None:
    _ABORT.set()
    try:
        stop()
    except RobotHardwareError:
        pass


def clear_stop() -> None:
    _ABORT.clear()


def is_stop_requested() -> bool:
    return _ABORT.is_set()


def _call_motion(name: str, speed: int | None, duration_seconds: float | None) -> None:
    if _ABORT.is_set():
        _stop_quietly()
        return
    with _MOTION_LOCK:
        if _ABORT.is_set():
            _stop_quietly()
            return
        module = _motion_module()
        func = getattr(module, name, None)
        if not callable(func):
            raise RobotHardwareError(f"McLumk_Wheel_Sports is missing {name}()")
        for index in range(COMMAND_REPEAT):
            if _ABORT.is_set():
                _stop_quietly()
                return
            func(_speed(speed))
            if index < COMMAND_REPEAT - 1:
                _interruptible_sleep(max(0.0, COMMAND_REPEAT_GAP_SECONDS))
        _interruptible_sleep(_duration(duration_seconds))
        if _ABORT.is_set():
            _stop_quietly()


def _call_forward_corrected(
    speed: int | None,
    correction: int,
    direction: str,
    duration_seconds: float | None,
) -> None:
    if _ABORT.is_set():
        _stop_quietly()
        return
    base_speed = _speed(speed)
    correction_speed = max(0, min(int(correction), base_speed))
    normalized_direction = str(direction).strip().lower()
    if correction_speed <= 0 or normalized_direction not in {"left", "right"}:
        _call_motion("move_forward", base_speed, duration_seconds)
        return
    with _MOTION_LOCK:
        if _ABORT.is_set():
            _stop_quietly()
            return
        module = _motion_module()
        bot = getattr(module, "bot", None)
        ctrl_muto = getattr(bot, "Ctrl_Muto", None)
        if not callable(ctrl_muto):
            _call_motion("move_forward", base_speed, duration_seconds)
            return
        left_speed = base_speed
        right_speed = base_speed
        if normalized_direction == "right":
            left_speed = min(100, base_speed + correction_speed)
            right_speed = max(0, base_speed - correction_speed)
        else:
            left_speed = max(0, base_speed - correction_speed)
            right_speed = min(100, base_speed + correction_speed)
        wheel_speeds = (left_speed, left_speed, right_speed, right_speed)
        signs = _forward_motor_signs()
        for index in range(COMMAND_REPEAT):
            if _ABORT.is_set():
                _stop_quietly()
                return
            for motor_id, wheel_speed in enumerate(wheel_speeds):
                ctrl_muto(motor_id, _signed_speed(wheel_speed, signs[motor_id]))
            if index < COMMAND_REPEAT - 1:
                _interruptible_sleep(max(0.0, COMMAND_REPEAT_GAP_SECONDS))
        _interruptible_sleep(_duration(duration_seconds))
        if _ABORT.is_set():
            _stop_quietly()


def _speed(speed: int | None) -> int:
    value = DEFAULT_SPEED if speed is None else int(speed)
    if value <= 0:
        return 0
    return max(MIN_RUNNING_SPEED, min(value, 100))


def _signed_speed(speed: int, sign: int) -> int:
    value = _speed(speed)
    if value == 0:
        return 0
    return value if sign >= 0 else -value


def _forward_motor_signs() -> tuple[int, int, int, int]:
    raw_values = [part.strip() for part in FORWARD_MOTOR_SIGNS.replace(";", ",").split(",")]
    signs: list[int] = []
    for raw in raw_values[:4]:
        try:
            signs.append(1 if int(raw) >= 0 else -1)
        except ValueError:
            signs.append(1)
    while len(signs) < 4:
        signs.append(1)
    return tuple(signs)  # type: ignore[return-value]


def _duration(duration_seconds: float | None) -> float:
    if duration_seconds is None:
        return DEFAULT_STEP_SECONDS
    return max(0.0, float(duration_seconds))


def _interruptible_sleep(seconds: float) -> None:
    deadline = time.monotonic() + max(0.0, float(seconds))
    while not _ABORT.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        _ABORT.wait(min(STOP_POLL_SECONDS, remaining))


def _stop_quietly() -> None:
    try:
        stop()
    except RobotHardwareError:
        pass


def _force_stop_all_motors(module: ModuleType) -> bool:
    bot = getattr(module, "bot", None)
    ctrl_muto = getattr(bot, "Ctrl_Muto", None)
    ctrl_car = getattr(bot, "Ctrl_Car", None)
    if not callable(ctrl_muto) and not callable(ctrl_car):
        return False
    for index in range(STOP_REPEAT):
        for motor_id in range(4):
            if callable(ctrl_muto):
                ctrl_muto(motor_id, 0)
            if callable(ctrl_car):
                ctrl_car(motor_id, 0, 0)
        if index < STOP_REPEAT - 1:
            time.sleep(max(0.0, STOP_REPEAT_GAP_SECONDS))
    return True


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
