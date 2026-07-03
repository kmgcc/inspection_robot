from __future__ import annotations

import os
import time
from typing import Any

from .sensors import RobotHardwareError, get_bot


DEFAULT_YAW_ANGLE = int(os.environ.get("CAMERA_SERVO_YAW", "60"))
DEFAULT_PITCH_ANGLE = int(os.environ.get("CAMERA_SERVO_PITCH", "25"))
DEFAULT_SETTLE_SECONDS = float(os.environ.get("CAMERA_SERVO_SETTLE_SECONDS", "0.4"))


def initialize_side_camera(
    yaw_angle: int | None = None,
    pitch_angle: int | None = None,
    settle_seconds: float | None = None,
    bot: Any | None = None,
) -> None:
    controller = bot or get_bot()
    func = getattr(controller, "Ctrl_Servo", None)
    if not callable(func):
        raise RobotHardwareError("Raspbot_Lib does not expose Ctrl_Servo() for camera gimbal control")
    func(1, _angle(DEFAULT_YAW_ANGLE if yaw_angle is None else yaw_angle))
    func(2, _angle(DEFAULT_PITCH_ANGLE if pitch_angle is None else pitch_angle))
    time.sleep(max(0.0, DEFAULT_SETTLE_SECONDS if settle_seconds is None else float(settle_seconds)))


def _angle(value: int) -> int:
    return max(0, min(int(value), 180))
