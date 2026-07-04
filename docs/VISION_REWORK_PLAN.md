# 视觉识别系统改造指引

**状态：** 后续视觉相关修改的指引与目录。
**用途：** 把视觉识别、轮次感知、异常上报闭环、网页视频与检测结果传输等问题集中归档,作为修改 checklist 与设计依据。
**依据：** `docs/REAL_REQUIREMENTS.md` 第四节(识别需求)、4.4(算法选型建议)、7.5(实时视频与检测结果展示)、第十节(验收标准)。
**优先级原则：** 本文与 `REAL_REQUIREMENTS.md` 冲突时以需求文档为准。

## 一、本次改造范围与决策

本次改造聚焦视觉识别主链路,基于 2026-07-04 的决策:

1. **先不做语音** — 音频提示、缺货语音警报、TTS 不在本次范围。
2. **先做好视觉识别** — AprilTag、OCR、颜色、帧稳定性全部做出来,达到"初步能识别"。
3. **帧稳定性先实现但默认关闭** — 实现写在代码里,通过配置开关关闭,后续接入"根据画面运动状态决定小车运动"时再启用。
4. **视觉状态机先写但不启用** — 写出状态机骨架,但 runtime 仍走当前"到货架点直接调 `iter_detections`"的简化路径,状态机作为后续替换的预留。
5. **轮次判断改为感知式** — 不通过地标转向计数,而是通过识别到的货架号序列感知:从 A1→A4 检测完,再从 B 末尾(B4)反向检测到 B1,标记为一轮完成。
6. **做好异常上报闭环** — 异常判断 + 事件记录 + 网页展示,不含语音。
7. **做好网页视频流与检测结果叠加** — 不仅是流畅视频,还要把识别框、AprilTag ID、OCR 文本等检测结果叠加在画面上传输。

不在本次范围:

- 语音警报、TTS、音频队列节流。
- YOLO 轻量目标检测(第二阶段)。
- PaddleOCR 迁移(当前 `pytesseract` 是可接受的第一阶段方案)。
- 卡片轮廓检测与透视矫正(可后续接入视觉状态机时再做)。
- 6 秒等待绕行、嵌套避障等底盘逻辑。

## 二、当前代码现状(基线)

### 2.1 `vision/tag_detector.py`(唯一视觉文件,227 行)

| 函数 | 位置 | 现状 |
|---|---|---|
| `iter_detections` | `tag_detector.py:29` | 主入口,从摄像头读帧,返回 `tag_id/marker_family/ocr_text/color/image_class/confidence` |
| `_read_stable_detections` | `:67` | 3 帧投票,≥2 帧同一 `tag_id` 才算稳定;**无角度/四角位置/已处理判断** |
| `_detect_frame` | `:84` | 单帧检测:AprilTag + OCR(原图上 1/3) + 颜色(tag 中心 45px crop) |
| `_dominant_color_name` | `:105` | RGB 阈值,9 种颜色;**无 HSV**,对光照敏感 |
| `_try_ocr_text` | `:144` | `pytesseract --psm 7`,ROI 是整张图上 1/3;**无透视矫正** |
| `_confidence` | `:160` | 由 `decision_margin` 归一化到 0-1 |
| `_load_vision_dependencies` | `:170` | 优先 `dt_apriltags`,失败回退 OpenCV `aruco.DICT_APRILTAG_36h11` |
| `_OpenCVArucoAprilTagDetector` | `:202` | OpenCV 回退检测器 |

### 2.2 `runtime.py` 中的视觉调用

- `runtime.py:121` `detection_provider: DetectionProvider = tag_detector.iter_detections` 默认注入。
- `runtime.py:946` `_perform_scan` 在货架扫描步骤播放 `first` cue → 调 `_collect_detections`。
- `runtime.py:967` `_collect_detections` 调 `iter_detections(device, cooldown=0.5, idle_timeout=scan_timeout)`,取 `scan_max_detections` 个。
- **没有轮次判断、没有第一轮跳过缺货检测的接入**。

### 2.3 `core/rules.py` 异常判断(已齐全)

| 异常类型 | 位置 | 备注 |
|---|---|---|
| `missing_item` | `rules.py:88` | 已有,且 `evaluate_shelf_scan` 有 `skip_missing` 参数(第一轮跳过预留) |
| `duplicate_item` | `:65` | 已有 |
| `unknown_item` | `:256` | 已有,通过 `_unknown_item` |
| `wrong_shelf` | `:50, 232` | 已有 |
| `evidence_mismatch` | `:167, 342` | 已有,对比 OCR 与 `expected_label` |

