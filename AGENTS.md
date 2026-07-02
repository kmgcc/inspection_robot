# AGENTS.md - AI Agent 协作指南

本文档为 AI Agent 提供项目协作指南，帮助快速了解仓库结构、遵循开发规范、高效完成任务。

---

## 一、项目概述

### 1.1 项目定位

**项目名称：** 固定仓库场景下的麦克纳姆轮巡逻小车

**项目目标：** 在 RASPBOT V2 麦轮小车上完成"固定地图路径规划、黑胶带禁区规避、超声波避障、侧向货架扫描、多模态物品识别、物品异常上报、网页看板展示"的课程演示系统。

**项目边界（重要）：**
- ✅ 固定小场景巡逻
- ✅ 货架识别与异常检测
- ✅ 网页看板展示
- ❌ 不做 SLAM
- ❌ 不做开放环境自动驾驶
- ❌ 不训练大型识别模型
- ❌ 不做机械臂搬运
- ⚠️ LLM 仅用于告警后处理摘要，不参与实时底盘控制

### 1.2 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | Python 3 + Flask |
| 硬件平台 | RASPBOT V2 麦轮小车 (树莓派) |
| 机器人框架 | ROS2 (Docker 容器内) |
| 识别技术 | AprilTag TAG36H11, OCR, 颜色识别, 图像分类 |
| 通信协议 | SSH, VNC, HTTP REST API |
| 部署方式 | rsync + SSH, Docker |

### 1.3 分工结构

本项目采用**四人并行开发**模式，以 `docs/api_contract.md` 为共享契约：

| 角色 | 计划文档 | 职责范围 |
|------|----------|----------|
| 基准协调者 | `docs/plans/0.1-plan-shared-contract.md` | 接口、配置、资料依据统一 |
| 队友 1 | `docs/plans/1.1-plan-robot-io.md` | 小车硬件输入输出、麦轮运动、传感器 |
| 队友 2 | `docs/plans/2.1-plan-core-contract.md` | 路径规划、货架清单、异常规则、状态机 |
| 队友 3 | `docs/plans/3.1-plan-dashboard-demo.md` | 看板 UI、部署脚本、演示兜底、答辩证据 |

**关键原则：** 三名队友可以并行推进。1.1 上报观察，2.1 产出状态和事件，3.1 展示 `/api/status`；共同边界以 `docs/api_contract.md` 为准。

---

## 二、开发规范与协作要求

### 2.1 按照计划实施

**强制要求：** 所有开发工作必须严格按照对应的计划文档执行。

**操作流程：**
1. **开始任务前**：先阅读对应的计划文档（`docs/plans/` 目录下）
2. **理解依赖关系**：确认当前任务的前置依赖是否完成
3. **遵循接口契约**：所有接口实现必须符合 `docs/api_contract.md`
4. **保持向后兼容**：不能删除旧字段，只能新增扩展字段

**计划文档入口：**
- `docs/PROJECT_PLAN.md` - 全局规划书
- `docs/api_contract.md` - 共享 API 契约（最重要）
- `docs/plans/0.1-plan-shared-contract.md` - 基准计划
- `docs/plans/1.1-plan-robot-io.md` - 硬件计划
- `docs/plans/2.1-plan-core-contract.md` - 核心逻辑计划
- `docs/plans/3.1-plan-dashboard-demo.md` - 看板演示计划

### 2.2 多人协作：实时同步代码

**强制要求：** 多人合作项目必须注意实时拉取最新代码，查看是否有更新。

**操作流程：**

```bash
# 1. 开始工作前，先拉取最新代码
git pull origin main

# 2. 查看最近的提交记录，了解其他人做了什么
git log --oneline -10

# 3. 查看当前工作区状态
git status

# 4. 如果有本地修改，先 stash 或 commit
git stash  # 临时保存
# 或
git add . && git commit -m "描述你的修改"

# 5. 再次拉取并合并
git pull origin main

# 6. 如果有冲突，手动解决后继续
```

**最佳实践：**
- 每天开始工作前先 `git pull`
- 完成一个小功能后立即 `git push`
- 使用 `git log --oneline -10` 查看最近变更
- 使用 `git diff` 查看具体修改内容

### 2.3 提交时妥善处理冲突

**强制要求：** 提交时要妥善处理冲突，不能强行覆盖他人代码。

**冲突处理流程：**

