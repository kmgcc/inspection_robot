from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(slots=True)
class HeadingHoldSettings:
    enabled: bool = True
    tolerance_degrees: float = 3.0
    gain: float = 0.02
    min_pulse_seconds: float = 0.03
    max_pulse_seconds: float = 0.12
    correction_speed: int | None = None
    fallback_speed: int = 22
    invert: bool = False
    rate_damping: float = 0.02


@dataclass(slots=True)
class HeadingHoldPulse:
    deviation_degrees: float
    rate_dps: float
    effective_degrees: float
    direction: str
    pulse_seconds: float
    speed: int


def apply_heading_hold(
    guard: Any,
    motion_adapter: Any,
    settings: HeadingHoldSettings,
    *,
    stop_requested: Callable[[], bool] | None = None,
) -> HeadingHoldPulse | None:
    if not settings.enabled or guard is None:
        return None
    if stop_requested is not None and stop_requested():
        return None
    updater = getattr(guard, "update", None)
    if not callable(updater):
        return None

    deviation = float(updater())
    tolerance = max(0.0, float(settings.tolerance_degrees))
    if abs(deviation) <= tolerance:
        return None

    rate = _guard_rate(guard)
    damping = max(0.0, float(settings.rate_damping))
    effective = deviation + damping * rate
    if effective == 0.0 or (effective > 0.0) != (deviation > 0.0):
        return None

    pulse_seconds = min(
        max(abs(effective) * max(0.0, float(settings.gain)), float(settings.min_pulse_seconds)),
        float(settings.max_pulse_seconds),
    )
    if pulse_seconds <= 0:
        return None

    turn_right = effective > 0.0
    if settings.invert:
        turn_right = not turn_right
    speed = settings.correction_speed or max(1, int(settings.fallback_speed))
    mover = motion_adapter.rotate_right_slow if turn_right else motion_adapter.rotate_left_slow
    mover(speed=speed, duration_seconds=pulse_seconds)
    motion_adapter.stop()
    return HeadingHoldPulse(
        deviation_degrees=deviation,
        rate_dps=rate,
        effective_degrees=effective,
        direction="right" if turn_right else "left",
        pulse_seconds=pulse_seconds,
        speed=speed,
    )


def _guard_rate(guard: Any) -> float:
    try:
        return float(getattr(guard, "last_rate_dps", 0.0))
    except (TypeError, ValueError):
        return 0.0