异常判断逻辑已完备,本次改造**不需要新增异常类型**,只需在 runtime 中正确接入轮次与 `skip_missing`。

### 2.4 `core/store.py` 轮次字段(已预留)

- `store.py:105` `record_cycle(cycle, skip_shortage_detection)` — 已存在。
- `store.py:108` `state.patrol_cycle` — 已存在轮次状态字段。
- **但调用方(runtime)目前不基于货架号感知调用 `record_cycle`**,需要补接入。

### 2.5 `robot/alarm.py` 灯光状态

| 函数 | 颜色 | 用途 |
|---|---|---|
| `show_normal` | 绿 | 正常巡逻 |
| `show_obstacle_wait` | - | 障碍等待 |
| `show_warning` | - | 警告 |
| `show_high_priority_alarm` | 红 | 缺货/高优先级异常 |
| `show_line_follow` | - | 寻线 |

缺货红灯可用 `show_high_priority_alarm`,本次不接入语音,但灯光可作为异常闭环的一部分。

### 2.6 `web.py`(无视频流)

- **完全没有视频流端点** — 无 `StreamingResponse`、无 `MJPEG`、无 `/video_feed`。
- 仅有 `/api/demo/scan/<shelf_id>` 等 demo 端点和 `/api/simulate/tag/<tag_id>`。
- 验收 10.4 第 10 条(实时画面 + 检测结果叠加)**完全空白**。

## 三、待修改问题清单(总览)

| 编号 | 问题 | 模块 | 优先级 | 依赖 |
|---|---|---|---|---|
| V1 | AprilTag 识别增强(角度/四角/已处理标记) | vision | P1 | 无 |
| V2 | 帧稳定性机制(实现但默认关闭) | vision + config | P1 | V1 |
| V3 | OCR 文字识别改进(ROI 优化) | vision | P1 | 无 |
| V4 | 颜色识别改进(HSV 色彩空间) | vision | P1 | 无 |
| V5 | 视觉状态机骨架(写但不启用) | vision + runtime | P2 | V1-V4 |
| V6 | 基于货架号感知的轮次判断 | runtime + store | P1 | V1 |
| V7 | 异常上报闭环接入(轮次 + skip_missing + 网页) | runtime + rules + web | P1 | V6 |
| V8 | 网页视频流(MJPEG)与检测结果叠加 | vision + web | P1 | V1, V3, V4 |

## 四、详细修改项

### V1. AprilTag 识别增强

**现状:** `_detect_frame` 只取 `tag_id`、`center`、`decision_margin`,没有暴露 AprilTag 的四角坐标、角度、是否已处理等字段。需求 4.4 要求 AprilTag 提供"货架或卡片身份、位置、角度、四角位置、是否稳定、是否处理过"。

**目标:** 检测结果增加 `corners`、`angle`、`hamming`、`goodness` 字段,供 V2 帧稳定性与 V5 状态机使用。

**涉及文件:**
- `src/inspection_robot/vision/tag_detector.py`
  - `_detect_frame:84` — 扩展返回字段。
  - `_OpenCVArucoDetection:195` — 增加 `corners`、`angle` 字段。
  - `_OpenCVArucoAprilTagDetector.detect:209` — 计算四角与角度。
  - `dt_apriltags` 路径 — 直接透传 `corners`、`hamming`、`goodness`、`pose_R` 等。

**修改要点:**
1. `_OpenCVArucoDetection` dataclass 增加 `corners: tuple[float, float, float, float]`、`angle: float | None`。
2. `_OpenCVArucoAprilTagDetector.detect` 用 `cv2.aruco.estimatePoseSingleMarkers` 或四角顺序计算角度。
3. `_detect_frame` 把这些字段透传到 detection dict。
4. 保持向后兼容:旧字段(`tag_id/marker_family/ocr_text/color/image_class/confidence`)不变。

**验收:** 单元测试覆盖 `_OpenCVArucoAprilTagDetector` 返回四角与角度;`iter_detections` yield 的 dict 包含新字段。

---

### V2. 帧稳定性机制(实现但默认关闭)

**现状:** `_read_stable_detections` 只做"3 帧 ≥2 帧同一 `tag_id`"的简单投票,没有角度稳定、四角位置稳定、连续帧数可配、是否处理过的状态。

