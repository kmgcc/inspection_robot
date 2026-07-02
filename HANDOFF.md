# AprilTag 物品巡检小车项目交接

这个仓库是小车项目的代码仓库，不是官方资料包仓库。仓库只保留能直接开发、部署和交接所需的内容；官方镜像、视频、虚拟机、课程大 PPT 和整包源码都不放进 GitHub。

项目方向是“基于 AprilTag 的巡线式物品巡检、异常上报与人工回收确认系统”。当前版本已经完成网页看板的最小闭环：打开看板，点击开始巡检，模拟识别正常或异常标签，异常事件进入待确认状态，人工确认后事件关闭。后续接入真实小车数据时，不需要重写网页，只要把官方 AprilTag 话题收到的标签 ID 转给现有状态处理逻辑。

完整方案、硬件依据、功能取舍和 6 天推进安排放在 `docs/PROJECT_PLAN.md`。交接时先读本文件，做答辩和排期时再读全局规划书。

## 仓库结构

```text
inspection_robot/
├── HANDOFF.md
├── README.md
├── app.py
├── config/
│   └── tag_map.json
├── data/
│   └── .gitkeep
├── docs/
│   └── PROJECT_PLAN.md
├── requirements.txt
├── scripts/
│   ├── deploy_to_car.sh
│   ├── run_local.sh
│   ├── run_on_car.sh
│   └── stop_on_car.sh
└── src/
    └── inspection_robot/
        ├── __init__.py
        ├── config.py
        ├── state.py
        ├── web.py
        ├── static/
        │   ├── dashboard.js
        │   └── styles.css
        └── templates/
            └── dashboard.html
```

`app.py` 是小车和本地都能直接运行的入口。它只负责加载 `src/` 并启动 Flask，不放业务逻辑。

`config/tag_map.json` 是标签配置。AprilTag 本身只识别 ID，物品名称、所在分区和期望分区都由这里映射。演示用的 `4` 号标签故意配置为错放，用来触发异常。

`src/inspection_robot/state.py` 是业务核心，负责巡检状态、标签处理、异常事件和人工确认。真实 AprilTag 数据接入后，应继续调用 `InspectionStore.handle_tag(tag_id)`。

`src/inspection_robot/web.py` 是 Flask API 层。当前接口包括 `/api/status`、`/api/start`、`/api/stop`、`/api/reset`、`/api/simulate/tag/<tag_id>`、`/api/confirm` 和 `/health`。

`src/inspection_robot/templates/` 与 `src/inspection_robot/static/` 是看板页面。页面保持单页结构，便于答辩展示和现场调试。

`scripts/` 是部署和运行脚本。队友换电脑或重新部署时优先使用脚本，不要手动复制散文件。

## 本地运行

在电脑上进入仓库：

```bash
cd inspection_robot
python3 -m pip install -r requirements.txt
scripts/run_local.sh
```

本地默认端口是 `5050`，浏览器打开：

```text
http://127.0.0.1:5050
```

如果需要换端口：

```bash
PORT=5060 scripts/run_local.sh
```

## 小车部署

小车开机并连上热点后，电脑连接小车：

```bash
ssh pi@192.168.1.11
```

默认密码：

```text
yahboom
```

如果小车 IP 不是 `192.168.1.11`，在小车终端执行 `hostname -I` 查看实际地址。

开发前先关闭官方 APP 大程序，避免占用硬件资源：

```bash
sh /home/pi/project_demo/raspbot/killprocess.sh
```

在电脑仓库根目录部署：

```bash
scripts/deploy_to_car.sh
```

如果 IP 变了：

```bash
CAR_HOST=pi@新的IP scripts/deploy_to_car.sh
```

启动小车上的看板：

```bash
scripts/run_on_car.sh
```

电脑浏览器打开：

```text
http://192.168.1.11:5000
```

停止看板：

```bash
scripts/stop_on_car.sh
```

## GitHub 仓库

远程仓库：

```text
https://github.com/kmgcc/inspection_robot.git
```

首次关联：

```bash
git remote add origin https://github.com/kmgcc/inspection_robot.git
git branch -M main
git push -u origin main
```

如果已经关联过远程仓库：

```bash
git remote set-url origin https://github.com/kmgcc/inspection_robot.git
git push -u origin main
```

## 当前功能边界

当前版本的标签来自模拟接口：

```text
POST /api/simulate/tag/<tag_id>
```

真实小车识别要接官方 ROS2 AprilTag 节点。官方资料中 AprilTag 使用 `TAG36H11`，识别程序会发布标签 ID。接入时建议新增 `src/inspection_robot/ros_bridge.py`，订阅 `/single_apriltag_id`，收到 ID 后调用现有的 `InspectionStore.handle_tag(tag_id)`。

