# API Contract

**状态：** 按 `docs/REAL_REQUIREMENTS.md` 同步后的共享契约。旧的固定栅格地图、A* 主链路和预设路径字段不再是核心接口；真车主链路改为货架通道循环巡逻、列端黑胶带触发、动态拓扑生成、侧向识别和异常上报。

## 一、状态快照 `GET /api/status`

`/api/status` 必须同时服务网页、真车 runtime 和软件兜底。建议返回以下字段：

| Field | Type | Meaning |
|---|---|---|
| `run_id` | `string` | 当前运行 ID。 |
| `run_mode` | `string` | `simulate` 或 `robot`。 |
| `hardware_connected` | `boolean` | 是否连接真实硬件。 |
| `task_status` | `string` | 内部状态枚举。 |
| `robot_status` | `string` | 页面展示状态。 |
| `current_shelf` | `string | null` | 当前货架，如 `A1`。 |
| `current_tag` | `string | null` | 最近识别到的 AprilTag、文字或图形 ID。 |
| `current_item` | `string | null` | 最近识别到的物品。 |
| `patrol_cycle` | `number` | 当前巡检轮次，从 1 开始。 |
| `skip_shortage_detection` | `boolean` | 当前是否处于第一轮跳过缺货检测状态。 |
| `last_message` | `string` | 最近状态说明。 |
| `obstacle` | `object` | 超声波障碍状态。 |
| `boundary` | `object` | 黑胶带/列端/禁区状态。 |
| `alarm` | `object` | 灯光和告警状态。 |
| `audio` | `object` | 最近音频提示或语音播报状态。 |
| `gimbal` | `object` | 云台方向和初始化状态。 |
| `topology` | `object` | 运行中生成的巡检拓扑、货架节点、转向点和边界。 |
| `shelves` | `array` | 货架摘要。 |
| `scan` | `object` | 当前扫描状态。 |
| `events` | `array` | 事件列表，最新事件在前。 |

兼容旧字段可以继续返回，但不能作为新功能的主依据：

| Old Field | Compatibility |
|---|---|
| `current_zone` | 可由 `current_shelf` 推导。 |
| `zones` | 可为空；新页面使用 `shelves` 和 `topology`。 |
| `path` | 只用于旧软件兜底或未来路径规划，不代表真车主地图。 |
| `pose` | 可选；如果没有真实定位，不应伪造精确坐标。 |
| `forbidden_zones` | 可选；新逻辑优先使用 `boundary` 事件和动态拓扑。 |

最小示例：

```json
{
  "run_id": "robot-001",
  "run_mode": "robot",
  "hardware_connected": true,
  "task_status": "PATROLLING",
  "robot_status": "巡逻中",
  "current_shelf": "A2",
  "current_tag": "102",
  "current_item": null,
  "patrol_cycle": 2,
  "skip_shortage_detection": false,
  "last_message": "正在巡检 A2 货架。",
  "obstacle": {
    "distance_mm": 430,
    "blocked": false,
    "waiting_seconds": 0
  },
  "boundary": {
    "tape_state": [1, 1, 1, 1],
    "full_black": false,
    "kind": "none"
  },
  "alarm": {
    "level": "normal",
    "light": "green",
    "message": "正常"
  },
  "audio": {
    "last_cue": "shelf_detected",
    "last_message": "扫描到 A2 货架"
  },
  "gimbal": {
    "side_initialized": true,
    "yaw": 60,
    "pitch": 25
  },
  "topology": {
    "status": "building",
    "nodes": [
      {"id": "A1", "kind": "shelf", "label": "A1"},
      {"id": "turn-1", "kind": "boundary_turn", "label": "列端转向"}
    ],
    "edges": [["A1", "A2"]],
    "current_node": "A2"
  },
  "shelves": [
    {"shelf_id": "A1", "status": "normal", "anomaly_count": 0},
    {"shelf_id": "A2", "status": "scanning", "anomaly_count": 0}
  ],
  "scan": {
    "active": true,
    "shelf_id": "A2",
    "detected_items": ["item_04"],
    "detections": [
      {
        "tag_id": "4",
        "kind": "item",
        "item_id": "item_04",
        "ocr_text": "ITEM-04",
        "image_class": "BOX",
        "color": "YELLOW",
        "confidence": 0.92
      }
    ]
  },
  "events": []
}
```

## 二、状态枚举

`task_status` 使用英文枚举，网页层负责中文展示：

```text
IDLE
STARTING
GIMBAL_INIT
PATROLLING
MOVING
TURNING_AT_BOUNDARY
SCANNING_SHELF
ANALYZING
FIRST_PASS_LEARNING
NORMAL_LOGGED
ABNORMAL_ALARM
WAIT_CONFIRM
CONFIRMED
OBSTACLE_WAIT
AVOIDING_OBSTACLE
NESTED_AVOIDANCE
FORBIDDEN_ZONE_WAIT
MANUAL_CONTROL
STOPPED
ERROR
```

旧状态兼容：

```text
PLANNING -> STARTING 或 FIRST_PASS_LEARNING
PLAN_READY -> STARTING
ALIGNING_SHELF -> SCANNING_SHELF
REROUTING -> AVOIDING_OBSTACLE
FINISHED -> STOPPED，仅用于软件兜底，不用于持续巡逻主流程
PATROL -> PATROLLING
TAG_DETECTED -> SCANNING_SHELF 或 ANALYZING
```

## 三、事件类型

基础事件类型：