**目标:** 实现完整的帧稳定性判断,但通过配置开关默认关闭,runtime 走"单帧即采"的快速路径;后续接入"根据画面运动状态决定小车运动"时启用。

**涉及文件:**
- `src/inspection_robot/vision/tag_detector.py`
  - 新增 `_StabilityTracker` 类,记录每个 `tag_id` 的最近 N 帧角度/四角/中心位移。
  - `iter_detections` — 增加 `stability_enabled: bool = False` 参数。
  - 关闭时:`_read_stable_detections` 走当前简单投票。
  - 开启时:要求角度方差 < 阈值、四角位移 < 阈值、连续帧数 >= `min_stable_frames`。
- `src/inspection_robot/config_types.py` / `config_defaults.py`
  - 新增 `vision_stability_enabled: bool = False`。
  - 新增 `vision_min_stable_frames: int = 3`。
  - 新增 `vision_angle_variance_threshold: float = ...`。
  - 新增 `vision_corner_displacement_threshold: float = ...`。
- `src/inspection_robot/runtime.py:967` `_collect_detections` — 把 `vision_stability_enabled` 透传给 `iter_detections`。

**修改要点:**
1. 实现完整稳定性逻辑,但默认 `vision_stability_enabled=False`。
2. 关闭时行为与当前一致(不破坏现有测试)。
3. 文档与配置注释说明"后续接入画面运动状态决定小车运动时启用"。

**验收:** 关闭时现有测试全过;开启时单测覆盖稳定性判断分支;手动开启后能过滤抖动检测。

---

### V3. OCR 文字识别改进

**现状:** `_try_ocr_text` ROI 是整张图上 1/3,用 `--psm 7`(单行文本),没有基于 AprilTag 位置裁剪、没有预处理(二值化、降噪)、没有三层校验(置信度/编号格式/预置库比对)。

**目标:** 第一阶段在 AprilTag 附近裁剪 ROI 再 OCR,加预处理,加编号格式校验。**不做 PaddleOCR 迁移**(当前 `pytesseract` 可接受)。**不做透视矫正**(留待视觉状态机)。

**涉及文件:**
- `src/inspection_robot/vision/tag_detector.py`
  - `_try_ocr_text:144` — 改为接受 `tag_center` / `tag_corners` 参数,在 tag 附近裁剪 ROI。
  - 增加预处理:`cv2.threshold`、`cv2.GaussianBlur`、可选 `--psm 6`(假设单一统一文本块)。
  - 增加置信度返回:`pytesseract.image_to_data` 取 `conf` 字段。
  - 增加编号格式校验:正则匹配 `^[A-Z]\d+_\d+$` 之类(从配置读取预期格式)。
  - 增加"识别不可靠时返回 None,不硬猜"。
- `src/inspection_robot/config_types.py`
  - 新增 `ocr_expected_format: str | None = None`。
  - 新增 `ocr_min_confidence: float = 60.0`。
- `src/inspection_robot/core/rules.py:318` `_normalized_evidence` — 复用做预置库比对。

**修改要点:**
1. OCR 从"整图上 1/3"改为"AprilTag 周围 ROI"。
2. 加预处理提升识别率。
3. 加置信度与格式校验,低置信度返回 `None`。
4. 保持 `ocr_text` 字段含义不变,新增 `ocr_confidence` 字段。

**验收:** 在固定卡片距离下,OCR 识别率提升;低质量画面返回 `None` 而非乱码。

---

### V4. 颜色识别改进

**现状:** `_dominant_color_name` 用 RGB 均值 + 阈值,9 种颜色。对光照敏感,容易把"黄色被强光打成白色"或"红色弱光打成橙色"。

**目标:** 改用 HSV 色彩空间判断,支持蓝/绿/黄/红等需求 4.2 要求的常见颜色,没有颜色时返回 `None` 而不报错(需求 4.2.4)。

**涉及文件:**
- `src/inspection_robot/vision/tag_detector.py`
  - `_dominant_color_name:105` — 改为 HSV 判断。
  - 计算 HSV 均值与主导色相区间。
  - 增加"无明显颜色"判断:饱和度 < 阈值或明度极低/极高时返回 `None`。
  - 保留 BLACK/WHITE/GRAY 的明度判断。
- `src/inspection_robot/config_types.py`
  - 新增 `color_saturation_threshold: float = ...`。
  - 新增 `color_value_threshold: float = ...`。