```bash
# 1. 拉取时发现冲突
git pull origin main
# 输出：CONFLICT (content): Merge conflict in src/xxx.py

# 2. 查看冲突文件
git status
# 输出：both modified: src/xxx.py

# 3. 打开冲突文件，查找冲突标记
# 冲突标记格式：
# <<<<<<< HEAD
# 你的代码
# =======
# 他人的代码
# >>>>>>> origin/main

# 4. 手动解决冲突，保留正确的代码
# 删除冲突标记，合并两边的修改

# 5. 标记冲突已解决
git add src/xxx.py

# 6. 完成合并
git commit -m "merge: 解决 xxx.py 的冲突"

# 7. 推送
git push origin main
```

**冲突预防：**
- 开发前先 `git pull`
- 修改文件前先 `git log --oneline src/xxx.py` 查看最近修改
- 小步提交，减少冲突范围
- 及时沟通，避免同时修改同一文件

### 2.4 代码风格与质量

**Python 代码规范：**
- 遵循 PEP 8 风格指南
- 使用类型注解（Type Hints）
- 保持函数简洁，单一职责
- 添加必要的注释和文档字符串

**提交规范：**
- 提交信息清晰描述修改内容
- 一个提交只做一件事
- 提交前运行测试：`python3 -m unittest discover -s tests -v`

---

## 三、代码查看与分析

### 3.1 仓库目录结构

```
inspection_robot/
├── app.py                          # Flask 看板入口
├── config/                         # 配置文件目录
│   ├── tag_map.json                # 标签字典（物品、货架）
│   ├── warehouse_map.json          # 仓库地图配置（待创建）
│   └── shelf_manifest.json         # 货架清单配置（待创建）
├── data/                           # 运行时数据目录
│   └── .gitkeep
├── docs/                           # 文档目录
│   ├── PROJECT_PLAN.md             # 全局规划书
│   ├── api_contract.md             # 共享 API 契约（核心）
│   ├── ssh_operations.md           # SSH 连接与运维手册
│   ├── plans/                      # 计划文档
│   │   ├── 0.1-plan-shared-contract.md
│   │   ├── 1.1-plan-robot-io.md
│   │   ├── 2.1-plan-core-contract.md
│   │   └── 3.1-plan-dashboard-demo.md
│   └── RASPBOT-V2_Clean_Docs/     # 官方精简资料
├── scripts/                        # 部署和运行脚本
│   ├── deploy_to_car.sh            # 部署到小车
│   ├── run_local.sh                # 本地运行
│   ├── run_on_car.sh               # 小车上运行
│   ├── stop_on_car.sh              # 停止小车服务
│   └── play_audio_on_car.sh        # 播放音频
├── src/                            # 源代码目录
│   └── inspection_robot/           # 主包
│       ├── __init__.py
│       ├── audio.py                # 音频播放
│       ├── config.py               # 配置加载
│       ├── state.py                # 状态管理（InspectionStore）
│       ├── web.py                  # Flask 路由
│       ├── static/                 # 静态资源
│       │   ├── dashboard.js
│       │   ├── styles.css
│       │   └── audio/
│       └── templates/              # HTML 模板
│           └── dashboard.html
└── tests/                          # 测试目录
    └── test_contract.py            # 契约测试
```

### 3.2 核心文件说明

| 文件 | 作用 | 重要程度 |
|------|------|----------|
| `docs/api_contract.md` | 共享 API 契约，定义所有接口 | ⭐⭐⭐⭐⭐ |
| `src/inspection_robot/state.py` | 状态管理核心，InspectionStore | ⭐⭐⭐⭐⭐ |
| `src/inspection_robot/web.py` | Flask 路由，API 端点 | ⭐⭐⭐⭐ |
| `config/tag_map.json` | 标签字典 | ⭐⭐⭐⭐ |
| `tests/test_contract.py` | 契约测试，保证接口不破 | ⭐⭐⭐⭐ |

### 3.3 使用 CodeGraph 高效掌握全局结构

**CodeGraph** 是一个强大的代码分析工具，可以帮助 AI Agent 快速理解代码库结构、函数调用关系和依赖关系。

**安装与初始化：**

```bash
# 1. 安装 CodeGraph CLI（如果未安装）
# 参考官方文档安装

# 2. 初始化索引（首次使用）
codegraph index .

# 3. 查看项目概览
codegraph summary
```

**常用命令：**

```bash
# 查找函数定义
codegraph search "def record_tag"

# 查看函数调用关系
codegraph callers src/inspection_robot/state.py:record_tag

# 查看函数被谁调用
codegraph callees src/inspection_robot/web.py:api_status

# 查看文件依赖关系
codegraph deps src/inspection_robot/web.py

# 生成架构文档
codegraph generate-arch
```

**CodeGraph 在本项目中的应用：**

