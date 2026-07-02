# API Contract

**状态：** 0.1 共享契约已完成，后续 1.1、2.1、3.1 以本文件为准。

本文定义 robot runtime、inspection core、dashboard demo 之间的最小共享契约。0.1 只冻结字段和方法，不实现完整硬件适配、异常规则、持久化或看板重构。

## `GET /api/status`

`/api/status` 至少返回以下顶层字段，后续实现可以增加字段，但不能删除这些字段：

| Field | Type | Meaning |
|---|---|---|
| `run_id` | `string` | 当前巡检轮次 ID；本地最小实现使用 `local-001`。 |
| `task_status` | `string` | 内部状态枚举，见下方 Internal Status。 |
| `robot_status` | `string` | 给页面展示的小车状态文本。 |
| `current_zone` | `string | null` | 当前识别或巡检分区。 |
| `current_tag` | `string | null` | 最近识别到的 AprilTag ID。 |
| `current_item` | `string | null` | 最近标签对应的物品名。 |
| `last_message` | `string` | 最近状态说明。 |
| `obstacle` | `object` | 障碍状态，包含 `distance_mm` 和 `blocked`。 |
| `alarm` | `object` | 告警状态，包含 `level` 和 `message`。 |
| `zones` | `array` | 分区摘要；0.1 可以为空数组。 |
| `events` | `array` | 事件列表，最新事件在前。 |

最小示例：

```json
{
  "run_id": "local-001",
  "task_status": "PATROL",
  "robot_status": "巡检中",
  "current_zone": "A区",
  "current_tag": "1",
  "current_item": "Apple",
  "last_message": "标签 1 识别正常。",
  "obstacle": {
    "distance_mm": 320,
    "blocked": false
  },
  "alarm": {
    "level": "normal",
    "message": "正常"
  },
  "zones": [],
  "events": []
}
```

## Event Fields

`events` 中每个事件必须包含以下字段；暂时没有值的字段使用 `null` 或 `"-"`，不要省略字段。

| Field | Type | Meaning |
|---|---|---|
| `id` | `string` | 事件 ID。 |
| `time` | `string` | ISO-like 时间字符串。 |
| `type` | `string` | 事件类型，见 Event Type。 |
| `tag_id` | `string | null` | 关联标签 ID；非标签事件可为 `null`。 |
| `item` | `string` | 物品名；非物品事件使用 `"-"`。 |
| `zone` | `string` | 观察到的分区；未知时使用 `"-"`。 |
| `expected_zone` | `string | null` | 期望分区；不适用时为 `null`。 |
| `priority` | `number` | 优先级，数值越大越优先。 |
| `status` | `string` | 事件状态，见 Event Status。 |
| `message` | `string` | 给页面展示的事件说明。 |

示例：

```json
{
  "id": "evt-0001",
  "time": "2026-07-02T10:00:00",
  "type": "wrong_zone",
  "tag_id": "4",
  "item": "Bottle",
  "zone": "A区",
  "expected_zone": "B区",
  "priority": 2,
  "status": "waiting_confirm",
  "message": "Bottle 出现在 A区，期望 B区。"
}
```

## Internal Status

`task_status` 内部统一使用英文枚举。中文展示由 dashboard 层映射。

```text
IDLE
PATROL
TAG_DETECTED
NORMAL_LOGGED
ABNORMAL_ALARM
WAIT_CONFIRM
CONFIRMED
OBSTACLE_WAIT
FINISHED
STOPPED
```

## Event Type

```text
normal_tag
unknown_tag
wrong_zone
missing_tag
duplicate_tag
obstacle_wait
obstacle_clear
manual_confirm
system
```

## Event Status

```text
normal
waiting_confirm
confirmed
info
```

## `InspectionStore` Methods

`InspectionStore` 必须对外提供以下方法，供 robot runtime、core rule engine 和 dashboard route 共用：

```python
def record_tag(
    self,
    tag_id: str,
    observed_zone: str | None = None,
    source: str = "simulate",
) -> None: ...

def record_obstacle(self, distance_mm: int | None, blocked: bool) -> None: ...

def record_robot_status(self, status: str, message: str | None = None) -> None: ...

def confirm(self, event_id: str | None = None) -> bool: ...

def export_events_csv(self) -> str: ...
```

0.1 最小实现允许 `record_tag()` 复用当前模拟标签规则；完整异常规则、缺失/重复检测和持久化留给 2.1。