**修改要点:**
1. RGB → HSV。
2. 主导色相区间映射到颜色名。
3. 低饱和度/极端明度返回 `None`(无颜色)。
4. 保持 `color` 字段含义不变,但值集合可能从 9 种缩减为需求要求的"蓝/绿/黄/红 + 黑/白 + None"。

**验收:** 在不同光照下颜色判断稳定;无颜色物品返回 `None` 不报错(需求 4.2.4、4.2.5)。

---

### V5. 视觉状态机骨架(写但不启用)

**现状:** runtime 是"到货架点直接调 `iter_detections` 取 N 个检测",没有需求 4.4 描述的状态机:`IDLE→SEARCHING→ALIGNING→SLOW_DOWN_OR_STOP→CAPTURE→RECTIFY→OCR→VERIFY→TTS→DONE→SEARCHING`。

**目标:** 写出状态机骨架代码,但 runtime 仍走当前简化路径,状态机作为后续替换的预留。**TTS 状态本次跳过**(先不做语音)。

**涉及文件:**
- `src/inspection_robot/vision/` 新增 `state_machine.py`
  - 定义 `VisionState` 枚举:`IDLE/SEARCHING/ALIGNING/SLOW_DOWN/CAPTURE/RECTIFY/OCR/VERIFY/DONE`。
  - 定义 `VisionStateMachine` 类,持有当前状态、当前目标、已处理目标集合。
  - 实现 `transition(detection)` 方法,按状态转移规则推进。
  - `SEARCHING` → 检测到 tag 进入 ROI → `ALIGNING`。
  - `ALIGNING` → 角度与连续帧稳定 → `SLOW_DOWN`。
  - `SLOW_DOWN` → 减速或暂停信号 → `CAPTURE`。
  - `CAPTURE` → 拍高分辨率帧 → `RECTIFY`(本次不做透视矫正,直接跳过)。
  - `RECTIFY` → `OCR`。
  - `OCR` → `VERIFY`(置信度/格式/预置库校验)。
  - `VERIFY` → `DONE`(记录已处理)。
  - `DONE` → 离开 ROI → `SEARCHING`。
- `src/inspection_robot/vision/tag_detector.py`
  - 新增 `iter_detections_with_state` 函数,用状态机驱动,但**默认不被 runtime 调用**。
- `src/inspection_robot/config_types.py`
  - 新增 `vision_state_machine_enabled: bool = False`。
- `src/inspection_robot/runtime.py:967` `_collect_detections`
  - 当 `vision_state_machine_enabled=True` 时走 `iter_detections_with_state`,否则走当前路径。

**修改要点:**
1. 状态机代码完整但默认不启用。
2. 状态转移有日志,便于调试。
3. 不破坏当前 runtime 路径。
4. 文档说明"后续启用时需要接入 CAPTURE→高分辨率拍照、RECTIFY→透视矫正、TTS→语音"。

**验收:** 单测覆盖状态转移规则;关闭时 runtime 行为不变。

---

### V6. 基于货架号感知的轮次判断

**现状:** `store.py:105` 有 `record_cycle(cycle, skip_shortage_detection)` 和 `state.patrol_cycle`,但 runtime 不基于货架号序列感知调用。当前轮次推进依赖转向计数(`_cycle_from_turn_count:1026`),与用户决策冲突。

**目标:** 轮次推进改为感知式 — runtime 维护"已观察到的货架号序列",当序列匹配预期一轮的完整序列(A1→A2→A3→A4→B4→B3→B2→B1)时,递增 `patrol_cycle` 并调用 `record_cycle`。

**涉及文件:**
- `src/inspection_robot/runtime.py`
  - 新增 `_observed_shelf_sequence: list[str]` 状态。
  - 在 `_perform_scan:946` 或货架识别回调中追加当前 `shelf_id`。
  - 新增 `_check_cycle_completion()` 方法,比对已观察序列与预期序列。
  - 匹配时调用 `store.record_cycle(cycle, skip_shortage_detection=(cycle == 1))`。
  - 重置 `_observed_shelf_sequence` 开始新一轮。
  - **保留** `_cycle_from_turn_count:1026` 作为兜底/兼容,但主路径改为感知式。
- `src/inspection_robot/config_types.py`
  - 新增 `expected_shelf_sequence: list[str] = ["A1","A2","A3","A4","B4","B3","B2","B1"]`。
