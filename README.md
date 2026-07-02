# AprilTag 物品巡检小车

小团队工程实践项目：基于 RASPBOT-V2 官方镜像，做一个“视觉标签识别、异常上报、网页看板、人工确认”的巡检闭环。

## 文档入口

- [HANDOFF.md](HANDOFF.md)：交接、运行、部署、代码边界。
- [docs/PROJECT_PLAN.md](docs/PROJECT_PLAN.md)：完整全局规划书，含方案依据、功能规划、6 天推进和答辩思路。

## 本地运行

```bash
python3 -m pip install -r requirements.txt
scripts/run_local.sh
```

浏览器打开：

```text
http://127.0.0.1:5050
```
