# 当前代码整理状态

## 已处理的问题

原来的 `app.py` 能跑，但所有 HTML、CSS、JS、状态逻辑、API 都挤在一个文件里，队友接手后很难判断应该改哪里。现在已经拆成：

- `state.py`：业务状态和事件处理；
- `web.py`：Flask 路由；
- `dashboard.html`：页面结构；
- `styles.css`：页面样式；
- `dashboard.js`：轮询接口和按钮动作；
- `tag_map.json`：标签配置。

原来标签配置写死在 Python 里。现在移到 `config/tag_map.json`，后续换物品、换分区时不用改代码。

原来没有仓库忽略规则。现在 `.gitignore` 已排除缓存、日志、数据文件、视频、镜像、压缩包、PPT、Word、PDF 等容易把 GitHub 撑爆的文件。

原来没有部署脚本。现在 `scripts/deploy_to_car.sh` 可以把仓库同步到小车 `/home/pi/temp/inspection_robot`。

## 仍然存在的限制

当前标签来源还是模拟接口：

```text
POST /api/simulate/tag/<tag_id>
```

还没有接入官方 ROS2 AprilTag 话题。下一步要做的是新增 ROS2 订阅模块，把 `/single_apriltag_id` 收到的 ID 转给 `InspectionStore.handle_tag()`。

当前事件只保存在内存中，重启程序后会丢失。答辩前如果需要历史记录，可以加 `storage.py`，把事件写到 `data/events.jsonl` 或 `data/events.csv`。

当前声光告警还没有真正发布 ROS2 话题。异常和确认状态已经有了，后续只要在异常产生、确认完成时补发布 `/buzzer` 和 `/rgblight`。

## 接手时不要做的事

不要把官方 40GB 资料包提交到 GitHub。

不要为了“高级”去加 SLAM、自由路径规划、训练模型。当前题目的亮点是巡检闭环，不是复杂导航。

不要直接修改官方 Docker 工作区里的源码。先在本仓库完成，再部署到 `/home/pi/temp/inspection_robot`。