- `src/inspection_robot/core/store.py:105`
  - 确认 `record_cycle` 接口兼容,必要时扩展记录"本轮观察到的货架序列"。

**修改要点:**
1. 货架号序列感知,不依赖转向计数。
2. 第一轮 `skip_shortage_detection=True`。
3. 序列匹配支持"允许中间重复观察"(同一货架被识别多次只算一次)。
4. 序列匹配支持"允许跳过未识别货架"(某货架没识别到 tag,但下一个识别到了,序列继续推进)— 需要确认容错策略(见待确认问题)。
5. 重置时机:序列匹配成功后立即重置开始新一轮。

**验收:**
- 单测覆盖:A1→A4→B4→B1 完整序列触发 `record_cycle(2, False)`。
- 单测覆盖:第一轮 `skip_shortage_detection=True`。
- 单测覆盖:中间重复观察同一货架不重复计数。
- 真车验收:小车跑完一圈 A 列 + B 列,网页显示轮次 +1。

---

### V7. 异常上报闭环接入

**现状:** `rules.py` 异常判断已齐全(见 2.3),`store.py` 有 `record_cycle` 与 `skip_shortage_detection`,但 runtime 没有把"轮次 + skip_missing"正确接入异常判断,异常事件没有完整闭环到网页展示与红灯提示。

**目标:** runtime 在每次货架扫描后,根据当前轮次调用 `evaluate_shelf_scan(skip_missing=...)`,异常事件进入 store,网页显示待确认异常列表,缺货时亮红灯。

**涉及文件:**
- `src/inspection_robot/runtime.py:946` `_perform_scan`
  - 在 `record_detection_evidence` 后调用 `rules.evaluate_shelf_scan(...)`。
  - 传入 `skip_missing=(self._current_cycle == 1)` 或基于 `state.patrol_cycle`。
  - 把返回的 `events` 全部记入 store。
  - 若有 `missing_item` 事件,调用 `self.alarm.show_high_priority_alarm()`(红灯)。
- `src/inspection_robot/core/rules.py:10` `evaluate_shelf_scan`
  - 确认 `skip_missing` 参数语义正确(第一轮跳过缺货判断)。
- `src/inspection_robot/web.py`
  - 确认 `/api/events` 或类似端点暴露待确认异常列表(需求 7.4.12)。
  - 确认网页能显示 `missing_item/duplicate_item/wrong_shelf/unknown_item/evidence_mismatch` 五类异常(需求 10.4.8)。
- `src/inspection_robot/robot/alarm.py`
  - 缺货用 `show_high_priority_alarm`(红),已有。

**修改要点:**
1. runtime 接入 `evaluate_shelf_scan`,第一轮 `skip_missing=True`。
2. 异常事件进 store,网页可查。
3. 缺货亮红灯(语音本次不做)。
4. 网页待确认异常列表支持人工确认(`store.confirm` 已存在,见 `store.py:423`)。

**验收:**
- 第二轮起识别到缺货,store 产生 `missing_item` 事件。
- 网页显示该事件,状态 `waiting_confirm`。
- 缺货时 `alarm.show_high_priority_alarm()` 触发红灯。
- 人工确认后事件状态变 `confirmed`。
- 第一轮不产生 `missing_item` 事件。

---

### V8. 网页视频流(MJPEG)与检测结果叠加

**现状:** `web.py` 完全没有视频流端点。需求 7.5 强制要求网页第一页有流畅实时画面,且画面上或侧边渲染检测结果(AprilTag ID、物品名、OCR 内容)。

**目标:** 新增 `/api/video_feed` MJPEG 端点,低分辨率(320×240 或 640×480)、高帧率,每帧叠加 AprilTag 检测框、tag_id、OCR 文本、颜色标签;同时新增 `/api/video/detections` JSON 端点供前端侧边面板轮询最新检测证据。

**涉及文件:**
- `src/inspection_robot/vision/tag_detector.py`
  - 抽出 `_detect_frame` 为可复用的"单帧检测"函数,既能给 `iter_detections` 用,也能给视频流用。
  - 新增 `draw_detections(frame, detections)` — 在帧上画框、写 tag_id、OCR、颜色。