声光告警也还没有接入真实 ROS2 发布。异常事件和确认状态已经在 `state.py` 里集中处理，接入时只需要在异常产生和确认完成的位置发布 `/buzzer` 与 `/rgblight`。

巡线、超声波、底盘控制尚未写进本仓库。保守方案是只做低速巡线、遇障停车、障碍消失后继续，不做自由空间绕障、SLAM、机械臂抓取或深度学习训练。

## 官方资料使用边界

这些资料需要保留在本地或网盘，不进入 GitHub：

```text
RASPBOT-V2 AI视觉小车/21.出厂镜像/Raspbotv2AI-20250820.zip
RASPBOT-V2 AI视觉小车/18.ROS2基础教程/虚拟机/ros2_VM.rar
RASPBOT-V2 AI视觉小车/**/*.mp4
RASPBOT-V2 AI视觉小车/19.程序源码汇总/程序源码汇总.zip
```

开发时常查的官方文件：

```text
RASPBOT-V2 AI视觉小车/15.开发环境搭建/4.远程访问/远程访问.pdf
RASPBOT-V2 AI视觉小车/03.小车基础教程/00.开发前的准备/开发前的准备.pdf
RASPBOT-V2 AI视觉小车/17.Docker/5、进入小车的docker容器/5、进入机器人的docker容器.pdf
RASPBOT-V2 AI视觉小车/13.ROS2-机器人底盘与控制/1.机器人信息发布/机器人信息发布.pdf
RASPBOT-V2 AI视觉小车/14.ROS2-opencv系列课程/9.AprilTag标签码识别/9.AprilTag标签码识别.pdf
```

## 提交前检查

```bash
python3 -m py_compile app.py src/inspection_robot/*.py
git status --short
du -sh .
```

仓库应保持在 KB 或少量 MB 级别。若出现 `.zip`、`.mp4`、`.rar`、镜像文件、日志文件或缓存目录，先删掉再提交。

## 2026-07-02 Codex 交接记录

### 已完成步骤

- [x] 将远端 `https://github.com/kmgcc/inspection_robot.git` 同步到本地 `C:\Users\15pro\Desktop\小学期`，当前分支为 `main`，同步到提交 `4e25097`。
- [x] 按最新确认，错误的本地资料上传计划已取消；当前本地只保留仓库源码和本机缓存目录。
- [x] 将 `.codegraph/` 加入 `.gitignore`，避免本地代码索引缓存被误提交。
- [x] 定位并处理本机 GitHub 拉取失败原因：全局 Git 代理 `127.0.0.1:7897` 会导致 HTTPS TLS EOF/握手失败；已在本仓库本地配置中清空 `http.proxy` 与 `https.proxy`，未改全局配置。
- [x] 阅读 `HANDOFF.md` 与 `docs/PROJECT_PLAN.md`，确认当前软件侧可继续推进的可验证任务是日志导出。
- [x] 新增 `GET /api/export.csv`，看板增加“导出日志”按钮，导出的 CSV 包含事件 ID、时间、标签、物品、分区、状态和说明。
- [x] 修复本机运行脚本兼容性：`scripts/run_local.sh` 会自动选择可用的 `python3`、`python` 或 `py -3`，避免 WindowsApps 的 `python3` 占位符导致退出 49。

### 本轮验证

```bash
py -3 -m py_compile app.py src/inspection_robot/*.py
```

已通过 Flask 测试客户端走通：开始巡检、模拟正常标签、模拟异常标签、确认回收、导出 CSV。注意本机 Git Bash 的 `python3` 指向 WindowsApps 占位符，会直接退出 49；本轮验证改用 `py -3`。

已从真实脚本入口验证：

```bash
scripts/run_local.sh
```

本地服务已通过 HTTP 验证：

```text
http://127.0.0.1:5050
```

验证内容：`/health` 返回 `{"ok": true}`；首页包含“导出日志”按钮；通过 HTTP 调用开始巡检、模拟正常标签、模拟异常标签后，`/api/export.csv` 能导出包含数据行的 CSV。

验证完成后，本地看板服务已关闭，`5050` 端口不再监听。

### 资料状态更正

已按最新口径取消资料上传计划：本轮提交只包含仓库源码、脚本、配置和交接文档，不包含本地课程资料。

### 下一步建议

- [ ] 若有小车环境，按交接文档接入真实 ROS2 AprilTag 话题 `/single_apriltag_id`，继续调用 `InspectionStore.handle_tag(tag_id)`。
- [ ] 若仍在本机开发，补一个缺失/重复标签的异常规则，再更新看板展示。
