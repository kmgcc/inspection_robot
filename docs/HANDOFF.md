# 交接说明

这个仓库是小车项目的代码仓库，不是官方资料包仓库。官方资料体积很大，尤其是镜像、虚拟机、视频和源码压缩包，不要直接放进 GitHub。需要查资料时看 `docs/OFFICIAL_REFERENCE_INDEX.md`。

## 当前已经完成

- 电脑可以通过 SSH 连接小车：`ssh pi@192.168.1.11`，默认密码为 `yahboom`。
- 小车网页看板已能运行：`http://192.168.1.11:5000`。
- 看板已支持开始巡检、模拟正常标签、模拟异常标签、人工确认回收、重置。
- 代码已拆成配置、状态、Web 页面三层，后续接 ROS2 数据时不用重写页面。

## 队友接手后先做什么

1. 拉取 GitHub 私有仓库。
2. 在电脑上运行 `scripts/run_local.sh`，确认本地网页能打开。
3. 用 `scripts/deploy_to_car.sh` 同步到小车。
4. 用 `scripts/run_on_car.sh` 在小车上启动看板。
5. 进入 Docker 启动官方 AprilTag 节点，再按 `docs/NEXT_STEPS.md` 接真实标签数据。

## 关键目录

```text
app.py                         # 小车上直接运行的入口
config/tag_map.json            # 标签 ID 到物品/分区的映射
src/inspection_robot/state.py   # 巡检状态与事件处理
src/inspection_robot/web.py     # Flask API
src/inspection_robot/templates/ # 页面模板
src/inspection_robot/static/    # CSS 和 JS
scripts/                       # 部署和运行脚本
docs/                          # 交接文档
```

## 当前代码边界

现在的标签是模拟接口，不是真实摄像头数据。真实数据要来自官方 ROS2 AprilTag 节点，目标话题优先看 `/single_apriltag_id`，其次看 `/apriltag_positions`。