- `src/inspection_robot/vision/`(新文件)`video_stream.py`
  - `generate_mjpeg_frames(device, ...)` 生成器:读帧 → `_detect_frame` → `draw_detections` → JPEG 编码 → yield MJPEG 边界帧。
  - 单独摄像头实例,与 `iter_detections` 的 `capture` 分离(避免冲突)。
  - 帧率控制:目标 15-20 FPS,避免 CPU 过载。
  - 异常兜底:摄像头占用或 `VisionDependencyError` 时返回静态错误帧。
- `src/inspection_robot/web.py`
  - 新增 `@app.route("/api/video_feed")` 返回 `Response(generate_mjpeg_frames(), mimetype="multipart/x-mixed-replace; boundary=frame")`。
  - 新增 `@app.route("/api/video/detections")` 返回最近一帧的检测证据 JSON。
  - 模式判断:`simulate` 模式返回模拟视频流或静态占位图;`robot` 模式接真摄像头。
- 前端模板(`templates/` 或 `static/`)
  - 主页嵌入 `<img src="/api/video_feed">`。
  - 侧边面板轮询 `/api/video/detections` 显示当前 tag_id、OCR、color、confidence。

**修改要点:**
1. 视频流与 `iter_detections` 用不同摄像头实例或共享帧源(需要确认摄像头能否同时被两路打开,见待确认问题)。
2. MJPEG 帧率优先于检测完整度:必要时视频流用更轻量的检测(降采样、跳帧)。
3. 模拟模式提供占位视频流,不报错。
4. 前端叠加层与视频流解耦:视频流叠加基本框,侧边面板显示完整证据。

**验收:**
- 网页第一页显示流畅实时画面(验收 10.4.10)。
- 画面上叠加 AprilTag 检测框与 tag_id。
- 侧边显示 OCR 文本、颜色、confidence。
- `simulate` 模式有占位画面。
- 帧率稳定(目标 ≥10 FPS,无卡顿)。

## 五、推荐实施顺序

```
V1 AprilTag 增强 ──┬─→ V2 帧稳定性(默认关)
                   ├─→ V3 OCR 改进
                   ├─→ V4 颜色改进
                   └─→ V8 视频流(依赖 V1, V3, V4 的检测函数)

V6 轮次感知 ────────→ V7 异常闭环

V5 状态机骨架(独立,写完不启用)
```

建议按以下批次推进:

**批次 1(视觉识别核心):** V1 → V3 → V4 → V2
把 AprilTag 字段补齐、OCR 改为 tag 附近 ROI、颜色改 HSV、帧稳定性实现但关闭。这批做完后视觉识别"初步能用"。

**批次 2(轮次与异常):** V6 → V7
基于货架号感知推进轮次,接入 `evaluate_shelf_scan(skip_missing)`,异常事件进 store 与网页,缺货亮红灯。

**批次 3(网页视频):** V8
抽出可复用单帧检测,新增 MJPEG 端点与检测结果 JSON 端点,前端嵌入视频与侧边面板。

**批次 4(预留):** V5
写状态机骨架,不启用。可放在批次 1 后任意时机,独立于其他批次。

## 六、验收标准(汇总)

对应 `REAL_REQUIREMENTS.md` 第十节:

### 10.2 识别验收
- [ ] 能识别货架 AprilTag(V1)。
- [ ] 能识别至少一个物品 AprilTag(已有,确认 V1 后字段完整)。
- [ ] 能通过文字识别至少一个物品或标签文本(V3)。
- [ ] 能通过图形识别至少一个物品类别(本次不做 YOLO,标记为后续)。
- [ ] 有颜色时能记录颜色(V4)。
- [ ] 没有颜色时不报错(V4 返回 `None`)。
- [ ] 识别到货架播放货架提示音(已有 `first` cue,本次不动)。
- [ ] 识别到物品播放物品提示音(已有 `following` cue,本次不动)。
- [ ] 检测到缺货时红灯提示(V7 接入 `show_high_priority_alarm`)。
- [ ] 缺货语音报警(本次不做,标记为后续)。

### 10.4 网页验收
- [ ] 网页第一页包含流畅实时摄像头视频预览(V8)。
- [ ] 画面上或侧边渲染检测结果(V8)。
- [ ] 事件列表显示缺货、错放、重复、未知、证据冲突五类(V7)。
- [ ] 待确认异常可人工确认(V7,已有 `store.confirm`)。

### 轮次相关(需求 3.2、7.4.7、7.4.8)
- [ ] 第一轮跳过缺货检测(V6, V7)。
- [ ] 第二轮开始检测缺货(V6, V7)。
- [ ] 网页显示当前轮次(V7, 确认网页已读 `state.patrol_cycle`)。
- [ ] 网页显示第一轮是否跳过检测(V7, 确认网页显示 `skip_shortage_detection`)。