1. **快速定位函数**：当需要修改某个功能时，先用 `codegraph search` 找到函数位置
2. **理解调用链**：用 `codegraph callers/callees` 了解函数的上下游关系
3. **评估修改影响**：修改前用 `codegraph impact` 分析影响范围
4. **发现循环依赖**：用 `codegraph circular` 检查架构问题

**示例工作流：**

```bash
# 场景：需要修改 record_tag 函数

# 1. 找到函数定义
codegraph search "def record_tag"
# 输出：src/inspection_robot/state.py:125

# 2. 查看谁调用了这个函数
codegraph callers src/inspection_robot/state.py:125
# 输出：web.py:handle_tag, tests/test_contract.py:test_simulate_tag

# 3. 查看这个函数调用了什么
codegraph callees src/inspection_robot/state.py:125
# 输出：_add_event, tag_map.get

# 4. 评估修改影响
codegraph impact src/inspection_robot/state.py:125
# 输出：直接影响 web.py 和 test_contract.py
```

### 3.4 快速上手代码阅读

**推荐阅读顺序：**

1. **先读契约**：`docs/api_contract.md` - 理解系统接口
2. **再读状态**：`src/inspection_robot/state.py` - 理解核心数据结构
3. **然后路由**：`src/inspection_robot/web.py` - 理解 API 端点
4. **最后配置**：`config/tag_map.json` - 理解数据格式

**关键类和函数：**

```python
# 状态管理核心类
class InspectionStore:
    def start()              # 开始巡检
    def stop()               # 停止巡检
    def reset()              # 重置状态
    def record_tag()         # 记录标签识别
    def record_obstacle()    # 记录障碍物
    def confirm()            # 人工确认
    def snapshot()           # 获取状态快照
    def export_events_csv()  # 导出事件 CSV

# Flask 路由端点
@app.get("/api/status")      # 获取状态
@app.post("/api/start")      # 开始巡检
@app.post("/api/stop")       # 停止巡检
@app.post("/api/reset")      # 重置状态
@app.post("/api/simulate/tag/<tag_id>")  # 模拟标签
@app.post("/api/confirm")    # 确认处理
@app.get("/api/export.csv")  # 导出 CSV
```

---

## 四、部署与运维

### 4.1 本地开发环境

**环境要求：**
- Python 3.8+
- pip

**安装依赖：**

```bash
# 进入项目根目录
cd inspection_robot

# 安装依赖
python3 -m pip install -r requirements.txt

# Windows 用户使用
py -3 -m pip install -r requirements.txt
```

**本地运行：**

```bash
# 方式 1：使用脚本（推荐）
scripts/run_local.sh

# 方式 2：直接运行
python3 app.py

# 方式 3：Windows 用户
py -3 app.py
```

**访问地址：** `http://127.0.0.1:5050`

**本地验证：**

```bash
# 语法检查
python3 -m py_compile app.py src/inspection_robot/*.py

# 运行测试
python3 -m unittest discover -s tests -v
```

### 4.2 小车部署流程

**前置条件：**
- 电脑已连接小车热点（默认热点名：`Raspbot`）
- 已获取小车 IP（默认：`192.168.1.11`）

**部署步骤：**

```bash
# 1. 部署代码到小车
scripts/deploy_to_car.sh

# 2. 如果小车 IP 不是默认值
CAR_HOST=pi@新的IP scripts/deploy_to_car.sh

# 3. 启动小车上的服务
scripts/run_on_car.sh

# 4. 访问小车上的服务
# 浏览器打开：http://192.168.1.11:5000

# 5. 查看运行日志
ssh pi@192.168.1.11
cd /home/pi/temp/inspection_robot
tail -f app.log

# 6. 停止服务
scripts/stop_on_car.sh
```

**部署脚本说明：**

| 脚本 | 功能 |
|------|------|
| `scripts/deploy_to_car.sh` | 使用 rsync 同步代码到小车 |
| `scripts/run_on_car.sh` | SSH 到小车启动服务 |
| `scripts/stop_on_car.sh` | SSH 到小车停止服务 |
| `scripts/run_local.sh` | 本地启动服务 |

**部署脚本配置：**

```bash
# 默认配置
CAR_HOST=pi@192.168.1.11
CAR_DIR=/home/pi/temp/inspection_robot
PORT=5050

# 自定义配置
CAR_HOST=pi@192.168.1.100 scripts/deploy_to_car.sh
CAR_DIR=/home/pi/temp/test_inspection scripts/deploy_to_car.sh
```

### 4.3 SSH 连接与运维

**SSH 登录：**

```bash
# 连接小车
ssh pi@192.168.1.11

# 默认密码
yahboom
```

**常用运维命令：**

