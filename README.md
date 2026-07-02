# 固定仓库场景下的麦克纳姆轮巡逻小车

这是一个小团队工程实践项目，目标是在 RASPBOT V2 麦轮小车上完成“固定地图路径规划、黑胶带禁区规避、超声波避障、侧向货架扫描、多模态物品识别、物品异常上报、网页看板展示”的课程演示系统。

项目边界很清楚：小车在固定小场景中巡逻，不做 SLAM，不做开放环境自动驾驶，不训练大型识别模型，不做机械臂搬运。LLM 可以作为告警后处理的加分项，用来生成巡检摘要和处置建议；它不参与实时底盘控制。

## 当前状态

当前仓库已经有一个 0.1 软件最小闭环：

- Flask 看板入口：`app.py`
- 状态与事件逻辑：`src/inspection_robot/state.py`
- API 路由：`src/inspection_robot/web.py`
- 标签配置：`config/tag_map.json`
- 单页看板：`src/inspection_robot/templates/dashboard.html`
- 前端轮询脚本：`src/inspection_robot/static/dashboard.js`
- CSV 导出接口：`GET /api/export.csv`

这套代码仍是旧的最小契约版本，字段以 `zone/tag/item` 为主。新版计划已经在文档中对齐，后续要按 `docs/api_contract.md` 扩展固定地图、货架、路径、扫描和异常规则。

## 文档入口

- [全局规划书](docs/PROJECT_PLAN.md)：新版项目定位、资料依据、场景设计、功能模块、分工和验收标准。
- [共享 API 契约](docs/api_contract.md)：`/api/status`、事件字段、状态枚举、地图/货架配置和 `InspectionStore` 扩展方法。
- [0.1 基准计划](docs/plans/0.1-plan-shared-contract.md)：共享契约、配置格式和资料依据。
- [1.1 队友计划：小车硬件与侧向感知](docs/plans/1.1-plan-robot-io.md)：麦轮运动、超声波、黑胶带禁区兜底、侧向摄像头和 runtime。
- [2.1 队友计划：路径规划与货架规则](docs/plans/2.1-plan-core-contract.md)：固定地图、A*、货架清单、异常规则、状态机和测试。
- [3.1 队友计划：看板与演示](docs/plans/3.1-plan-dashboard-demo.md)：地图看板、模拟演示、部署脚本、运行手册和答辩证据。
- [官方资料索引](docs/RASPBOT-V2_Clean_Docs/README.md)：已精简的 RASPBOT V2 官方文档与示例位置。
- [打印素材目录](../打印素材_AprilTag)：已生成的物品/货架 AprilTag 标签、A4 PDF 和 ID 对照表。

## 分工建议

四份执行文档的关系如下：

- 基准协调者执行 `0.1`：保证接口、配置和资料依据统一。
- 队友 1 执行 `1.1`：只管真实小车硬件输入输出。
- 队友 2 执行 `2.1`：只管路径规划、货架清单和异常判定。
- 队友 3 执行 `3.1`：只管看板、部署、兜底演示和答辩证据。

三名队友可以并行推进。1.1 上报观察，2.1 产出状态和事件，3.1 展示 `/api/status`；共同边界以 `docs/api_contract.md` 为准。

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

当前页面仍支持：

- 开始巡检
- 模拟正常标签
- 模拟异常标签
- 人工确认处理
- 重置状态
- 导出 CSV 日志

后续 3.1 会把页面改成新版地图、路径、货架和异常看板。

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

后续 3.1 会让 `run_on_car.sh` 区分：

```bash
RUN_MODE=simulate scripts/run_on_car.sh
RUN_MODE=robot scripts/run_on_car.sh
```

## 验证命令

本地轻量检查：

```bash
py -3 -m py_compile app.py src/inspection_robot/*.py
```

运行测试：

```bash
py -3 -m unittest discover -s tests -v
```

小车硬件验证按 [1.1 队友计划](docs/plans/1.1-plan-robot-io.md) 中的脚本执行。

## 代码目录

```text
.
├── app.py
├── config/
│   └── tag_map.json
├── data/
├── docs/
│   ├── PROJECT_PLAN.md
│   ├── api_contract.md
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

- 黑胶带在新版方案中表示禁区、边界或兜底保护线，不是主巡线路径。
- 货架识别以 AprilTag TAG36H11 为主，OCR 识别上方大号货架号作为补充；标签版式固定为“上方货架号、下方 AprilTag、底部数字脚注”。
- 物品识别采用 AprilTag、图像、文字、颜色块共同参与的多模态方案；AprilTag 负责稳定身份，颜色和图像用于复核、展示和加分检测。
- AprilTag ID 必须分段管理：物品、货架、定位点、特殊区域不能复用同一个语义 ID。
- 动态避障采用超声波停车、等待、保守绕行或短路径重规划，不承诺开放环境自由驾驶。
- 底部四路红外传感器可用于识别黑胶带禁区边界；它是安全兜底，不是主循迹控制。
- 人工按钮用于确认处理或复核异常，不再使用旧版回收叙事。
- 现场演示必须保留软件模拟兜底，避免硬件或网络临时失败导致无法展示系统主链路。