## 七、待确认问题

1. **摄像头共享** — `iter_detections` 与 `/api/video_feed` 能否同时打开同一摄像头?Picamera2 在树莓派上通常支持多消费者,但 `cv2.VideoCapture` 默认不行。需要确认是否引入 Picamera2 共享帧源,或视频流用独立检测路径(可能造成检测与视频画面不一致)。
2. **货架序列容错** — V6 中,若某货架 AprilTag 没识别到(漏检),序列推进是否容许跳过?还是必须严格顺序?建议容许"跳过 1 个"作为容错,跳过 2 个则报警。
3. **预期货架序列配置** — `expected_shelf_sequence` 是否写死为 `A1-A4, B4-B1`,还是从配置文件读?当前需求确认 A/B 两列各 4 个,后续若加 C/D 列需扩展。
4. **视频流分辨率与帧率权衡** — 320×240 @ 15FPS 还是 640×480 @ 10FPS?需求 7.5.2 要求"高且稳定的帧率",但未给具体数值。建议默认 320×240 @ 15FPS,可配置。
5. **检测结果叠加层位置** — 叠加在视频帧上(画框)还是仅侧边面板?需求 7.5.3 允许"画面上或其侧边",建议两者都做:视频帧画轻量框,侧边显示完整证据。
6. **`skip_missing` 语义** — `rules.py:103` 的 `skip_missing` 当前是否真的跳过 `missing_item` 事件?需要确认实现是"完全不产生"还是"产生但标记为 skipped"。
7. **视觉状态机启用时机** — V5 写完不启用,何时启用?建议待"根据画面运动状态决定小车运动"需求明确后再启用,本次仅预留骨架。
8. **颜色集合** — V4 改 HSV 后,颜色集合是保留 9 种还是缩减为需求 4.2.2 要求的"蓝/绿/黄/红 + 黑/白 + None"?建议保留 9 种但优先保证需求要求的 4 种准确。
9. **OCR 格式校验** — V3 的 `ocr_expected_format` 正则从哪读?是否每个物品 tag 在配置里有自己的 `ocr_label`(已有 `TagInfo.ocr_label`)?建议复用 `ocr_label` 做比对,而非全局正则。
10. **轮次感知的兜底** — V6 改为感知式后,若整轮都没识别到任何货架(摄像头故障),轮次永远不推进。是否保留 `_cycle_from_turn_count` 作为兜底,或新增"超时未推进则强制递增"?

## 八、相关文件索引

### 视觉核心
- `src/inspection_robot/vision/tag_detector.py` — 主视觉文件。
- `src/inspection_robot/vision/__init__.py` — 包入口。
- 待新增:`src/inspection_robot/vision/state_machine.py`(V5)。
- 待新增:`src/inspection_robot/vision/video_stream.py`(V8)。

### Runtime 与异常
- `src/inspection_robot/runtime.py` — 视觉调用、轮次、异常接入。
- `src/inspection_robot/core/rules.py` — 异常判断(已齐全)。
- `src/inspection_robot/core/store.py` — 轮次字段、事件记录、`confirm`。
- `src/inspection_robot/core/events.py` — `make_event`、`EventRecord`。

### 配置
- `src/inspection_robot/config_types.py` — 配置类型定义。
- `src/inspection_robot/config_defaults.py` — 默认值。
- `src/inspection_robot/config.py` — 配置加载。

### 网页与硬件
- `src/inspection_robot/web.py` — 视频流端点、检测结果端点、异常列表。
- `src/inspection_robot/robot/alarm.py` — 灯光(缺货用 `show_high_priority_alarm`)。
- `src/inspection_robot/audio.py` — 音频(本次不动)。

### 测试
- `tests/test_runtime.py` — runtime 测试,需扩展轮次与异常接入。
- `tests/test_rules.py` — 异常判断测试(已齐全,确认 `skip_missing` 覆盖)。
- `tests/test_web_api.py` — 网页 API 测试,需新增视频流端点测试。
- `scripts/test_side_camera_tag_on_car.py` — 真车侧摄像头测试脚本。

### 文档
- `docs/REAL_REQUIREMENTS.md` — 真实需求(优先级最高)。
- 本文 — 视觉修改指引。
