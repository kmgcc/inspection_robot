# 答辩证据清单

建议把最终素材统一放到 `docs/evidence/` 或答辩资料文件夹，文件名带日期和功能点，便于 PPT 引用和现场兜底。

| 证据项 | 建议文件名 | 验收要点 | 状态 |
|---|---|---|---|
| 场地布置照片 | `field-layout.jpg` | 白地、黑胶带禁区、A1/A2/B1/B2 货架清楚可见 | 待补 |
| 麦轮前进、横移、旋转短视频 | `mecanum-motion.mp4` | 前进、横移、原地旋转和停车可区分 | 待补 |
| 路径规划避开禁区截图或录屏 | `path-planning-dashboard.mp4` | 看板地图显示禁区和规划路径 | 待补 |
| 超声波障碍等待/解除短视频 | `obstacle-wait-clear.mp4` | 障碍出现停车，移开后恢复 | 待补 |
| 侧向摄像头扫描货架视频 | `side-camera-shelf-scan.mp4` | 摄像头朝向货架，能看到扫描姿态 | 待补 |
| 至少 4 个标签识别截图 | `apriltag-four-ids.png` | 标签 ID 可读，物品/货架范围不混用 | 待补 |
| 货架“上字下码”识别截图 | `shelf-ocr-tag.png` | 货架号 OCR 与 AprilTag 同屏 | 待补 |
| 多模态证据同屏截图 | `multimodal-evidence.png` | AprilTag、颜色、文字、图像类别同时展示 | 待补 |
| 缺失异常截图 | `missing-item.png` | `missing_item` 事件和货架号可见 | 待补 |
| 重复异常截图 | `duplicate-item.png` | `duplicate_item` 事件和物品名可见 | 待补 |
| 错放异常截图 | `wrong-shelf.png` | 当前货架与期望货架可见 | 待补 |
| 未知物品异常截图 | `unknown-item.png` | 未知标签进入待确认 | 待补 |
| 识别证据冲突截图 | `evidence-mismatch.png` | OCR/颜色/图像类别冲突可见 | 待补 |
| RGB/蜂鸣器告警视频 | `alarm-buzzer-rgb.mp4` | 正常、障碍、异常提示可区分 | 待补 |
| 看板确认处理截图 | `manual-confirm.png` | 待确认事件变为已确认 | 待补 |
| CSV 导出样例 | `inspection_events.csv` | 包含路径、障碍、货架扫描、异常和确认事件 | 待补 |
| 软件兜底演示录屏 | `software-demo-loop.mp4` | 无小车也能完整演示路径、扫描、异常和确认 | 待补 |
| 最终答辩文件 | `final-defense.pptx` 或 `final-defense.pdf` | 包含项目边界、架构、演示流程和风险兜底 | 待补 |

## 现场前检查

- [ ] `scripts/run_local.sh` 能打开本地看板。
- [ ] `RUN_MODE=simulate scripts/run_on_car.sh` 能打开小车看板，且不触碰硬件。
- [ ] 真实硬件接入后，`RUN_MODE=robot scripts/run_on_car.sh` 已验证。
- [ ] “软件兜底全流程”按钮能生成 `evidence_mismatch` 和 `manual_confirm`。
- [ ] CSV 能下载并打开。
- [ ] 所有视频可离线播放。
- [ ] PPT 明确写出：不做 SLAM、不做开放场景自动驾驶、不让 LLM 控底盘。
