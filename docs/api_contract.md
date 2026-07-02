# API Contract

**状态：** 0.1 已有最小软件闭环；本文是新版仓库巡逻目标下的共享契约。后续 1.1、2.1、3.1 以本文为准，并保持对现有字段的兼容。

本文定义 robot runtime、inspection core、dashboard demo 之间的接口。当前代码已经返回 `run_id`、`task_status`、`robot_status`、`current_zone`、`current_tag`、`current_item`、`obstacle`、`alarm`、`zones`、`events` 等旧字段；新版允许继续返回这些字段，同时增加地图、路径、货架和扫描相关字段。过渡期内，前端必须容忍新增字段缺失，核心代码也不能删除旧字段。

## `GET /api/status`

`/api/status` 至少返回以下基础字段：

| Field | Type | Meaning |
|---|---|---|
| `run_id` | `string` | 当前巡检轮次 ID。 |
| `task_status` | `string` | 内部状态枚举，见 Internal Status。 |
| `robot_status` | `string` | 页面展示用的小车状态。 |
| `current_zone` | `string | null` | 兼容旧字段，表示当前区域。新版可由 `current_shelf` 或路径点推导。 |
| `current_tag` | `string | null` | 最近识别到的 AprilTag、二维码或文字标签 ID。 |
| `current_item` | `string | null` | 最近标签对应的物品名。 |
| `last_message` | `string` | 最近状态说明。 |
| `obstacle` | `object` | 障碍状态，至少包含 `distance_mm` 和 `blocked`。 |
| `alarm` | `object` | 告警状态，至少包含 `level` 和 `message`。 |
| `zones` | `array` | 兼容旧字段，可为空；新版推荐用 `shelves` 表示货架。 |
| `events` | `array` | 事件列表，最新事件在前。 |

新版推荐增加以下扩展字段：

| Field | Type | Meaning |
|---|---|---|
| `current_shelf` | `string | null` | 当前正在接近或扫描的货架，如 `A1`。 |
| `current_target` | `string | null` | 当前路径目标点，如 `A1_SCAN`、`B2_EXIT`。 |
| `pose` | `object | null` | 小车在固定地图中的估计位置，建议包含 `x`、`y`、`heading`。 |
| `path` | `object` | 路径规划结果，建议包含 `status`、`waypoints`、`next_index`。 |
| `forbidden_zones` | `array` | 黑胶带禁区或配置禁区摘要。 |
| `shelves` | `array` | 货架摘要，包含货架编号、状态和异常数量。 |
| `scan` | `object` | 当前扫描状态，建议包含 `active`、`shelf_id`、`detected_items`、`frame_id`。 |
| `llm_summary` | `string | null` | 可选字段，只用于告警后处理摘要，不参与控制。 |

识别证据统一放在 `scan.detections` 或事件扩展字段中。AprilTag 是主身份来源，OCR、颜色和图像分类是补充证据；补充证据可以提示冲突，但不能在没有人工确认的情况下覆盖 AprilTag 主 ID。

最小示例：

```json
{
  "run_id": "local-001",
  "task_status": "SCANNING_SHELF",
  "robot_status": "扫描货架",
  "current_zone": "A区",
  "current_shelf": "A1",
  "current_target": "A1_SCAN",
  "current_tag": "item_02",
  "current_item": "Bottle",
  "last_message": "正在扫描 A1 货架。",
  "obstacle": {
    "distance_mm": 320,
    "blocked": false
  },
  "alarm": {
    "level": "normal",
    "message": "正常"
  },
  "pose": {
    "x": 3,
    "y": 2,
    "heading": "E"
  },
  "path": {
    "status": "active",
    "waypoints": [[0, 0], [1, 0], [2, 0], [3, 0], [3, 2]],
    "next_index": 4
  },
  "forbidden_zones": [
    {"id": "F1", "cells": [[1, 1], [1, 2]]}
  ],
  "shelves": [
    {"shelf_id": "A1", "status": "scanning", "anomaly_count": 0},
    {"shelf_id": "A2", "status": "pending", "anomaly_count": 0}
  ],
  "scan": {
    "active": true,
    "shelf_id": "A1",
    "detected_items": ["item_01", "item_02"],
    "frame_id": "frame-0008",
    "detections": [
      {
        "tag_id": "46",
        "kind": "item",
        "item_id": "item_46",
        "marker_family": "TAG36H11",
        "color": "RED",
        "ocr_text": "ITEM-46",
        "image_class": "BOTTLE",
        "confidence": 0.92
      }
    ]
  },
  "zones": [],
  "events": []
}
```

