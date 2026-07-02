# AprilTag 物品巡检小车

基于 RASPBOT-V2 官方镜像的工程实践项目。当前仓库先完成“网页看板 + 异常上报 + 人工确认”的最小闭环，后续把模拟标签接口替换为 ROS2 AprilTag 识别数据。

## 快速运行

在小车上：

```bash
cd /home/pi/temp/inspection_robot
python3 app.py
```

电脑浏览器打开：

```text
http://192.168.1.11:5000
```

如果小车 IP 变了，用 `hostname -I` 查看实际地址。

## 当前状态

- 已完成 Flask 网页看板。
- 已完成正常标签、异常标签、人工确认的模拟闭环。
- 已整理 GitHub 可上传的干净仓库结构。
- 下一步接入官方 AprilTag ROS2 话题 `/single_apriltag_id`。

## 仓库结构

```text
inspection_robot/
├── app.py                    # 兼容入口，小车上直接运行它
├── config/tag_map.json       # 标签 ID 与物品/分区映射
├── docs/                     # 交接、部署、资料索引
├── scripts/                  # 部署与运行脚本
├── src/inspection_robot/     # 后端源码
└── requirements.txt
```

## 交接入口

队友先读：

1. `docs/HANDOFF.md`
2. `docs/DEPLOY_TO_CAR.md`
3. `docs/NEXT_STEPS.md`
