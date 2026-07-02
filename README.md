# 基于 AprilTag 的移动式物品巡检系统

这是一个小团队工程实践项目，目标是在 RASPBOT V2 小车上完成“巡线巡检、AprilTag 识别、异常上报、网页看板、人工确认回收”的闭环演示。

项目边界很清楚：小车负责巡检、识别、告警和上报；人工负责实际回收动作；网页看板负责展示状态、确认异常和导出日志。不做 SLAM、不做自由空间路径规划、不做机械臂抓取、不训练深度学习模型。

## 当前状态

当前仓库已经有一个可运行的软件最小闭环：

- Flask 看板入口：`app.py`
- 状态与事件逻辑：`src/inspection_robot/state.py`
- API 路由：`src/inspection_robot/web.py`
- 标签配置：`config/tag_map.json`
- 单页看板：`src/inspection_robot/templates/dashboard.html`
- 前端轮询脚本：`src/inspection_robot/static/dashboard.js`
- CSV 导出接口：`GET /api/export.csv`

真实小车的 AprilTag、巡线、超声波、蜂鸣器和 RGB 还需要按三个实施计划继续接入。

## 文档入口

- [全局规划书](docs/PROJECT_PLAN.md)：项目目标、官方资料依据、功能边界、6 天推进和答辩思路。
- [共享 API 契约](docs/api_contract.md)：`/api/status`、事件字段、状态枚举和 `InspectionStore` 对外方法。
- [0.1 Plan：共享契约先行](docs/plans/0.1-plan-shared-contract.md)：已完成；契约文件见 `docs/api_contract.md`。
- [1.1 Plan：小车硬件与感知适配](docs/plans/1.1-plan-robot-io.md)：按 `docs/api_contract.md` 接入 AprilTag、巡线、障碍等待、蜂鸣器/RGB 和小车 runtime。
- [2.1 Plan：核心状态机与规则](docs/plans/2.1-plan-core-contract.md)：按 `docs/api_contract.md` 实现异常规则、状态机、持久化和测试。
- [3.1 Plan：看板、部署与演示](docs/plans/3.1-plan-dashboard-demo.md)：按 `docs/api_contract.md` 完成网页看板、演示兜底、部署脚本、中文说明和答辩证据。
- [官方资料索引](docs/RASPBOT-V2_Clean_Docs/README.md)：已精简的 RASPBOT V2 相关官方文档与示例位置。

## 分工建议

0.1 共享契约已完成，共享入口是 `docs/api_contract.md`。现在 1.1、2.1、3.1 可以按计划编号并行推进：

- 负责人 1 执行 `1.1 Plan`：只管小车硬件输入输出。
- 负责人 2 执行 `2.1 Plan`：只管核心状态机、异常规则、持久化和测试。
- 负责人 3 执行 `3.1 Plan`：只管看板、部署和演示材料。

## 本地运行

在仓库根目录安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

Windows 上如果 `python3` 不可用，可使用：

```bash
py -3 -m pip install -r requirements.txt
```

启动本地看板：

```bash
scripts/run_local.sh
```

浏览器打开：

```text
http://127.0.0.1:5050
```

当前页面支持：

- 开始巡检
- 模拟正常标签
- 模拟异常标签
- 人工确认回收
- 重置状态
- 导出 CSV 日志

## 小车部署入口

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

启动小车上的看板：

```bash
scripts/run_on_car.sh
```

停止小车上的看板：

```bash
scripts/stop_on_car.sh
```

如果小车 IP 变化：

```bash
CAR_HOST=pi@新的IP scripts/deploy_to_car.sh
CAR_HOST=pi@新的IP scripts/run_on_car.sh
```

## 验证命令

本地轻量检查：

```bash
py -3 -m py_compile app.py src/inspection_robot/*.py
```

如果后续已新增 `tests/`：

```bash
py -3 -m unittest discover -s tests -v
```

小车硬件验证按 [1.1 Plan](docs/plans/1.1-plan-robot-io.md) 中的脚本执行。

## 代码目录

```text
.
├── app.py
├── config/
│   └── tag_map.json
├── data/
├── docs/
│   ├── PROJECT_PLAN.md
│   ├── plans/
│   │   ├── 0.1-plan-shared-contract.md
│   │   ├── 1.1-plan-robot-io.md
│   │   ├── 2.1-plan-core-contract.md
│   │   └── 3.1-plan-dashboard-demo.md
│   └── RASPBOT-V2_Clean_Docs/
├── scripts/
├── src/
│   └── inspection_robot/
│       ├── config.py
│       ├── state.py
│       ├── web.py
│       ├── static/
│       └── templates/
└── requirements.txt
```

## 重要边界

- AprilTag 只提供标签 ID；物品名和分区来自 `config/tag_map.json`。
- 动态避障只承诺“检测障碍、停车等待、解除后恢复”，不承诺自由绕行。
- 人工回收确认优先使用网页按钮；KEY1 或确认二维码只作为加分项。
- 现场演示要保留软件模拟兜底，避免硬件或网络临时失败导致完全无法展示。