## Event Fields

`events` 中每个事件必须包含旧版基础字段；新版事件可增加 `shelf_id`、`target`、`source` 等字段，但不能省略基础字段。

| Field | Type | Meaning |
|---|---|---|
| `id` | `string` | 事件 ID。 |
| `time` | `string` | ISO-like 时间字符串。 |
| `type` | `string` | 事件类型，见 Event Type。 |
| `tag_id` | `string | null` | 关联标签 ID；非标签事件可为 `null`。 |
| `item` | `string` | 物品名；非物品事件使用 `"-"`。 |
| `zone` | `string` | 观察到的区域；未知时使用 `"-"`。 |
| `expected_zone` | `string | null` | 兼容旧字段；新版货架事件可放期望货架或 `null`。 |
| `priority` | `number` | 优先级，数值越大越优先。 |
| `status` | `string` | 事件状态，见 Event Status。 |
| `message` | `string` | 页面展示说明。 |

推荐扩展字段：

| Field | Type | Meaning |
|---|---|---|
| `shelf_id` | `string | null` | 关联货架编号，如 `A1`。 |
| `expected_shelf` | `string | null` | 期望货架。 |
| `target` | `string | null` | 关联路径点。 |
| `source` | `string` | `camera`、`simulate`、`planner`、`ultrasonic`、`line_sensor`、`llm` 等。 |
| `frame_id` | `string | null` | 关联图像帧或截图编号。 |
| `marker_family` | `string | null` | AprilTag/二维码类型，例如 `TAG36H11`。 |
| `ocr_text` | `string | null` | OCR 读出的文字，例如货架号 `A1` 或物品文字。 |
| `color` | `string | null` | 颜色识别结果，例如 `RED`、`YELLOW`、`GREEN`、`BLUE`。 |
| `image_class` | `string | null` | 图像或模板识别结果，例如 `BOTTLE`、`BOX`。 |
| `evidence` | `object | null` | 识别证据汇总，可包含各模态置信度和冲突说明。 |

示例：

```json
{
  "id": "evt-0001",
  "time": "2026-07-02T10:00:00",
  "type": "duplicate_item",
  "tag_id": "item_02",
  "item": "Bottle",
  "zone": "A区",
  "expected_zone": "A1",
  "priority": 2,
  "status": "waiting_confirm",
  "message": "A1 货架识别到重复物品 Bottle。",
  "shelf_id": "A1",
  "expected_shelf": "A1",
  "target": "A1_SCAN",
  "source": "camera",
  "frame_id": "frame-0008"
}
```

## Internal Status

`task_status` 内部统一使用英文枚举。中文展示由 dashboard 层映射。

```text
IDLE
PLANNING
PLAN_READY
MOVING
ALIGNING_SHELF
SCANNING_SHELF
ANALYZING
NORMAL_LOGGED
ABNORMAL_ALARM
WAIT_CONFIRM
CONFIRMED
OBSTACLE_WAIT
REROUTING
FORBIDDEN_ZONE_WAIT
FINISHED
STOPPED
```

兼容旧状态：

```text
PATROL -> MOVING
TAG_DETECTED -> SCANNING_SHELF 或 ANALYZING
```

## Event Type

基础事件类型：