```bash
# 查看小车 IP
hostname -I

# 查看主机名
hostname

# 关闭官方大程序（开发前必须执行）
sh /home/pi/project_demo/raspbot/killprocess.sh

# 查看端口占用
ss -lntp | grep 5000

# 释放端口
fuser -k 5000/tcp

# 查看进程
ps aux | grep python

# 查看磁盘空间
df -h

# 查看内存使用
free -h
```

**VNC 连接（需要图形界面时）：**

```
地址：192.168.1.11
用户名：pi
密码：yahboom
```

### 4.4 ROS2 Docker 环境

**进入 Docker：**

```bash
# SSH 登录小车后执行
cd ~
./docker_ros2.sh

# 成功后提示符变为
root@yahboom:/#
```

**常用 ROS2 命令：**

```bash
# 查看话题列表
ros2 topic list

# 查看话题数据
ros2 topic echo /ultrasonic
ros2 topic echo /line_sensor

# 发布话题数据
ros2 topic pub -1 /buzzer std_msgs/msg/Bool "data: 1"
ros2 topic pub -1 /rgblight std_msgs/msg/Int32MultiArray "data: [255, 0, 0]"
ros2 topic pub -1 /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.08, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}"

# 启动底盘驱动
ros2 launch yahboomcar_bringup bringup.launch.py

# 运行 AprilTag 识别
ros2 run yahboomcar_apriltag apriltag_identify
```

**第二终端进入同一容器：**

```bash
# 查看运行中的容器
docker ps

# 进入容器
docker exec -it <CONTAINER_ID> /bin/bash
```

### 4.5 常见问题排查

**问题 1：SSH 能连，但网页打不开**

```bash
# 检查服务是否启动
ssh pi@192.168.1.11
cd /home/pi/temp/inspection_robot
tail -n 80 app.log

# 检查端口
ss -lntp | grep 5000

# 重启服务
fuser -k 5000/tcp 2>/dev/null || true
cd /home/pi/temp/inspection_robot
nohup python3 app.py > app.log 2>&1 &
```

**问题 2：连到了别人的车**

现场多台车默认热点名和 IP 可能相同。连接后用蜂鸣器或 RGB 灯确认物理车辆。

**问题 3：Docker 容器未启动**

```bash
cd ~
./docker_ros2.sh
```

**问题 4：ROS2 话题无数据**

```bash
# 关闭官方大程序
sh /home/pi/project_demo/raspbot/killprocess.sh

# 重启底盘驱动
ros2 launch yahboomcar_bringup bringup.launch.py
```

### 4.6 推荐日常开发流程

```
1. 电脑连接本组小车热点
2. SSH 登录小车，确认 IP 和主机
3. 关闭官方大程序
4. 本地运行测试
5. 使用 scripts/deploy_to_car.sh 推送代码
6. 使用 scripts/run_on_car.sh 启动网页看板
7. 浏览器访问 http://192.168.1.11:5000
8. 需要真实硬件时，进入 ROS2 Docker，启动底盘驱动并测试话题
9. 结束后停止网页看板，必要时关闭 Docker 或重启小车
```

---

## 五、API 接口参考

### 5.1 基础接口

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/health` | 健康检查 |
| GET | `/api/status` | 获取状态快照 |
| POST | `/api/start` | 开始巡检 |
| POST | `/api/stop` | 停止巡检 |
| POST | `/api/reset` | 重置状态 |
| POST | `/api/simulate/tag/<tag_id>` | 模拟标签识别 |
| POST | `/api/confirm` | 人工确认处理 |
| GET | `/api/export.csv` | 导出事件 CSV |

### 5.2 状态字段

**基础字段（必须保留）：**

```json
{
  "run_id": "local-001",
  "task_status": "IDLE",
  "robot_status": "待命",
  "current_zone": null,
  "current_tag": null,
  "current_item": null,
  "last_message": "系统已启动",
  "obstacle": {"distance_mm": null, "blocked": false},
  "alarm": {"level": "normal", "message": "正常"},
  "zones": [],
  "events": []
}
```

**扩展字段（新版）：**

```json
{
  "current_shelf": "A1",
  "current_target": "A1_SCAN",
  "pose": {"x": 3, "y": 2, "heading": "E"},
  "path": {"status": "active", "waypoints": [], "next_index": 0},
  "forbidden_zones": [],
  "shelves": [],
  "scan": {"active": false, "shelf_id": null, "detected_items": []},
  "llm_summary": null
}
```

### 5.3 状态枚举

```
IDLE, PLANNING, PLAN_READY, MOVING, ALIGNING_SHELF, SCANNING_SHELF,
ANALYZING, NORMAL_LOGGED, ABNORMAL_ALARM, WAIT_CONFIRM, CONFIRMED,
OBSTACLE_WAIT, REROUTING, FORBIDDEN_ZONE_WAIT, FINISHED, STOPPED
```

### 5.4 事件类型

```
system, path_planned, path_step, path_replanned, forbidden_zone_detected,
obstacle_wait, obstacle_clear, shelf_arrived, shelf_aligned, shelf_scanned,
normal_item, unknown_item, wrong_shelf, missing_item, duplicate_item,
evidence_mismatch, manual_confirm, llm_summary
```

---

## 六、配置文件说明

### 6.1 tag_map.json

标签字典，定义所有 AprilTag 标签的映射关系。

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

**ID 范围约定：**

| 范围 | 用途 |
|------|------|
| 1-50 | 物品标签 |
| 101-120 | 货架标签 |
| 201-220 | 定位点预留 |
| 301-320 | 禁区/特殊点预留 |

### 6.2 warehouse_map.json（待创建）

仓库地图配置。

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

### 6.3 shelf_manifest.json（待创建）

货架清单配置。

```json
{
  "A1": {"expected_items": ["item_01", "item_02", "item_03"]},
  "A2": {"expected_items": ["item_04", "item_05"]}
}
```

---

## 七、测试与验证

### 7.1 运行测试

```bash
# 运行所有测试
python3 -m unittest discover -s tests -v

