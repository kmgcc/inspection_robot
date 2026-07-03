from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ULTRASONIC_HIGH_REGISTER = 0x1B
ULTRASONIC_LOW_REGISTER = 0x1A
LINE_SENSOR_REGISTER = 0x0A


@dataclass(frozen=True, slots=True)
class RobotHardwareError(RuntimeError):
    message: str

    def __str__(self) -> str:
        return self.message


_BOT: Any | None = None


def read_distance_mm() -> int | None:
    """Read ultrasonic distance in millimeters.

    The Yahboom examples return high/low bytes from two registers. Temporary
    I2C read failures return None so runtime can wait instead of crashing.
    """

    bot = get_bot()
    try:
        _call_optional(bot, "Ctrl_Ulatist_Switch", 1)
        high = _first_int(bot.read_data_array(ULTRASONIC_HIGH_REGISTER, 1))
        low = _first_int(bot.read_data_array(ULTRASONIC_LOW_REGISTER, 1))
    except (OSError, TypeError, ValueError, AttributeError, IndexError):
        return None
    if high is None or low is None:
        return None
    distance = (high << 8) | low
    if distance <= 0 or distance > 5000:
        return None
    return distance


def read_tape_boundary() -> tuple[int, int, int, int] | None:
    """Read the four line sensors as left, left-center, right-center, right.

    The official examples use 0 for black tape and 1 for white floor. The raw
    four-channel state is preserved so runtime can tell which side tripped.
    """

    bot = get_bot()
    try:
        raw = bot.read_data_array(LINE_SENSOR_REGISTER, 1)
    except (OSError, TypeError, ValueError, AttributeError):
        return None
    return normalize_tape_state(raw)


def normalize_tape_state(raw: Any) -> tuple[int, int, int, int] | None:
    if raw is None:
        return None
    if isinstance(raw, int):
        return _decode_bitfield(raw)
    if not isinstance(raw, (list, tuple)):
        return None
    values = [int(value) for value in raw]
    if len(values) >= 4:
        return tuple(_binary(value) for value in values[:4])  # type: ignore[return-value]
    if len(values) == 1:
        return _decode_bitfield(values[0])
    return None


def tape_boundary_detected(state: tuple[int, int, int, int] | None) -> bool:
    return state is not None and any(value == 0 for value in state)


def black_tape_count(state: tuple[int, int, int, int] | None) -> int:
    if state is None:
        return 0
    return sum(1 for value in state if value == 0)


def full_tape_boundary_detected(state: tuple[int, int, int, int] | None) -> bool:
    return state is not None and all(value == 0 for value in state)


def tape_boundary_count_detected(state: tuple[int, int, int, int] | None, min_black: int = 2) -> bool:
    return black_tape_count(state) >= max(1, min(4, int(min_black)))


def describe_tape_boundary(state: tuple[int, int, int, int] | None) -> dict[str, bool | None]:
    if state is None:
        return {
            "left_detected": None,
            "right_detected": None,
            "front_or_center_detected": None,
            "any_detected": None,
        }
    left, left_center, right_center, right = state
    left_detected = left == 0 or left_center == 0
    right_detected = right == 0 or right_center == 0
    front_or_center_detected = left_center == 0 or right_center == 0
    return {
        "left_detected": left_detected,
        "right_detected": right_detected,
        "front_or_center_detected": front_or_center_detected,
        "any_detected": left_detected or right_detected,
    }


def get_bot() -> Any:
    global _BOT
    if _BOT is None:
        try:
            from Raspbot_Lib import Raspbot  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RobotHardwareError(
                "Raspbot_Lib is not available. Run this on the RASPBOT image or install the vendor library."
            ) from exc
        _BOT = Raspbot()
    return _BOT


def reset_bot_for_tests() -> None:
    global _BOT
    _BOT = None


def _first_int(raw: Any) -> int | None:
    if isinstance(raw, int):
        return raw
    if isinstance(raw, (list, tuple)) and raw:
        return int(raw[0])
    return None


def _decode_bitfield(value: int) -> tuple[int, int, int, int]:
    return tuple(_binary((value >> shift) & 1) for shift in (3, 2, 1, 0))  # type: ignore[return-value]


def _binary(value: int) -> int:
    return 1 if int(value) else 0


def _call_optional(bot: Any, name: str, *args: object) -> None:
    func = getattr(bot, name, None)
    if callable(func):
        func(*args)
