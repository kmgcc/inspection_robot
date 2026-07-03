# 答辩证据清单

建议把最终素材统一放到 `docs/evidence/` 或答辩资料文件夹，文件名带日期和功能点，便于 PPT 引用和现场兜底。

| 证据项 | 建议文件名 | 验收要点 | 状态 |
|---|---|---|---|
| 场地布置照片 | `field-layout.jpg` | A1-A4、B1-B4、列端“工字形”黑胶带、通道和小车初始位置清楚可见 | 待补 |
| Wi-Fi/网页/SSH/VNC 可用截图 | `network-access.png` | 树莓派联网，网页、SSH、VNC 均可访问 | 待补 |
| 官方 App 禁用证据 | `official-app-disabled.png` | 官方主程序未占用摄像头、底盘、I2C 或端口 | 待补 |
| 网页 robot mode 截图 | `robot-mode-dashboard.png` | 页面清楚显示真车模式和硬件连接状态 | 待补 |
| 手动控制短视频 | `manual-control.mp4` | 前进、停止、横移、原地转向、急停可用 | 待补 |
| 云台侧向初始化视频 | `gimbal-side-init.mp4` | 开机或网页触发后摄像头朝向货架侧面 | 待补 |
| 四路全黑端点转向视频 | `boundary-turn-90.mp4` | 压到横向黑胶带后顺时针转 90 度并继续 | 待补 |
| 第一轮跳过检测截图 | `first-pass-learning.png` | 第一轮识别货架但不产生缺货事件 | 待补 |
| B 列反向巡检证据 | `b4-to-b1-patrol.mp4` | 小车转到 B 列后按 B4、B3、B2、B1 顺序巡检 | 待补 |
| 第二轮缺货截图 | `missing-item-second-cycle.png` | 第二轮出现 `missing_item`，货架和物品可见 | 待补 |
| 红灯和语音警报视频 | `missing-alarm-audio-light.mp4` | 缺货时红灯和语音报警触发 | 待补 |
| 货架提示音视频 | `shelf-audio-cue.mp4` | 扫描到货架时播放提示音 | 待补 |
| 物品提示音视频 | `item-audio-cue.mp4` | 识别到物品时播放不同提示音 | 待补 |
| 货架 AprilTag 识别截图 | `shelf-apriltag.png` | 货架身份来自 AprilTag，OCR 可作辅助 | 待补 |
| 物品多模态识别截图 | `item-multimodal.png` | 物品 AprilTag、文字、图形、颜色可见；颜色可为空 | 待补 |
| 障碍等待 6 秒视频 | `obstacle-wait-6s.mp4` | 障碍出现停车，等待 6 秒后再判断 | 待补 |
| 绕行视频 | `obstacle-avoidance.mp4` | 障碍未解除时执行绕行并恢复方向 | 待补 |
| 嵌套避障或安全停止证据 | `nested-avoidance.png` | 绕行中遇新障碍可中断或进入安全停止 | 待补 |
| 动态拓扑截图 | `runtime-topology.png` | 初始不是假地图，运行后生成货架、边界、异常拓扑 | 待补 |
| 异常确认截图 | `manual-confirm.png` | 待确认事件变为已确认 | 待补 |
| CSV 导出样例 | `inspection_events.csv` | 包含轮次、货架、物品、音频灯光、障碍和异常事件 | 待补 |
| 软件兜底录屏 | `software-demo-loop.mp4` | 无小车也能演示真实主链路状态变化 | 待补 |
| 最终答辩文件 | `final-defense.pptx` 或 `final-defense.pdf` | 包含真实需求、边界、架构、演示流程和风险兜底 | 待补 |

## 现场前检查

- [ ] `docs/REAL_REQUIREMENTS.md` 已作为需求基准。
- [ ] `RUN_MODE=robot` 已验证真车启动。
- [ ] `RUN_MODE=simulate` 仅用于软件兜底。
- [ ] 网页“开始”能让小车运动。
- [ ] 手动控制和急停可用。
- [ ] 第一轮不报缺货。
- [ ] 第二轮缺货会触发红灯和语音。
- [ ] CSV 能下载并打开。
- [ ] PPT 明确写出：不做 SLAM、不做开放场景自动驾驶、不循线、不让 LLM 控底盘。
