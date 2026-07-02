# 项目结构说明

这个仓库只放项目代码和轻量文档。官方资料包、镜像、视频、虚拟机不放进 GitHub。

```text
inspection_robot/
├── app.py
├── config/
│   └── tag_map.json
├── data/
├── docs/
├── scripts/
├── src/
│   └── inspection_robot/
│       ├── config.py
│       ├── state.py
│       ├── web.py
│       ├── templates/
│       │   └── dashboard.html
│       └── static/
│           ├── dashboard.js
│           └── styles.css
└── requirements.txt
```

## 各层职责

`app.py` 是兼容入口。放在仓库根目录，是为了在小车上直接 `python3 app.py`，不用让队友记复杂命令。

`config/tag_map.json` 是标签配置。AprilTag 只能识别 ID，物品名称、分区、期望分区都由这份配置映射出来。答辩时也要这样解释。

`src/inspection_robot/state.py` 负责业务状态。当前的开始巡检、标签处理、异常事件、人工确认都在这里。

`src/inspection_robot/web.py` 负责 Flask API。后续接 ROS2 时，尽量不要把 ROS2 逻辑塞进页面代码。

`templates/` 和 `static/` 是前端页面。现在页面不追求花哨，只保证状态、异常、确认动作清楚。

`scripts/` 是给小车部署用的脚本。队友换电脑后，优先用这些脚本，不要手动复制散文件。

## 后续扩展位置

真实 AprilTag 接入建议新增：

```text
src/inspection_robot/ros_bridge.py
```

小车运动控制建议新增：

```text
src/inspection_robot/robot_control.py
```

数据落盘建议新增：

```text
src/inspection_robot/storage.py
```

不要把这些逻辑重新塞回 `app.py`。`app.py` 应该一直保持很薄。
