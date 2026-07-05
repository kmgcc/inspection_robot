from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(slots=True)
class HeadingHoldSettings:
    enabled: bool = True
    tolerance_degrees: float = 2.5
    gain: float = 0.012
    min_pulse_seconds: float = 0.03
    max_pulse_seconds: float = 0.12
    correction_speed: int | None = 16
    fallback_speed: int = 22
    invert: bool = False
    rate_damping: float = 0.18
    speed_gain: float = 1.8
    min_correction_speed: int = 4
    max_speed_fraction: float = 0.8
    integral_degrees: float = 0.0
    integral_gain: float = 0.0
    previous_correction_speed: int = 0
    max_correction_step: int = 0


@dataclass(slots=True)
class HeadingHoldPulse:
    deviation_degrees: float
    rate_dps: float
    effective_degrees: float
    direction: str
    pulse_seconds: float
    speed: int


@dataclass(slots=True)
class HeadingHoldCorrection:
    deviation_degrees: float
    rate_dps: float
    effective_degrees: float
    direction: str
    correction_speed: int


def apply_heading_hold(
    guard: Any,
    motion_adapter: Any,
    settings: HeadingHoldSettings,
    *,
    stop_requested: Callable[[], bool] | None = None,
    deviation_degrees: float | None = None,
) -> HeadingHoldPulse | None:
    correction = compute_heading_hold_correction(
        guard,
        settings,
        stop_requested=stop_requested,
        deviation_degrees=deviation_degrees,
    )
    if correction is None:
        return None

    pulse_seconds = min(
        max(abs(correction.effective_degrees) * max(0.0, float(settings.gain)), float(settings.min_pulse_seconds)),
        float(settings.max_pulse_seconds),
    )
    if pulse_seconds <= 0:
        return None

    mover = motion_adapter.rotate_right_slow if correction.direction == "right" else motion_adapter.rotate_left_slow
    motion_adapter.stop()
    mover(speed=correction.correction_speed, duration_seconds=pulse_seconds)
    motion_adapter.stop()
    reset = getattr(guard, "reset", None)
    if callable(reset):
        reset()
    return HeadingHoldPulse(
        deviation_degrees=correction.deviation_degrees,
        rate_dps=correction.rate_dps,
        effective_degrees=correction.effective_degrees,
        direction=correction.direction,
        pulse_seconds=pulse_seconds,
        speed=correction.correction_speed,
    )


def compute_heading_hold_correction(
    guard: Any,
    settings: HeadingHoldSettings,
    *,
    stop_requested: Callable[[], bool] | None = None,
    deviation_degrees: float | None = None,
) -> HeadingHoldCorrection | None:
    if not settings.enabled or guard is None:
        return None
    if stop_requested is not None and stop_requested():
        return None
    if deviation_degrees is None:
        updater = getattr(guard, "update", None)
        if not callable(updater):
            return None
        deviation = float(updater())
    else:
        deviation = float(deviation_degrees)
    tolerance = max(0.0, float(settings.tolerance_degrees))
    if abs(deviation) <= tolerance:
        return None

    rate = _guard_rate(guard)
    damping = max(0.0, float(settings.rate_damping))
    integral = float(settings.integral_degrees) * max(0.0, float(settings.integral_gain))
    effective = deviation + damping * rate + integral
    if effective == 0.0 or (effective > 0.0) != (deviation > 0.0):
        return None

    # With the MPU6050 mounted chip-up, positive yaw means the chassis has
    # drifted left (CCW). The corrective steering direction is therefore right.
    turn_right = effective > 0.0
    if settings.invert:
        turn_right = not turn_right
    fallback_speed = max(1, int(settings.fallback_speed))
    max_speed_fraction = max(0.0, float(settings.max_speed_fraction))
    max_speed = min(
        settings.correction_speed or fallback_speed,
        max(1, int(round(fallback_speed * max_speed_fraction))),
    )
    target_speed = min(
        max_speed,
        max(int(settings.min_correction_speed), int(round(abs(effective) * max(0.0, float(settings.speed_gain))))),
    )
    max_step = max(0, int(settings.max_correction_step))
    previous_speed = int(settings.previous_correction_speed)
    previous_direction = 1 if previous_speed > 0 else -1 if previous_speed < 0 else 0
    target_direction = 1 if turn_right else -1
    if max_step > 0 and previous_direction in {0, target_direction}:
        previous_abs = abs(previous_speed)
        if previous_abs > 0:
            target_speed = min(target_speed, max(previous_abs + max_step, int(settings.min_correction_speed)))
        elif target_speed > int(settings.min_correction_speed):
            target_speed = min(target_speed, max(max_step, int(settings.min_correction_speed)))
    correction_speed = target_speed
    if correction_speed <= 0:
        return None
    return HeadingHoldCorrection(
        deviation_degrees=deviation,
        rate_dps=rate,
        effective_degrees=effective,
        direction="right" if turn_right else "left",
        correction_speed=correction_speed,
    )


def _guard_rate(guard: Any) -> float:
    try:
        return float(getattr(guard, "last_rate_dps", 0.0))
    except (TypeError, ValueError):
        return 0.0
