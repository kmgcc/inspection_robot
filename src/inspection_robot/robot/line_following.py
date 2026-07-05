from __future__ import annotations

from dataclasses import dataclass
import time


TapeState = tuple[int, int, int, int]
LineMask = tuple[int, int, int, int]
SENSOR_POSITIONS = (-1.5, -0.5, 0.5, 1.5)
TRANSFER_EXIT_MASKS: frozenset[LineMask] = frozenset(
    {
        (0, 1, 1, 0),
        (0, 1, 0, 0),
        (0, 0, 1, 0),
        (1, 1, 0, 0),
        (0, 0, 1, 1),
        (1, 1, 1, 0),
        (0, 1, 1, 1),
    }
)


@dataclass(frozen=True, slots=True)
class LineFollowDecision:
    command: str
    description: str
    line_seen: bool
    centered: bool = False
    boundary_candidate: bool = False


@dataclass(frozen=True, slots=True)
class TransferLineSettings:
    base_speed: int = 16
    min_speed: int = 10
    kp: float = 10.0
    kd: float = 2.0
    turn_max: int = 10
    error_alpha: float = 0.45
    rate_alpha: float = 0.35
    debounce_frames: int = 1
    bridge_seconds: float = 0.18
    search_seconds: float = 0.65
    failsafe_seconds: float = 1.2


@dataclass(frozen=True, slots=True)
class TransferLineCommand:
    state: str
    line_seen: bool
    stop: bool
    speed: int = 0
    correction: int = 0
    direction: str = "right"
    error: float | None = None
    mask: LineMask | None = None
    description: str = ""


class TransferLineController:
    """Continuous center-line tracker for A/B column transfers.

    Public inputs keep the project-wide sensor convention: 0 means black tape
    and 1 means white floor. Internally the controller uses black=1 masks for
    weighted position estimation.
    """

    def __init__(self, settings: TransferLineSettings | None = None) -> None:
        self.settings = settings or TransferLineSettings()
        self.reset()

    def reset(self) -> None:
        self.state = "TRACK"
        self._candidate_mask: LineMask | None = None
        self._candidate_count = 0
        self._stable_mask: LineMask | None = None
        self._last_error: float | None = None
        self._filtered_error: float | None = None
        self._filtered_rate = 0.0
        self._last_update_at: float | None = None
        self._lost_started_at: float | None = None
        self._last_bias = 0

    def update(self, tape: TapeState | None, *, now: float | None = None) -> TransferLineCommand:
        timestamp = time.monotonic() if now is None else float(now)
        raw_mask = transfer_line_mask(tape)
        mask = self._debounced_mask(raw_mask)
        if mask is None:
            return self._lost_command(timestamp, None, "传感器无有效读数")
        if any(mask):
            return self._track_command(mask, timestamp)
        return self._lost_command(timestamp, mask, "短暂丢线")

    def _debounced_mask(self, mask: LineMask | None) -> LineMask | None:
        if mask is None:
            return None
        frames = max(1, int(self.settings.debounce_frames))
        if self._stable_mask is None:
            self._stable_mask = mask
            self._candidate_mask = mask
            self._candidate_count = 1
            return mask
        if mask == self._stable_mask:
            self._candidate_mask = mask
            self._candidate_count = 0
            return self._stable_mask
        if mask != self._candidate_mask:
            self._candidate_mask = mask
            self._candidate_count = 1
        else:
            self._candidate_count += 1
        if self._candidate_count >= frames:
            self._stable_mask = mask
            self._candidate_count = 0
        return self._stable_mask

    def _track_command(self, mask: LineMask, now: float) -> TransferLineCommand:
        error_raw = estimate_transfer_line_error(mask)
        if error_raw is None:
            return self._lost_command(now, mask, "短暂丢线")

        dt = 0.0 if self._last_update_at is None else max(1e-3, now - self._last_update_at)
        previous = error_raw if self._filtered_error is None else self._filtered_error
        error_alpha = _clamp_float(self.settings.error_alpha, 0.0, 1.0)
        filtered_error = error_raw if self._filtered_error is None else (
            error_alpha * error_raw + (1.0 - error_alpha) * self._filtered_error
        )
        raw_rate = 0.0 if dt <= 0 else (filtered_error - previous) / dt
        rate_alpha = _clamp_float(self.settings.rate_alpha, 0.0, 1.0)
        self._filtered_rate = rate_alpha * raw_rate + (1.0 - rate_alpha) * self._filtered_rate

        turn = self.settings.kp * filtered_error + self.settings.kd * self._filtered_rate
        correction = min(max(0, int(round(abs(turn)))), max(0, int(self.settings.turn_max)))
        if abs(turn) < 0.2:
            correction = 0
        direction = "right" if turn >= 0 else "left"
        speed = self._scheduled_speed(mask, filtered_error)

        self.state = "TRACK"
        self._last_update_at = now
        self._lost_started_at = None
        self._last_error = filtered_error
        if filtered_error > 0.05:
            self._last_bias = 1
        elif filtered_error < -0.05:
            self._last_bias = -1
        return TransferLineCommand(
            state=self.state,
            line_seen=True,
            stop=False,
            speed=speed,
            correction=correction,
            direction=direction,
            error=filtered_error,
            mask=mask,
            description="中心线跟踪",
        )

    def _lost_command(self, now: float, mask: LineMask | None, description: str) -> TransferLineCommand:
        if self._lost_started_at is None:
            self._lost_started_at = now
        lost_for = max(0.0, now - self._lost_started_at)
        failsafe = max(0.0, float(self.settings.failsafe_seconds))
        bridge = max(0.0, float(self.settings.bridge_seconds))
        search = max(0.0, float(self.settings.search_seconds))
        if failsafe > 0 and lost_for >= failsafe:
            self.state = "FAILSAFE"
            return TransferLineCommand(
                state=self.state,
                line_seen=False,
                stop=True,
                mask=mask,
                description=f"{description}，超过安全停车时长",
            )
        if lost_for <= bridge:
            self.state = "BRIDGE"
            correction = min(max(0, int(round(self.settings.turn_max * 0.5))), max(0, int(self.settings.turn_max)))
            return TransferLineCommand(
                state=self.state,
                line_seen=False,
                stop=False,
                speed=max(0, int(self.settings.min_speed)),
                correction=correction,
                direction=self._bias_direction(),
                mask=mask,
                description=f"{description}，沿最后趋势桥接",
            )
        if search <= 0 or lost_for <= bridge + search:
            self.state = "BIASED_SEARCH"
            return TransferLineCommand(
                state=self.state,
                line_seen=False,
                stop=False,
                speed=max(0, int(self.settings.min_speed)),
                correction=max(0, int(self.settings.turn_max)),
                direction=self._bias_direction(),
                mask=mask,
                description=f"{description}，按最后看到线的一侧低速找线",
            )
        self.state = "FAILSAFE"
        return TransferLineCommand(
            state=self.state,
            line_seen=False,
            stop=True,
            mask=mask,
            description=f"{description}，找线超时",
        )

    def _scheduled_speed(self, mask: LineMask, error: float) -> int:
        base = max(0, int(self.settings.base_speed))
        minimum = max(0, min(base, int(self.settings.min_speed)))
        if mask in {(1, 0, 0, 0), (0, 0, 0, 1)}:
            return minimum
        ratio = min(1.0, abs(error) / max(abs(position) for position in SENSOR_POSITIONS))
        return max(minimum, int(round(base - (base - minimum) * ratio)))

    def _bias_direction(self) -> str:
        if self._last_bias < 0:
            return "left"
        if self._last_bias > 0:
            return "right"
        if self._last_error is not None and self._last_error < 0:
            return "left"
        return "right"


