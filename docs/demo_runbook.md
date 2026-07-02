# 仓库巡逻演示运行手册

本文属于 3.1 看板与演示部分，只负责演示编排、网页看板、部署运行和兜底说明。真实麦轮运动、侧向摄像头、超声波、黑胶带传感器、RGB/蜂鸣器由 1.1 接入；路径规划和货架异常规则由 2.1 提供。

## 一、场地布置

1. 地面使用白色板材或白纸，黑胶带只表示禁区、边界或安全兜底线，不作为主巡线路径。
2. 按 `config/warehouse_map.json` 布置 8x6 固定栅格，禁区对应 `forbidden_cells`。
3. 设置 A1、A2、B1、B2 四个货架扫描点，货架位置与 `shelf_points` 保持一致。
4. 物品清单以 `config/shelf_manifest.json` 为准。
5. 货架标签采用“上方货架号、下方 AprilTag、底部数字脚注”。
6. 物品标签采用“颜色块、简化图像、文字名称、AprilTag、底部数字脚注”。

打印素材使用：

- 物品标签：`打印素材_AprilTag/item_labels_A4.pdf`
- 货架标签：`打印素材_AprilTag/shelf_labels_A4.pdf`
- ID 对照：`打印素材_AprilTag/manifest.csv`

如果打印素材目录不在当前仓库内，答辩前将 PDF 和 CSV 放到同一台电脑可访问的位置，并在 PPT 中引用截图或文件名。

## 二、真车演示流程

1. 部署代码：

```bash
scripts/deploy_to_car.sh
```

2. 启动小车看板。硬件 runtime 未接入时用模拟模式；1.1 接入后再启用 robot：

```bash
RUN_MODE=simulate scripts/run_on_car.sh
RUN_MODE=robot scripts/run_on_car.sh
```

3. 浏览器打开：

```text
http://小车IP:5000
```

4. 点击“开始巡逻”，看板进入规划或路径就绪状态。
5. 展示固定地图、黑胶带禁区和 A1/A2/B1/B2 货架扫描点。
6. 小车根据规划路线移动，遇到前方障碍进入“障碍等待”，障碍解除后恢复。
7. 到达 A1，侧向摄像头对准货架，识别货架 AprilTag、OCR 货架号和物品标签。
8. A1 正常扫描后，看板显示 `shelf_scanned` 或正常物品事件。
9. 在 A2 制造缺失、重复、错放或识别证据冲突，看板显示待确认异常。
10. 点击“确认处理”，事件变为已确认。
11. 点击“导出日志”，保存 CSV。

## 三、软件兜底流程

本地启动：

```bash
scripts/run_local.sh
```

打开：

```text
http://127.0.0.1:5050
```

推荐按钮顺序：

1. 开始巡逻
2. 模拟规划路径
3. 模拟扫描 A1 正常
4. 模拟障碍等待
5. 模拟障碍解除
6. 模拟禁区触发
7. 模拟禁区恢复
8. 模拟扫描 A2 异常
9. 模拟识别证据冲突
10. 确认处理
11. 导出日志

也可以点击“软件兜底全流程”，一次生成路径、A1 正常扫描、障碍、禁区、A2 异常、证据冲突、人工确认和任务完成事件。

## 四、硬件失败时怎么讲

- 麦轮运动失败：保留看板和软件兜底，说明 1.1 硬件 runtime 临时不可用，核心地图、路径、货架异常和确认链路可复现。
- AprilTag 识别不稳：展示已录制的标签识别视频，再用模拟扫描按钮把同样事件推到看板。
- OCR/颜色受光照影响：强调它们是复核证据，不覆盖 AprilTag 主身份。
- 超声波误报：用“模拟障碍等待/解除”展示状态机和看板联动。
- RGB/蜂鸣器不可用：展示事件日志和录屏，说明声光告警属于现场提示层。

## 五、网络失败兜底

如果浏览器打不开，使用终端打 API：

```bash
curl -X POST http://127.0.0.1:5050/api/start
curl -X POST http://127.0.0.1:5050/api/demo/path
curl -X POST http://127.0.0.1:5050/api/demo/scan/A1/normal
curl -X POST http://127.0.0.1:5050/api/demo/obstacle
curl -X POST http://127.0.0.1:5050/api/demo/obstacle/clear
curl -X POST http://127.0.0.1:5050/api/demo/forbidden
curl -X POST http://127.0.0.1:5050/api/demo/forbidden/clear
curl -X POST http://127.0.0.1:5050/api/demo/scan/A2/abnormal
curl -X POST http://127.0.0.1:5050/api/demo/evidence-mismatch
curl -X POST http://127.0.0.1:5050/api/confirm
curl http://127.0.0.1:5050/api/export.csv
```

小车端把地址改成：

```text
http://小车IP:5000
```

## 六、答辩 2-3 分钟讲解顺序

| 时间 | 动作 | 讲解点 |
|---|---|---|
| 0:00-0:20 | 打开看板和地图 | 固定场景，不做 SLAM；黑胶带是禁区 |
| 0:20-0:45 | 展示规划路径 | A* 或固定地图规划避开禁区 |
| 0:45-1:05 | 展示障碍等待/解除 | 超声波用于局部安全停车 |
| 1:05-1:35 | 展示 A1 正常扫描 | 货架 AprilTag + OCR，物品多模态证据 |
| 1:35-2:10 | 展示 A2 异常和证据冲突 | 缺失、重复、错放、证据不一致进入人工复核 |
| 2:10-2:40 | 确认处理并导出 CSV | 异常闭环和可追溯日志 |
