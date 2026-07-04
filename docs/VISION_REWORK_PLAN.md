# 视觉识别系统改造完成记录

**状态：** 本轮软件改造已完成；仅保留必须上真实小车验证的硬件证据缺口。
**依据：** `docs/REAL_REQUIREMENTS.md` 第四节、7.5、10.2、10.4，以及用户新增的边界黑胶带高速漏检、避障单段距离过大问题。
**验证：** `py -3 -m unittest tests.test_vision_detector tests.test_runtime tests.test_runtime_route_safety tests.test_web_api -v` 已通过；全量验证以最终执行记录为准。

## 一、完成情况总览

| 编号 | 状态 | 模块 | 本轮落地 |
|---|---|---|---|
| R1 | 已完成，默认关闭 | vision | 新增 `DetectionStabilityTracker`，按中心点、四角位移、角度和连续帧数判断稳定；runtime 可通过 `VISION_STABILITY_ENABLED=1` 启用。 |
| R2 | 已完成，默认关闭 | vision + runtime | 新增 `VisionStateMachine`，状态为 `IDLE -> SEARCHING -> ALIGNING -> CAPTURE -> OCR -> VERIFY -> DONE`；只标注视觉状态，不驱动底盘。 |
| R3 | 已补充软件观测，待上车 | vision + web | `/api/video/detections` 增加 `fps/latency_ms/updated_at`，视频流继续使用共享帧源；真实摄像头并发稳定性仍需上车跑。 |
| R4 | 已完成轻量方案，默认关闭 | vision | 新增 OpenCV 形状分类器，可识别简单 `CARD/BOX/CYLINDER`，通过 `IMAGE_CLASSIFIER_ENABLED=1` 启用；不默认制造证据冲突。 |
| R5 | 已完成首版 | vision | OCR ROI 增加卡片轮廓检测与透视矫正，低置信度仍返回 `None`。 |
| R6 | 已完成评估工具 | scripts | 新增 `scripts/evaluate_ocr_engines.py`，可用同一批实拍图片对比 pytesseract 与可选 PaddleOCR 的文本和耗时。 |
| R7 | 已完成首版 | audio + runtime | `missing_item` 高优先级异常触发本地 TTS 语音报警并节流；无 TTS 命令时记录失败但不阻塞巡逻。 |
| R8 | 已完成策略 | runtime + web | 连续扫描无货架时产生 `scan_failed` 待确认事件；新增 `/api/cycle/confirm`，由操作者确认后进入下一轮。 |
| B1 | 已完成 | runtime | 运动段内以 `MOTION_GUARD_POLL_SECONDS` 高频轮询黑胶带，捕获四路全黑后立即停车并锁存，下一循环执行列端/禁区动作。 |
| B2 | 已完成 | runtime | 避障默认单段距离缩短：基础段 `0.35s`，侧移/回线 `0.8` 车身，越障前进 `1.4` 车身。 |

## 二、关键开关

| 环境变量 | 默认值 | 作用 |
|---|---:|---|
| `VISION_STABILITY_ENABLED` | `false` | 启用完整帧稳定性 tracker。 |
| `VISION_MIN_STABLE_FRAMES` | `3` | 连续稳定帧数阈值。 |
| `VISION_MAX_CENTER_SHIFT_PX` | `10.0` | 同一 tag 中心点最大允许位移。 |
| `VISION_MAX_CORNER_SHIFT_PX` | `14.0` | 同一 tag 四角点最大允许位移。 |
| `VISION_MAX_ANGLE_DELTA_DEG` | `8.0` | 同一 tag 角度最大允许变化。 |
| `VISION_STATE_MACHINE_ENABLED` | `false` | 启用视觉状态机标注，不控制底盘。 |
| `IMAGE_CLASSIFIER_ENABLED` | `false` | 启用轻量 OpenCV 图像类别识别。 |
| `MOTION_GUARD_POLL_SECONDS` | `0.02` | 运动过程中黑胶带轮询间隔，用于高速捕获边界。 |
| `CAMERA_FAILURE_SCAN_THRESHOLD` | `8` | 连续多少次扫描无货架后请求人工确认轮次。 |
| `MISSING_ALERT_COOLDOWN_SECONDS` | `8.0` | 缺货语音报警节流间隔。 |

## 三、真实小车验证清单

- [ ] robot 模式首页视频连续显示，扫描触发时视频流不中断或可自动恢复。
- [ ] `/api/video/detections` 在真实摄像头下持续更新 `frame_id/source/error/detections/fps/latency_ms`。
- [ ] 记录树莓派真实可接受的视频分辨率、FPS、延迟和 CPU 占用。
- [ ] 开启 `VISION_STABILITY_ENABLED=1` 后，低速移动不会频繁误判为新目标。
- [ ] 开启 `IMAGE_CLASSIFIER_ENABLED=1` 后，用现场卡片/物品照片确认不会误触发大量 `evidence_mismatch`。
- [ ] 缺货时红灯和 TTS 均触发；若车上没有 TTS 命令，按日志安装 `espeak-ng`、`espeak` 或 `spd-say` 后复测。
- [ ] 连续扫描无货架时网页出现待确认事件，调用 `/api/cycle/confirm` 后轮次进入下一轮。
- [ ] 高速压到四路全黑时立即停车并执行列端转向，不再越过边界。
- [ ] 禁区/障碍绕行每段距离比旧参数更短，仍能绕过并回到巡逻方向。

## 四、使用建议

默认配置优先保护主链路：稳定性 tracker、视觉状态机、图像分类器都先关闭；真实小车验证时逐个开启。若上车后边界仍有漏检，优先降低 `MOTION_GUARD_POLL_SECONDS` 或降低行驶速度，而不是增加消抖样本数。
