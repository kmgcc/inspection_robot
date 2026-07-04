from __future__ import annotations

from dataclasses import dataclass


TapeState = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class LineFollowDecision:
    command: str
    description: str
    line_seen: bool
    centered: bool = False
    boundary_candidate: bool = False


def decide_line_follow_motion(tape: TapeState | None) -> LineFollowDecision:
    if tape is None:
        return LineFollowDecision("stop", "传感器异常或无有效读数", False)

    left, left_center, right_center, right = tape
    if left == 1 and left_center == 1 and right_center == 1 and right == 1:
        return LineFollowDecision("wait", "丢线（全白），等待重新检测", False)
    if left == 0 and left_center == 0 and right_center == 0 and right == 0:
        return LineFollowDecision("stop", "全路黑胶带（列端/禁区候选）", True, boundary_candidate=True)
    if left_center == 0 and right_center == 0:
        return LineFollowDecision("forward", "正常居中，短步前进", True, centered=True)
    if left == 0 or left_center == 0:
        return LineFollowDecision("strafe_left", describe_tape(tape), True)
    if right == 0 or right_center == 0:
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
