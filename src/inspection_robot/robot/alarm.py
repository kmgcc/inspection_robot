from __future__ import annotations

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


def show_normal() -> None:
    _set_rgb(COLOR_GREEN)
    _beep(0)


def show_obstacle_wait() -> None:
    _set_rgb(COLOR_YELLOW)
    _pulse_beep(1, 0.08)


def show_warning() -> None:
    _set_rgb(COLOR_PURPLE)
    _pulse_beep(2, 0.08)


def show_high_priority_alarm() -> None:
    _set_rgb(COLOR_RED)
    _pulse_beep(3, 0.08)


def clear_alarm() -> None:
    bot = get_bot()
    _call_optional(bot, "Ctrl_BEEP_Switch", 0)
    _call_optional(bot, "Ctrl_WQ2812_ALL", 1, COLOR_GREEN)


def show_line_follow() -> None:
    _set_rgb(COLOR_CYAN)
    _beep(0)


def _set_rgb(color_index: int) -> None:
    bot = get_bot()
    _call_optional(bot, "Ctrl_WQ2812_ALL", 1, color_index)


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