# 运行特定测试
python3 -m unittest tests.test_contract -v

# 语法检查
python3 -m py_compile app.py src/inspection_robot/*.py
```

### 7.2 测试覆盖要求

- 旧版 API 字段必须保留，测试不能破
- 新版配置必须可加载、可解析
- 异常场景必须有测试覆盖

### 7.3 验证清单

```bash
# 1. 语法检查
python3 -m py_compile app.py src/inspection_robot/*.py

# 2. 运行测试
python3 -m unittest discover -s tests -v

# 3. 本地启动验证
scripts/run_local.sh
# 浏览器访问 http://127.0.0.1:5050

# 4. 部署到小车验证
scripts/deploy_to_car.sh
scripts/run_on_car.sh
# 浏览器访问 http://192.168.1.11:5000
```

---

## 八、重要边界与约束

### 8.1 技术边界

- **黑胶带**：表示禁区、边界或兜底保护线，不是主巡线路径
- **货架识别**：以 AprilTag TAG36H11 为主，OCR 识别货架号作为补充
- **物品识别**：AprilTag 负责稳定身份，颜色和图像用于复核展示
- **AprilTag ID**：必须分段管理，物品、货架、定位点、特殊区域不能复用
- **动态避障**：超声波停车、等待、保守绕行，不承诺开放环境自由驾驶
- **红外传感器**：用于识别黑胶带禁区边界，是安全兜底，不是主循迹控制
- **人工按钮**：用于确认处理或复核异常

### 8.2 开发约束

- 不能删除旧字段，只能新增扩展字段
- 不能在 import 时触碰硬件
- LLM 不参与实时底盘控制
- 现场演示必须保留软件模拟兜底

---

## 九、快速参考

### 9.1 常用命令速查

```bash
# 本地开发
python3 -m pip install -r requirements.txt
scripts/run_local.sh
python3 -m unittest discover -s tests -v

# 部署到小车
scripts/deploy_to_car.sh
scripts/run_on_car.sh
scripts/stop_on_car.sh

# SSH 连接
ssh pi@192.168.1.11

# Git 操作
git pull origin main
git status
git log --oneline -10
git add . && git commit -m "描述"
git push origin main
```

### 9.2 关键文件速查

| 文件 | 用途 |
|------|------|
| `docs/api_contract.md` | API 契约，必读 |
| `src/inspection_robot/state.py` | 状态管理核心 |
| `src/inspection_robot/web.py` | Flask 路由 |
| `config/tag_map.json` | 标签字典 |
| `tests/test_contract.py` | 契约测试 |
| `docs/ssh_operations.md` | SSH 运维手册 |

### 9.3 文档入口速查

| 文档 | 内容 |
|------|------|
| `README.md` | 项目总览 |
| `AGENTS.md` | AI Agent 协作指南（本文档） |
| `docs/PROJECT_PLAN.md` | 全局规划书 |
| `docs/api_contract.md` | 共享 API 契约 |
| `docs/ssh_operations.md` | SSH 运维手册 |
| `docs/plans/*.md` | 各阶段计划文档 |

---

## 十、更新日志

- **2026-07-02**：初始版本，包含项目概述、开发规范、部署流程、API 参考
