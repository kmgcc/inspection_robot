# 固定仓库场景下的麦克纳姆轮巡逻小车

这是一个 RASPBOT V2 麦轮小车课程项目，目标是在固定小仓库场景中完成“路径规划、黑胶带禁区规避、超声波避障、侧向货架扫描、多模态物品识别、异常上报、网页看板展示和人工复核确认”的演示闭环。

项目边界：不做 SLAM，不做开放场景自动驾驶，不训练大型识别模型，不做机械臂搬运，不让 LLM 控制底盘。LLM 只可作为告警后的摘要和建议加分项。

## 当前代码状态

当前仓库已经具备可运行的软件闭环：

- Flask 入口：`app.py`
- 共享契约：`docs/api_contract.md`
- 状态与事件存储：`src/inspection_robot/core/store.py`
- Web API：`src/inspection_robot/web.py`
- 固定仓库地图：`config/warehouse_map.json`
- 货架清单：`config/shelf_manifest.json`
- 标签配置：`config/tag_map.json`
- 新版看板：`src/inspection_robot/templates/dashboard.html`
- 前端渲染：`src/inspection_robot/static/dashboard.js`
- CSV 导出：`GET /api/export.csv`

3.1 已提供软件兜底演示接口，可在没有小车时演示路径、货架扫描、障碍、禁区、异常、证据冲突、确认处理和日志导出。

## 文档入口

- [全局规划书](docs/PROJECT_PLAN.md)：项目定位、资料依据、场景设计、模块边界和验收清单。
- [共享 API 契约](docs/api_contract.md)：`/api/status`、事件字段、状态枚举、地图/货架/路径配置和 `InspectionStore` 方法。
- [SSH 连接与运维手册](docs/ssh_operations.md)：小车账号、SSH/VNC、代码部署、看板运行和 ROS2 操作。
- [演示运行手册](docs/demo_runbook.md)：真车流程、软件兜底、硬件失败兜底、网络失败兜底和 2-3 分钟答辩顺序。
- [答辩证据清单](docs/evidence_checklist.md)：视频、截图、CSV 和 PPT 素材检查表。
- [官方资料索引](docs/RASPBOT-V2_Clean_Docs/README.md)：精简后的 RASPBOT V2 官方文档和示例。

## 执行计划入口

- [0.1 基准计划](docs/plans/0.1-plan-shared-contract.md)：共享契约、配置格式和资料依据。
- [1.1 队友计划：小车硬件与侧向感知](docs/plans/1.1-plan-robot-io.md)：麦轮运动、超声波、黑胶带传感器、侧向摄像头和 runtime。
- [2.1 队友计划：路径规划与货架规则](docs/plans/2.1-plan-core-contract.md)：固定地图、A*、货架清单、异常规则、状态机和持久化。
- [3.1 队友计划：看板、模拟演示、部署与答辩证据](docs/plans/3.1-plan-dashboard-demo.md)：地图看板、模拟演示、部署脚本、运行手册和证据清单。

四份计划以 `docs/api_contract.md` 为边界并行推进：1.1 上报观察，2.1 产出状态和事件，3.1 展示 `/api/status` 并提供演示兜底。

## 本地运行

安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

Windows 上如果 `python3` 不可用：

```bash
py -3 -m pip install -r requirements.txt
```

启动本地看板，默认端口为 `5050`：

```bash
scripts/run_local.sh
```

浏览器打开：

```text
http://127.0.0.1:5050
```

当前看板支持：

- 开始巡逻、停止、重置
- 模拟规划路径
- 模拟障碍等待和障碍解除
- 模拟黑胶带禁区触发和恢复
- 模拟扫描 A1 正常
- 模拟扫描 A2 异常
- 模拟识别证据冲突
- 软件兜底全流程
- 确认处理
- 导出 CSV 日志

## 小车部署

默认小车地址：

```text
pi@192.168.1.11
```

默认部署目录：

```text
/home/pi/temp/inspection_robot
```

部署到小车：

```bash
scripts/deploy_to_car.sh
```

启动小车看板，默认端口为 `5000`。模拟模式只启动网页和软件演示，不触碰硬件：

```bash
RUN_MODE=simulate scripts/run_on_car.sh
```

真实硬件 runtime 接入后再启用：

```bash
RUN_MODE=robot scripts/run_on_car.sh
```

停止小车看板：

```bash
scripts/stop_on_car.sh
```

如果小车 IP 或端口变化：

```bash
CAR_HOST=pi@新的IP scripts/deploy_to_car.sh
CAR_HOST=pi@新的IP PORT=5000 RUN_MODE=simulate scripts/run_on_car.sh
CAR_HOST=pi@新的IP PORT=5000 scripts/stop_on_car.sh
```

## 验证命令

本地编译检查：

```bash
py -3 -m py_compile app.py src/inspection_robot/*.py src/inspection_robot/core/*.py
```

运行全部测试：

```bash
py -3 -m unittest discover -s tests -v
```

3.1 Web API 检查：

```bash
py -3 -m unittest tests.test_web_api -v
```

文档叙事检查：

```bash
rg -n "固定仓库场景|麦克纳姆|货架|禁区" README.md docs src scripts
```

## 代码目录

```text
.
├── app.py
├── config/
│   ├── shelf_manifest.json
│   ├── tag_map.json
│   └── warehouse_map.json
├── data/
├── docs/
│   ├── PROJECT_PLAN.md
│   ├── api_contract.md
│   ├── demo_runbook.md
│   ├── evidence_checklist.md
│   ├── ssh_operations.md
│   └── plans/
├── scripts/
├── src/
│   └── inspection_robot/
│       ├── core/
│       ├── static/
│       ├── templates/
│       └── web.py
└── tests/
```

## 重要边界

- 黑胶带表示禁区、边界或安全兜底线，不是主巡线路径。
- AprilTag TAG36H11 是货架和物品的主身份来源；OCR、颜色和图像类别是复核证据。
- 识别证据冲突进入人工复核，不自动覆盖 AprilTag 主 ID。
- 动态避障只承诺超声波停车等待、恢复或保守重规划，不承诺开放环境自由绕障。
- `RUN_MODE=simulate` 不触碰硬件；`RUN_MODE=robot` 留给 1.1 的真实 runtime。
- 现场演示必须保留软件兜底，避免硬件或网络临时失败导致无法展示主链路。