```text
system
runtime_started
runtime_stopped
manual_control
gimbal_initialized
shelf_detected
item_detected
shelf_scanned
first_pass_observed
cycle_started
cycle_completed
boundary_full_black
boundary_turn
unexpected_boundary
obstacle_wait
obstacle_clear
obstacle_avoidance_started
obstacle_avoidance_step
obstacle_avoidance_nested
forbidden_zone_detected
audio_cue
light_cue
normal_item
missing_item
duplicate_item
wrong_shelf
unknown_item
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
path_planned/path_step/path_replanned -> 仅软件兜底或旧演示使用
```

## 四、事件字段

每个事件建议包含：

| Field | Type | Meaning |
|---|---|---|
| `id` | `string` | 事件 ID。 |
| `time` | `string` | ISO-like 时间。 |
| `type` | `string` | 事件类型。 |
| `priority` | `number` | 优先级。 |
| `status` | `string` | `info`、`normal`、`warning`、`waiting_confirm`、`confirmed`、`error`。 |
| `message` | `string` | 页面展示说明。 |
| `source` | `string` | `runtime`、`camera`、`ultrasonic`、`line_sensor`、`manual`、`simulate` 等。 |
| `shelf_id` | `string | null` | 关联货架。 |
| `item_id` | `string | null` | 关联物品。 |
| `tag_id` | `string | null` | AprilTag 或其他标签 ID。 |
| `expected_shelf` | `string | null` | 期望货架。 |
| `patrol_cycle` | `number | null` | 事件所属轮次。 |
| `frame_id` | `string | null` | 图像帧或截图编号。 |
| `ocr_text` | `string | null` | 文字识别结果。 |
| `image_class` | `string | null` | 图形识别结果。 |
| `color` | `string | null` | 颜色识别结果，可为空。 |
| `evidence` | `object | null` | 多模态证据汇总。 |

## 五、配置文件

### 5.1 货架清单

`config/shelf_manifest.json` 表达每个货架预期物品。真实场地至少需要覆盖 `A1`、`A2`、`A3`、`A4`，B 列编号待现场确认。

```json
{
  "A1": {"expected_items": ["item_01", "item_02"]},
  "A2": {"expected_items": ["item_03"]},
  "A3": {"expected_items": ["item_04"]},
  "A4": {"expected_items": ["item_05"]}
}
```

### 5.2 标签字典

`config/tag_map.json` 支持货架和物品：

```json
{
  "101": {
    "kind": "shelf",
    "shelf_id": "A1",
    "marker_family": "TAG36H11",
    "ocr_label": "A1"
  },
  "1": {
    "kind": "item",
    "item_id": "item_01",
    "expected_shelf": "A1",
    "marker_family": "TAG36H11",
    "expected_ocr": "ITEM-01",
    "expected_image_class": "BOX",
    "expected_color": "RED"
  }
}
```

颜色字段可选。没有颜色时不应报错。

### 5.3 动态拓扑

不要把 `warehouse_map.json` 的固定栅格作为真实地图主接口。若保留该文件，只能作为旧测试、软件兜底或未来扩展。真车主接口应使用运行中生成的 `topology`：

```json
{
  "status": "building",
  "nodes": [
    {"id": "A1", "kind": "shelf", "label": "A1"},
    {"id": "turn-1", "kind": "boundary_turn", "label": "列端转向"}
  ],
  "edges": [["A1", "A2"]],
  "current_node": "A2"
}
```

## 六、控制接口

固定音频 cue：

| Cue | File | Meaning |
|---|---|---|
| `obstacle` | `src/inspection_robot/static/audio/obstacle.wav` | 障碍物或非预期禁区 |
| `first` | `src/inspection_robot/static/audio/first.wav` | 检测到货架 |
| `following` | `src/inspection_robot/static/audio/following.wav` | 检测到货架上的物品，每个物品一次 |

音频播放必须由小车端进程异步触发，通过树莓派默认音频设备输出；HTTP 请求不能等待音频完整播放结束。

必须保留：

```text
GET  /api/status
POST /api/start
POST /api/stop
POST /api/reset
POST /api/confirm
GET  /api/export.csv
GET  /health
```

真车控制建议：

```text
POST /api/control/forward
POST /api/control/backward
POST /api/control/left
POST /api/control/right
POST /api/control/turn_left_90
POST /api/control/turn_right_90
POST /api/control/stop
POST /api/gimbal/init
POST /api/audio/announce
```

如果当前是 `simulate`，接口可以拒绝真实运动，但页面必须提前清楚显示模式，不能只在点击后弹出含糊错误。

## 七、Store 方法建议

```python
def record_run_mode(mode: str, hardware_connected: bool) -> None: ...
def record_cycle(cycle: int, skip_shortage_detection: bool) -> None: ...
def record_gimbal_initialized(yaw: int | None = None, pitch: int | None = None) -> None: ...
def record_boundary(tape_state: tuple[int, int, int, int] | None, full_black: bool, kind: str) -> None: ...
def record_boundary_turn(direction: str = "clockwise", degrees: int = 90) -> None: ...
def record_obstacle(distance_mm: int | None, blocked: bool, waiting_seconds: int = 0) -> None: ...
def record_avoidance_step(step: str, nested_level: int = 0) -> None: ...
def record_shelf_detection(shelf_id: str, tag_id: str | None = None) -> None: ...
def record_item_detection(shelf_id: str, detection: dict[str, object]) -> None: ...
def record_scan_result(shelf_id: str, detected_items: list[str], frame_id: str | None = None) -> None: ...
def record_audio_cue(cue: str, message: str | None = None) -> None: ...
def record_light_cue(color: str, reason: str | None = None) -> None: ...
def record_topology_node(node: dict[str, object]) -> None: ...
def record_topology_edge(source: str, target: str) -> None: ...
def export_events_csv(self) -> str: ...
```
