from __future__ import annotations

import os
import time
from typing import Any

from .sensors import get_bot


COLOR_RED = 0
COLOR_GREEN = 1
COLOR_BLUE = 2
COLOR_YELLOW = 3
COLOR_PURPLE = 4
COLOR_CYAN = 5
COLOR_WHITE = 6


def _env_int(name: str, default: int, low: int = 0, high: int = 255) -> int:
    try:
        return max(low, min(high, int(os.environ.get(name, str(default)))))
    except (TypeError, ValueError):
        return default


# The 7-colour WS2812 palette has no orange, so recognition uses a custom RGB
# value driven through Ctrl_WQ2812_brightness_ALL (R, G, B) instead.
RECOGNITION_ORANGE_RGB = (
    _env_int("RECOGNITION_ORANGE_R", 255),
    _env_int("RECOGNITION_ORANGE_G", 70),
    _env_int("RECOGNITION_ORANGE_B", 0),
)


def show_normal() -> None:
    _set_rgb(COLOR_GREEN)
    _beep(0)


def show_obstacle_wait() -> None:
    _set_rgb(COLOR_YELLOW)
    _beep(0)


def show_warning() -> None:
    _set_rgb(COLOR_PURPLE)
    _beep(0)


def show_high_priority_alarm() -> None:
    _set_rgb(COLOR_RED)
    _beep(0)


def clear_alarm() -> None:
    bot = get_bot()
    _call_optional(bot, "Ctrl_BEEP_Switch", 0)
    _call_optional(bot, "Ctrl_WQ2812_ALL", 1, COLOR_GREEN)


def show_line_follow() -> None:
    _set_rgb(COLOR_CYAN)
    _beep(0)


def show_recognition() -> None:
    """Light the bar orange as a brief visual marker for a recognised target.

    Purely visual (no beep): the audio cue stays the primary alert and this is
    the extra indicator requested on top of it. The caller is responsible for
    restoring the base colour after the flash window.
    """

    _set_rgb_brightness(*RECOGNITION_ORANGE_RGB)


def _set_rgb(color_index: int) -> None:
    bot = get_bot()
    _call_optional(bot, "Ctrl_WQ2812_ALL", 1, color_index)


def _set_rgb_brightness(red: int, green: int, blue: int) -> None:
    bot = get_bot()
    _call_optional(bot, "Ctrl_WQ2812_brightness_ALL", int(red), int(green), int(blue))


def _beep(value: int) -> None:
    bot = get_bot()
    _call_optional(bot, "Ctrl_BEEP_Switch", value)


def _pulse_beep(count: int, pulse_seconds: float) -> None:
    for _ in range(count):
        _beep(1)
        time.sleep(pulse_seconds)
        _beep(0)
        time.sleep(pulse_seconds)


def _call_optional(bot: Any, name: str, *args: object) -> None:
    func = getattr(bot, name, None)
    if callable(func):
        func(*args)
