from __future__ import annotations

import importlib
import os
import sys
import threading
import time
from collections.abc import Mapping
from typing import Any


DEFAULT_OLED_PATH = "/home/pi/software/oled_yahboom"
_LOCK = threading.Lock()
_OLED: Any | None = None
_DISABLED_REASON: str | None = None
_LAST_REFRESH_AT = 0.0


def update_motion_sensor(sample: Mapping[str, object]) -> None:
    """Render the latest MPU6050 yaw on the Yahboom OLED when available."""
    if os.environ.get("OLED_DISPLAY_ENABLED", "1").strip().lower() in {"0", "false", "no", "off"}:
        return

    interval = _refresh_interval_seconds()
    now = time.monotonic()
    global _LAST_REFRESH_AT
    with _LOCK:
        if _DISABLED_REASON is not None:
            return
        if now - _LAST_REFRESH_AT < interval:
            return
        _LAST_REFRESH_AT = now
        try:
            oled = _oled()
            _render_motion_sensor(oled, sample)
        except Exception as exc:  # pragma: no cover - hardware/library dependent
            _disable(str(exc))


def disabled_reason() -> str | None:
    with _LOCK:
        return _DISABLED_REASON


def reset_for_test() -> None:
    global _OLED, _DISABLED_REASON, _LAST_REFRESH_AT
    with _LOCK:
        _OLED = None
        _DISABLED_REASON = None
        _LAST_REFRESH_AT = 0.0


def _oled() -> Any:
    global _OLED
    if _OLED is not None:
        return _OLED
    path = os.environ.get("YAHBOOM_OLED_PATH", DEFAULT_OLED_PATH)
    if path and path not in sys.path:
        sys.path.append(path)
    module = importlib.import_module("yahboom_oled")
    oled_class = getattr(module, "Yahboom_OLED")
    oled = oled_class(debug=False)
    initializer = getattr(oled, "init_oled_process", None)
    if callable(initializer):
        initializer()
    _OLED = oled
    return oled


def _render_motion_sensor(oled: Any, sample: Mapping[str, object]) -> None:
    ok = bool(sample.get("ok"))
    orientation = sample.get("orientation_deg")
    yaw = orientation.get("yaw") if isinstance(orientation, Mapping) else None
    last_turn = sample.get("last_turn")
    turn_text = _turn_text(last_turn) if isinstance(last_turn, Mapping) else "Turn: -"

    oled.clear()
    oled.add_line("Inspection Robot", 1)
    oled.add_line("MPU6050 OK" if ok else "MPU6050 ERR", 2)
    oled.add_line(_yaw_text(yaw), 3)
    oled.add_line(turn_text, 4)
    oled.refresh()


def _yaw_text(value: object) -> str:
    try:
        yaw = float(value)
    except (TypeError, ValueError):
        return "Yaw: -"
    return f"Yaw:{yaw:7.1f} deg"


def _turn_text(last_turn: Mapping[str, object]) -> str:
    direction = str(last_turn.get("direction") or "-")[:5]
    error = last_turn.get("error_degrees")
    try:
        error_value = float(error)
    except (TypeError, ValueError):
        return f"Turn:{direction} err:-"
    return f"Turn:{direction} err:{error_value:4.1f}"


def _refresh_interval_seconds() -> float:
    try:
        return max(0.1, float(os.environ.get("OLED_REFRESH_SECONDS", "0.75")))
    except ValueError:
        return 0.75


def _disable(reason: str) -> None:
    global _DISABLED_REASON
    _DISABLED_REASON = reason
