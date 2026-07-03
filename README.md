# 固定货架通道巡逻小车

这是一个 RASPBOT V2 麦克纳姆轮小车课程项目。真实目标不是循线，也不是按预设栅格地图跑 A* 路径，而是在固定货架通道中自动往返巡逻：侧向扫描货架和物品，检测缺货、重复、错放、未知物品和识别证据冲突，并通过灯光、声音、网页看板和日志进行上报。

当前最重要的需求文档是：

- [真实需求基准文档](docs/REAL_REQUIREMENTS.md)

如果其他文档和它冲突，以 `REAL_REQUIREMENTS.md` 为准。

## 当前真实主链路

1. 小车放在初始位置。
2. 开机自启或网页点击“开始”后，小车慢速向前巡逻。
3. 云台初始化到侧向，让摄像头面向货架。
4. 货架通过 AprilTag 识别。
5. 物品通过 AprilTag、文字、图形识别；颜色可选，有则记录，没有不能报错。
6. A 列当前明确为 `A1`、`A2`、`A3`、`A4`。
7. 小车到货架尽头遇到横向黑胶带，四路传感器全黑时顺时针原地转 90 度并继续。
8. 第一轮只记录观察，不报缺货。
9. 第二轮及之后开始检测并上报缺货等异常。
10. 遇到障碍先等待 6 秒，障碍仍在再绕行。
11. 网页显示运行模式、硬件连接、当前轮次、当前货架、事件和动态生成的巡检拓扑。

推荐算法方向：传感器触发流程，AprilTag 做定位和身份，轻量检测模型只确认卡片/货架槽位是否进入扫描区，OpenCV 做透视矫正，PaddleOCR 读取局部文字，Piper 或固定音频做离线播报。第一阶段可先不训练模型，用 AprilTag + 轮廓 + OCR 完成演示；第二阶段再把少类别 YOLO 轻量模型放到 AI HAT+ 上运行。

## 文档入口

- [真实需求基准文档](docs/REAL_REQUIREMENTS.md)：当前最高优先级需求。
- [全局规划书](docs/PROJECT_PLAN.md)：按真实需求同步后的总计划。
- [共享 API 契约](docs/api_contract.md)：状态字段、事件、运行模式、动态拓扑和控制接口。
- [0.1 基准计划](docs/plans/0.1-plan-shared-contract.md)：共享契约与配置格式。
- [1.1 硬件计划](docs/plans/1.1-plan-robot-io.md)：底盘、云台、超声波、黑胶带、音频灯光和 runtime。
- [2.1 核心计划](docs/plans/2.1-plan-core-contract.md)：轮次、货架清单、识别证据、异常规则和动态拓扑。
- [3.1 看板计划](docs/plans/3.1-plan-dashboard-demo.md)：网页控制、运行模式、动态拓扑和演示兜底。
- [SSH 连接与运维手册](docs/ssh_operations.md)：小车账号、SSH/VNC、部署和官方程序清理。
- [演示运行手册](docs/demo_runbook.md)：真车演示、网页演示和兜底流程。
- [答辩证据清单](docs/evidence_checklist.md)：需要录制或截图的证据。
- [官方资料索引](docs/RASPBOT-V2_Clean_Docs/README.md)：保留的 RASPBOT V2 资料说明。

## 本地运行

安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

启动本地看板，默认端口为 `5050`：

```bash
scripts/run_local.sh
```

浏览器打开：

```text
http://127.0.0.1:5050
```

本地模式主要用于软件兜底演示和接口检查，不应假装连接了真实底盘。

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

模拟模式只启动网页和软件演示，不触碰硬件：

```bash
RUN_MODE=simulate scripts/run_on_car.sh
```

真车模式应启动真实 runtime：

```bash
RUN_MODE=robot scripts/run_on_car.sh
```

停止小车看板：

```bash
scripts/stop_on_car.sh
```

## 运维要求

真车运行前必须确认：

1. 官方 Yahboom App 主程序已禁用或启动前被清理，不占用摄像头、底盘、I2C 和端口。
2. Raspberry Pi 开机后 Wi-Fi 正常。
3. 网页可访问。
4. SSH 可访问。
5. VNC 可访问。
6. 网页明确显示当前是 `simulate` 还是 `robot`。
7. 急停按钮始终可用。

## 验证命令

本地编译检查：

```bash
python3 -m py_compile app.py src/inspection_robot/*.py src/inspection_robot/core/*.py src/inspection_robot/robot/*.py src/inspection_robot/vision/*.py
```

运行全部测试：

```bash
python3 -m unittest discover -s tests -v
```

文档冲突检查：

```bash
rg -n "A\\*|固定栅格|避开禁区|A1/A2/B1/B2|模拟规划路径" README.md docs
```

## 重要边界

1. 黑胶带不是主巡线路径。
2. 四路全黑表示列端或禁区触发区，主动作是顺时针原地转 90 度。
3. 非预期禁区或局部压黑按障碍逻辑处理。
4. 网页初始不展示假的固定地图，只展示空状态或运行中生成的拓扑/轨迹。
5. AprilTag 是货架主身份来源。
6. 物品识别支持 AprilTag、文字、图形；颜色可选。
7. 第一轮不报缺货，第二轮开始检测缺货。
8. LLM 不参与实时底盘控制。