def decide_line_follow_motion(tape: TapeState | None) -> LineFollowDecision:
    if tape is None:
        return LineFollowDecision("stop", "传感器异常或无有效读数", False)

    left, left_center, right_center, right = tape
    if left == 1 and left_center == 1 and right_center == 1 and right == 1:
        return LineFollowDecision("wait", "丢线（全白），等待重新检测", False)
    if left == 0 and left_center == 0 and right_center == 0 and right == 0:
        return LineFollowDecision("stop", "全路黑胶带（列端/禁区候选）", True, boundary_candidate=True)
    if (left_center == 0 or left == 0) and right == 0:
        return LineFollowDecision("turn_right", describe_tape(tape), True)
    if left == 0 and (right_center == 0 or right == 0):
        return LineFollowDecision("turn_left", describe_tape(tape), True)
    if left_center == 0 and right_center == 0:
        return LineFollowDecision("forward", "正常居中，短步前进", True, centered=True)
    if left == 0:
        return LineFollowDecision("strafe_left", describe_tape(tape), True)
    if right == 0:
        return LineFollowDecision("strafe_right", describe_tape(tape), True)
    if left_center == 0:
        return LineFollowDecision("strafe_left", describe_tape(tape), True)
    if right_center == 0:
        return LineFollowDecision("strafe_right", describe_tape(tape), True)
    return LineFollowDecision("stop", describe_tape(tape), any(value == 0 for value in tape))


def describe_tape(tape: TapeState | None) -> str:
    if tape is None:
        return "传感器异常或无有效读数"

    left, left_center, right_center, right = tape
    if all(value == 1 for value in tape):
        return "丢线（全白）"
    if all(value == 0 for value in tape):
        return "全路黑胶带（列端/禁区候选）"
    if left_center == 0 and right_center == 0:
        return "正常居中"
    if (left_center == 0 or left == 0) and right == 0:
        return "右大弯/右锐角（右转）"
    if left == 0 and (right == 0 or right_center == 0):
        return "左大弯/左锐角（左急弯）"
    if left == 0:
        return "最左侧偏线（左移修正）"
    if right == 0:
        return "最右侧偏线（右移修正）"
    if left_center == 0 and right_center == 1:
        return "微偏右（左移微调）"
    if left_center == 1 and right_center == 0:
        return "微偏左（右移微调）"
    return f"复合触发（左={left}/左中={left_center}/右中={right_center}/右={right}）"


def transfer_line_mask(tape: TapeState | None) -> LineMask | None:
    if tape is None or len(tape) < 4:
        return None
    try:
        return tuple(1 if int(value) == 0 else 0 for value in tape[:4])  # type: ignore[return-value]
    except (TypeError, ValueError):
        return None


def estimate_transfer_line_error(mask: LineMask | None) -> float | None:
    if mask is None:
        return None
    hits = [position for position, active in zip(SENSOR_POSITIONS, mask, strict=True) if active]
    if not hits:
        return None
    return sum(hits) / len(hits)


def is_transfer_exit_line_frame(tape: TapeState | None) -> bool:
    mask = transfer_line_mask(tape)
    return mask in TRANSFER_EXIT_MASKS


def _clamp_float(value: float, lower: float, upper: float) -> float:
    return max(lower, min(float(value), upper))