```text
system
path_planned
path_step
path_replanned
forbidden_zone_detected
obstacle_wait
obstacle_clear
shelf_arrived
shelf_aligned
shelf_scanned
normal_item
unknown_item
wrong_shelf
missing_item
duplicate_item
evidence_mismatch
manual_confirm
llm_summary
```

兼容旧类型：

```text
normal_tag -> normal_item
unknown_tag -> unknown_item
wrong_zone -> wrong_shelf
missing_tag -> missing_item
duplicate_tag -> duplicate_item
```

## Event Status

```text
normal
waiting_confirm
confirmed
info
warning
```

## Configuration Files

新版建议增加三个配置文件。2.1 可以先实现纯软件版本，1.1 和 3.1 只消费结果。

### AprilTag 与 ID 范围

项目统一使用 AprilTag `TAG36H11`。建议 ID 范围如下：

| Range | Meaning |
|---|---|
| `1-50` | 物品标签。 |
| `101-120` | 货架标签。 |
| `201-220` | 定位点或路径校正标签，预留。 |
| `301-320` | 禁区、特殊点或演示提示标签，预留。 |

同一个数字 ID 不能在不同 `kind` 中复用。检测器读到的是数字 ID；核心系统必须通过 `tag_map.json` 把它映射为货架、物品、定位点或特殊区域。

`config/warehouse_map.json`：

```json
{
  "grid_size": [8, 6],
  "start": [0, 0],
  "home": [0, 0],
  "forbidden_cells": [[2, 2], [2, 3]],
  "shelf_points": {
    "A1": {"scan_pose": [3, 1, "E"], "safe_side": "W"},
    "A2": {"scan_pose": [5, 1, "E"], "safe_side": "W"}
  }
}
```

`config/shelf_manifest.json`：

```json
{
  "A1": {
    "expected_items": ["item_01", "item_02", "item_03"]
  },
  "A2": {
    "expected_items": ["item_04", "item_05"]
  }
}
```

`config/tag_map.json` 继续保留，并扩展 `kind`：

```json
{
  "118": {
    "name": "A1",
    "kind": "shelf",
    "shelf_id": "A1",
    "marker_family": "TAG36H11",
    "ocr_label": "A1"
  },
  "46": {
    "name": "Red Bottle",
    "kind": "item",
    "item_id": "item_46",
    "expected_shelf": "A1",
    "marker_family": "TAG36H11",
    "expected_color": "RED",
    "expected_ocr": "ITEM-46",
    "expected_image_class": "BOTTLE",
    "priority": 1
  }
}
```

货架打印版式固定为“上方大号货架号、下方 AprilTag、底部数字脚注”。物品打印版式固定为“颜色块、简化图像、文字名称、AprilTag、底部数字脚注”。当前已生成的打印素材位于仓库根目录 `打印素材_AprilTag/`，其中 `manifest.csv` 可以作为 `tag_map.json` 的初始数据来源。

## `InspectionStore` Methods

当前代码已有以下方法，必须保持：

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

新版建议逐步增加：

```python
def record_pose(self, x: int, y: int, heading: str, source: str = "runtime") -> None: ...
def record_path(self, waypoints: list[tuple[int, int]], status: str = "active") -> None: ...
def record_shelf_arrival(self, shelf_id: str, target: str | None = None) -> None: ...
def record_scan_result(self, shelf_id: str, detected_items: list[str], frame_id: str | None = None) -> None: ...
def record_detection_evidence(self, shelf_id: str, detections: list[dict[str, object]], frame_id: str | None = None) -> None: ...
def record_forbidden_zone(self, zone_id: str | None, blocked: bool) -> None: ...
def finish_run(self) -> None: ...
```

过渡期允许 1.1 先用 `record_robot_status()` 和 `record_tag()` 打通真车输入，2.1 再替换成完整地图、货架和异常规则。这个兼容策略是为了保证三名队友能并行推进，而不是互相等待某个大重构完成。
