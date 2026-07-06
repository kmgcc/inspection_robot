"""
test_mode.py — 运动测试模式（标定参数管理 + 测试会话执行）

设计原则：
- 所有电机操作均在 daemon 线程执行，stop_event 随时可中断
- 无论何种停止原因（正常结束 / 手动停止 / 异常），finally 块确保调用 motion.stop()
- 测试会话由 TestSessionManager 通过 Lock 串行化，防止并发冲突
- 标定参数持久化到 config/calibration.json，带合理默认值
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from .robot import motion, mpu6050, sensors
from .robot.heading_hold import HeadingHoldSettings, compute_heading_hold_correction
from .robot.line_following import decide_line_follow_motion, describe_tape
from .robot.sensors import RobotHardwareError

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_optional_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# 标定参数
# --------------------------------------------------------------------------- #

CALIBRATION_DEFAULTS: dict[str, Any] = {
    "_note": (
        "此文件由运动测试页面保存。首次使用前请在测试模式中完成实测标定，"
        "_uncalibrated 为 true 时界面会显示醒目提示。"
    ),
    "_uncalibrated": False,
    "straight_min_speed": 12,       # 最低稳定直行速度
    "straight_speed": 20,           # 直行测试默认速度
    "straight_step_seconds": 2.0,    # 直行测试默认时长(s)
    "patrol_step_seconds": 0.18,
    "patrol_settle_seconds": 0.05,
    "action_settle_seconds": 0.7,
    "turn_speed": 30,               # 转向测试默认速度
    "turn_cw90_seconds": 0.62,       # 顺时针约90°所需时间(s)
    "turn_ccw90_seconds": 0.62,      # 逆时针约90°所需时间(s)
    "cw_compensation": 1.0,          # 顺时针左右轮补偿系数
    "ccw_compensation": 1.0,         # 逆时针左右轮补偿系数
    "line_follow_speed": 30,         # 寻线测试速度
    "line_follow_step_seconds": 0.14, # 寻线每步时长(s)
}

CALIBRATION_ALLOWED_KEYS = {
    "straight_min_speed",
    "straight_speed",
    "straight_step_seconds",
    "patrol_step_seconds",
    "patrol_settle_seconds",
    "action_settle_seconds",
    "turn_speed",
    "turn_cw90_seconds",
    "turn_ccw90_seconds",
    "cw_compensation",
    "ccw_compensation",
    "line_follow_speed",
    "line_follow_step_seconds",
    "_uncalibrated",
}


class CalibrationStore:
    """读写 config/calibration.json，提供线程安全的访问接口。"""

    def __init__(self, root: Path) -> None:
        self._path = root / "config" / "calibration.json"
        self._lock = threading.Lock()

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self._path.exists():
                return CALIBRATION_DEFAULTS.copy()
            try:
                with self._path.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                # 补全缺失的键（向后兼容）
                merged = CALIBRATION_DEFAULTS.copy()
                merged.update({k: v for k, v in data.items() if k in CALIBRATION_ALLOWED_KEYS or k.startswith("_")})
                return merged
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("calibration.json 读取失败，使用默认值: %s", exc)
                return CALIBRATION_DEFAULTS.copy()

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        """部分更新标定参数并持久化，返回更新后的完整参数。"""
        with self._lock:
            current = self._read_unlocked()
            for key, value in patch.items():
                if key not in CALIBRATION_ALLOWED_KEYS:
                    continue
                current[key] = value
            # 若有任意实测值写入，清除 _uncalibrated 标记
            if any(k != "_uncalibrated" for k in patch if k in CALIBRATION_ALLOWED_KEYS):
                current["_uncalibrated"] = False
            self._write_unlocked(current)
            return current.copy()

    def _read_unlocked(self) -> dict[str, Any]:
        if not self._path.exists():
            return CALIBRATION_DEFAULTS.copy()
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return CALIBRATION_DEFAULTS.copy()

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
        except OSError as exc:
            logger.error("标定参数写入失败: %s", exc)


# --------------------------------------------------------------------------- #
# 测试会话状态
# --------------------------------------------------------------------------- #

@dataclass
class TestStatus:
    active: bool = False
    test_type: str = "none"          # "straight" / "turn" / "line_follow" / "none"
    direction: str = ""              # "forward" / "backward" / "cw" / "ccw"
    speed: int = 0
    duration_seconds: float = 0.0
    elapsed_seconds: float = 0.0
    stop_reason: str | None = None   # None=未停止, "completed"/"manual"/"error"/"timeout"
    error_message: str | None = None
    started_at: float = 0.0


@dataclass
class SensorStatus:
    line_sensor: tuple[int, int, int, int] | None = None
    line_description: str = "未读取"
    distance_mm: int | None = None


# --------------------------------------------------------------------------- #
# 测试会话管理器
# --------------------------------------------------------------------------- #

class TestSessionManager:
    """
    管理当前运动测试会话。

    - 同一时刻只允许一个测试会话运行（后来者会先 stop 前者）
    - 所有测试在 daemon 线程执行
    - stop() 可从任意线程安全调用
    """

    def __init__(self, motion_adapter: Any = motion, sensor_adapter: Any = sensors, imu_adapter: Any = mpu6050) -> None:
        self._motion = motion_adapter
        self._sensors = sensor_adapter
        self._imu = imu_adapter
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._status = TestStatus()
        self._sensor_status = SensorStatus()
        self._heading_guard: Any | None = None

    # ------------------------------------------------------------------ #
    # 公开 API
    # ------------------------------------------------------------------ #

    def stop(self) -> None:
        """立即停止当前测试，关闭全部电机输出。"""
        self._stop_event.set()
        try:
            stopper = getattr(self._motion, "request_stop", None)
            if callable(stopper):
                stopper()
            else:
                motion.request_stop()
                fallback_stop = getattr(self._motion, "stop", None)
                if callable(fallback_stop):
                    fallback_stop()
        except RobotHardwareError as exc:
            logger.warning("stop() 电机停止异常: %s", exc)
        with self._lock:
            if self._status.active:
                self._status.active = False
                self._status.stop_reason = "manual"
                self._status.elapsed_seconds = self._elapsed()
        logger.info(
            "[test] stop called | type=%s direction=%s speed=%s",
            self._status.test_type,
            self._status.direction,
            self._status.speed,
        )

    def run_straight_test(self, direction: str, speed: int, duration_seconds: float) -> None:
        """直行速度测试（前进或后退，固定时长）。"""
        norm_dir = direction.strip().lower()
        if norm_dir not in {"forward", "backward"}:
            raise ValueError(f"无效方向: {direction}，应为 forward 或 backward")
        self._start_session(
            test_type="straight",
            direction=norm_dir,
            speed=speed,
            duration_seconds=duration_seconds,
            target=self._straight_worker,
            args=(norm_dir, speed, duration_seconds),
        )

    def run_turn_test(self, direction: str, speed: int, duration_seconds: float) -> None:
        """原地转向测试（顺时针CW或逆时针CCW）。"""
        norm_dir = direction.strip().lower()
        if norm_dir not in {"cw", "ccw", "right", "left"}:
            raise ValueError(f"无效方向: {direction}，应为 cw/ccw 或 right/left")
        # 统一为 cw/ccw
        if norm_dir in {"right", "cw"}:
            norm_dir = "cw"
        else:
            norm_dir = "ccw"
        self._start_session(
            test_type="turn",
            direction=norm_dir,
            speed=speed,
            duration_seconds=duration_seconds,
            target=self._turn_worker,
            args=(norm_dir, speed, duration_seconds),
        )

    def run_line_follow_test(self, speed: int, step_seconds: float) -> None:
        """寻线测试（持续循环，直到手动 stop）。"""
        self._start_session(
            test_type="line_follow",
            direction="forward",
            speed=speed,
            duration_seconds=0.0,  # 无预设时长，手动停止
            target=self._line_follow_worker,
            args=(speed, step_seconds),
        )

    def get_status(self) -> dict[str, Any]:
        """返回当前测试状态 + 最新传感器读数。"""
        with self._lock:
            st = self._status
            ss = self._sensor_status
            elapsed = self._elapsed() if st.active else st.elapsed_seconds
            return {
                "active": st.active,
                "test_type": st.test_type,
                "direction": st.direction,
                "speed": st.speed,
                "duration_seconds": st.duration_seconds,
                "elapsed_seconds": round(elapsed, 2),
                "stop_reason": st.stop_reason,
                "error_message": st.error_message,
                "line_sensor": list(ss.line_sensor) if ss.line_sensor else None,
                "line_description": ss.line_description,
                "distance_mm": ss.distance_mm,
            }

    def read_sensors_now(self) -> None:
        """读取一次传感器，更新内部状态（供定时器或状态接口调用）。"""
        with self._lock:
            self._update_sensor_status()

    # ------------------------------------------------------------------ #
    # 内部：会话启动
    # ------------------------------------------------------------------ #

    def _start_session(
        self,
        *,
        test_type: str,
        direction: str,
        speed: int,
        duration_seconds: float,
        target: Any,
        args: tuple,
    ) -> None:
        # 先停止当前会话
        self.stop()
        # 清除停止事件，启动新的测试会话
        clearer = getattr(self._motion, "clear_stop", None)
        if callable(clearer):
            clearer()
        else:
            motion.clear_stop()
        self._stop_event.clear()
        with self._lock:
            self._status = TestStatus(
                active=True,
                test_type=test_type,
                direction=direction,
                speed=max(0, min(100, int(speed))),
                duration_seconds=float(duration_seconds),
                started_at=time.monotonic(),
            )
        logger.info(
            "[test] start | type=%s direction=%s speed=%s duration=%.2fs",
            test_type, direction, speed, duration_seconds,
        )
        self._thread = threading.Thread(target=target, args=args, daemon=True)
        self._thread.start()

    def _finish_session(self, stop_reason: str, error_message: str | None = None) -> None:
        with self._lock:
            self._status.active = False
            self._status.elapsed_seconds = self._elapsed()
            self._status.stop_reason = stop_reason
            self._status.error_message = error_message
        logger.info(
            "[test] finish | type=%s reason=%s elapsed=%.2fs error=%s",
            self._status.test_type,
            stop_reason,
            self._status.elapsed_seconds,
            error_message,
        )

    def _elapsed(self) -> float:
        if self._status.started_at == 0.0:
            return 0.0
        return time.monotonic() - self._status.started_at

    # ------------------------------------------------------------------ #
    # 内部：工作线程
    # ------------------------------------------------------------------ #

    def _straight_worker(self, direction: str, speed: int, duration_seconds: float) -> None:
        try:
            if direction == "forward":
                self._run_heading_held_straight(self._motion.move_forward_slow, speed, duration_seconds)
            else:
                self._run_heading_held_straight(self._motion.move_backward_slow, speed, duration_seconds)
            self._motion.stop()
            stop_reason = "manual" if self._stop_event.is_set() else "completed"
            self._finish_session(stop_reason)
        except RobotHardwareError as exc:
            logger.error("[test] straight worker hardware error: %s", exc)
            self._finish_session("error", str(exc))
        finally:
            try:
                self._motion.stop()
            except RobotHardwareError:
                pass

    def _run_heading_held_straight(self, mover: Any, speed: int, duration_seconds: float) -> None:
        self._prepare_heading_guard_for_straight()
        duration = max(0.0, float(duration_seconds))
        slice_seconds = max(0.0, _env_float("MOTION_GUARD_POLL_SECONDS", 0.02))
        if duration <= 0.0 or slice_seconds <= 0.0 or duration <= slice_seconds:
            current_mover = self._mover_with_heading_hold(mover, speed)
            current_mover(speed=speed, duration_seconds=duration)
            return

        remaining = duration
        while remaining > 0 and not self._stop_event.is_set():
            chunk = min(remaining, slice_seconds)
            current_mover = self._mover_with_heading_hold(mover, speed)
            if self._stop_event.is_set():
                break
            current_mover(speed=speed, duration_seconds=chunk)
            remaining -= chunk

    def _prepare_heading_guard_for_straight(self) -> None:
        guard = self._heading_guard_instance()
        if guard is None:
            return
        recalibrate = getattr(guard, "recalibrate_bias", None)
        if callable(recalibrate) and _env_bool("HEADING_ZUPT_ENABLED", True):
            try:
                recalibrate(
                    samples=_env_int("HEADING_ZUPT_SAMPLES", 15),
                    sample_seconds=_env_float("HEADING_ZUPT_SAMPLE_SECONDS", 0.005),
                )
            except (RobotHardwareError, OSError, AttributeError, TypeError, ValueError):
                self._heading_guard = None
                return
        resetter = getattr(guard, "reset", None)
        if callable(resetter):
            resetter()

    def _heading_guard_instance(self) -> Any | None:
        if not _env_bool("HEADING_HOLD_ENABLED", True):
            return None
        if self._heading_guard is not None:
            return self._heading_guard
        opener = getattr(self._imu, "open_straight_heading_guard", None)
        if not callable(opener):
            return None
        try:
            self._heading_guard = opener()
        except (RobotHardwareError, OSError, AttributeError, TypeError, ValueError):
            self._heading_guard = None
        return self._heading_guard

    def _mover_with_heading_hold(self, mover: Any, speed: int) -> Any:
        correction = self._heading_hold_correction(speed)
        if correction is None or getattr(mover, "__name__", "") != "move_forward_slow":
            return mover
        corrected = getattr(self._motion, "move_forward_corrected_slow", None)
        if not callable(corrected):
            return mover

        def corrected_mover(*, speed: int, duration_seconds: float) -> None:
            corrected(
                speed=speed,
                correction=correction.correction_speed,
                direction=correction.direction,
                duration_seconds=duration_seconds,
            )

        return corrected_mover

    def _heading_hold_correction(self, speed: int) -> Any | None:
        guard = self._heading_guard_instance()
        if guard is None:
            return None
        try:
            return compute_heading_hold_correction(
                guard,
                HeadingHoldSettings(
                    enabled=_env_bool("HEADING_HOLD_ENABLED", True),
                    tolerance_degrees=_env_float("HEADING_HOLD_TOLERANCE_DEG", 0.4),
                    gain=_env_float("HEADING_HOLD_GAIN", 0.012),
                    min_pulse_seconds=_env_float("HEADING_HOLD_MIN_PULSE_SECONDS", 0.025),
                    max_pulse_seconds=_env_float("HEADING_HOLD_MAX_PULSE_SECONDS", 0.10),
                    correction_speed=_env_optional_int("HEADING_HOLD_CORRECTION_SPEED") or 16,
                    fallback_speed=speed,
                    invert=_env_bool("HEADING_HOLD_INVERT", False),
                    rate_damping=_env_float("HEADING_HOLD_KD", _env_float("HEADING_HOLD_RATE_DAMPING", 0.18)),
                    speed_gain=_env_float("HEADING_HOLD_SPEED_GAIN", 3.0),
                    min_correction_speed=_env_int("HEADING_HOLD_MIN_CORRECTION_SPEED", 6),
                    max_speed_fraction=_env_float("HEADING_HOLD_MAX_SPEED_FRACTION", 0.8),
                ),
                stop_requested=self._stop_event.is_set,
            )
        except (RobotHardwareError, OSError, AttributeError, TypeError, ValueError):
            self._heading_guard = None
            return None

    def _turn_worker(self, direction: str, speed: int, duration_seconds: float) -> None:
        try:
            if direction == "cw":
                self._motion.rotate_right_slow(speed=speed, duration_seconds=duration_seconds)
            else:
                self._motion.rotate_left_slow(speed=speed, duration_seconds=duration_seconds)
            self._motion.stop()
            stop_reason = "manual" if self._stop_event.is_set() else "completed"
            self._finish_session(stop_reason)
        except RobotHardwareError as exc:
            logger.error("[test] turn worker hardware error: %s", exc)
            self._finish_session("error", str(exc))
        finally:
            try:
                self._motion.stop()
            except RobotHardwareError:
                pass

    def _line_follow_worker(self, speed: int, step_seconds: float) -> None:
        """
        安全寻线测试：
        - 使用有边界的短步指令，每步后显式 stop；
        - 根据 4 路传感器的物理排列 (x2, x1, x3, x4) 做小幅平移修正；
        - 包含丢线安全保护，连续 15 次（约 150ms）检测到全白时自动停车；
        - 支持随时通过 _stop_event 手动急停。
        """
        poll_interval = 0.01  # 10ms 轮询间隔，保证高频响应
        lost_counter = 0
        max_lost_ticks = 15   # 15 * 10ms = 150ms 丢线自动停车保护

        try:
            while not self._stop_event.is_set():
                tape = self._sensors.read_tape_boundary()
                with self._lock:
                    self._update_sensor_status_from_tape(tape)

                if tape is None:
                    # 传感器异常，停止电机
                    self._motion.stop()
                    time.sleep(poll_interval)
                    continue

                decision = decide_line_follow_motion(tape)

                if decision.command == "wait":
                    lost_counter += 1
                    if lost_counter >= max_lost_ticks:
                        self._motion.stop()
                        self._finish_session("completed", "丢线停车（未检测到黑线）")
                        return
                    time.sleep(poll_interval)
                    continue
                else:
                    lost_counter = 0  # 重新检测到线，计数器清零

                if decision.command == "forward":
                    self._motion.move_forward_slow(speed=speed, duration_seconds=step_seconds)
                elif decision.command == "strafe_left":
                    self._motion.strafe_left_slow(speed=speed, duration_seconds=step_seconds)
                elif decision.command == "strafe_right":
                    self._motion.strafe_right_slow(speed=speed, duration_seconds=step_seconds)
                elif decision.command == "turn_left":
                    self._motion.rotate_left_slow(speed=speed, duration_seconds=min(step_seconds, 0.08))
                elif decision.command == "turn_right":
                    self._motion.rotate_right_slow(speed=speed, duration_seconds=min(step_seconds, 0.08))
                else:
                    self._motion.stop()
                    time.sleep(poll_interval)
                    continue
                self._motion.stop()
                time.sleep(poll_interval)

            self._motion.stop()
            self._finish_session("manual")
        except RobotHardwareError as exc:
            logger.error("[test] line_follow worker hardware error: %s", exc)
            self._finish_session("error", str(exc))
        finally:
            try:
                self._motion.stop()
            except RobotHardwareError:
                pass

    def _update_sensor_status(self) -> None:
        """在已持有 _lock 的情况下刷新传感器状态。"""
        try:
            tape = self._sensors.read_tape_boundary()
            self._update_sensor_status_from_tape(tape)
            distance = self._sensors.read_distance_mm()
            self._sensor_status.distance_mm = distance
        except (RobotHardwareError, Exception):
            pass

    def _update_sensor_status_from_tape(self, tape: tuple[int, int, int, int] | None) -> None:
        self._sensor_status.line_sensor = tape
        self._sensor_status.line_description = _tape_description(tape)


# --------------------------------------------------------------------------- #
# 纯函数：传感器解析与描述
# --------------------------------------------------------------------------- #

def _tape_description(tape: tuple[int, int, int, int] | None) -> str:
    """将4路传感器状态转换为人类可读描述。"""
    return describe_tape(tape)
