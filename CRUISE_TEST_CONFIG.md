# 连续巡航测试配置说明

## 当前测试配置（已确认）

### ✅ 已禁用的功能
```bash
LINE_FOLLOW_ENABLED=0          # 循线逻辑完全关闭
# 巡逻自启开关已移除，开机后只启动网页服务
```

### ✅ 已启用的功能
```bash
RUN_MODE=robot                 # 真实硬件模式
SMOOTH_CRUISE_ENABLED=1        # 平滑巡航入口
TIMED_STOP_SCAN_ENABLED=0      # 移动中识别标签并加入处理逻辑；不强制停车扫描
CRUISE_SPEED=22                # 连续巡航速度
CRUISE_TICK_SECONDS=0.03       # 连续巡航控制周期
TIMED_STOP_SCAN_SPEED=15       # 仅在 TIMED_STOP_SCAN_ENABLED=1 时使用
TIMED_STOP_SCAN_DRIVE_SECONDS=0.8
TIMED_STOP_SCAN_SETTLE_SECONDS=0.2
```

## 启动流程

### 1. 部署到小车
```bash
# 默认配置：不自动启动，循线禁用
./scripts/run_on_car.sh
```

### 2. 在网页手动启动巡逻
1. 打开浏览器访问 `http://192.168.1.11:5000`
2. 点击顶部的 **"开始巡逻"** 按钮
3. 小车连续低速巡航；侧向摄像头在后台识别 AprilTag，识别到货架/物品标签后直接加入巡检处理逻辑，不需要先停车等画面稳定

### 3. 手动停止
- 点击顶部的 **"立即停止"** 红色按钮（优先级最高）
- 或调用 API: `curl -X POST http://192.168.1.11:5000/api/stop`

## 验证循线已禁用

### 方法 1：检查配置
```bash
ssh pi@192.168.1.11
cd /home/pi/temp/inspection_robot
grep LINE_FOLLOW_ENABLED app.log
# 应显示：LINE_FOLLOW_ENABLED=0
```

### 方法 2：检查运行时行为
```bash
# 启动巡逻后，查看日志中是否有循线相关记录
curl http://192.168.1.11:5000/api/status | jq '.events[] | select(.type == "motion_debug") | .evidence.stage' | grep -i line

# 如果循线已禁用，应该无任何输出
# 如果看到 "line_follow_step" 或 "line_follow_auto_enter"，说明循线未正确关闭
```

### 方法 3：观察实际行为
当小车任意一路传感器压到黑胶带时：
- ✅ **正确行为（边界优先）**: 小车立即停车，短退后按当前阶段转向或绕行
- ❌ **错误行为（漏检）**: 小车继续跨过黑胶带或先处理 AprilTag/扫描

## 当前巡逻模式

### 边界检测逻辑（边界优先）
```
触发条件: 任意一路红外检测到黑色，例如 (0,1,1,1)
动作: 停车 → 短退 → 按当前阶段右转 90° 或禁区绕行 → 继续巡航
用途: 列端转向、行走范围边界、矩形禁区边界
```

### 循线逻辑（已禁用）
```
触发条件: 部分红外检测到黑色，如 (0,1,1,1) 或 (1,0,0,1)
动作: 生产巡航中不作为循线信号；边界优先逻辑会先锁存停车
原逻辑: line-follow 测试模式下可显式提高边界阈值后再 strafe_left/right 横向纠偏
```

### 航向保持（启用）
```
触发条件: 陀螺仪检测到偏航 > 1.2°
动作: 前进时对左右轮速做差速纠偏，停车扫描后只做零漂重标定，不重置直行目标角
用途: 保持每一段前进都对齐启动/转弯后的直线方向
```

## 预期行为对照表

| 场景 | 边界优先生产模式 | 显式 line-follow 测试模式 |
|------|---------------------|----------------------|
| 完整黑胶带 (0,0,0,0) | 右转 90° | 右转 90° |
| 左侧偏黑 (0,1,1,1) | 锁存停车并执行边界动作 | 左移纠偏 strafe_left |
| 右侧偏黑 (1,1,1,0) | 锁存停车并执行边界动作 | 右移纠偏 strafe_right |
| 居中白线 (1,0,0,1) | 锁存停车并执行边界动作 | 直行跟随 |
| 陀螺仪偏航 6° | 前进差速纠偏 | 前进差速纠偏（叠加循线） |

## 测试重点

### ✅ 应该测试的内容
1. 连续巡航时移动识别可触发货架/物品事件，且不打断电机前进
2. 航向纠偏收敛（偏离后 2-3 秒恢复）
3. 边界转向精度（90° ± 10°）
4. 手动接管响应（转向命令完整执行）
5. 急停响应速度（< 0.5 秒）

### ❌ 不应该出现的现象
1. 压到任意黑胶带后继续跨过去
2. 原地转圈或持续弧线（R3 回归）
3. 手动转向无响应（R1 回归）
4. 停不下来或延迟停止
5. 部分车轮停止而其他轮子继续转动

## 紧急回滚

如果测试中发现严重问题，立即执行：

```bash
# 方案 1：关闭匀速巡航，回退到经典短步巡逻
SMOOTH_CRUISE_ENABLED=0 ./scripts/run_on_car.sh

# 方案 1b：如果现场画面抖动太大，可临时打开定时停车扫描
TIMED_STOP_SCAN_ENABLED=1 ./scripts/run_on_car.sh

# 方案 2：完全停止巡逻
curl -X POST http://192.168.1.11:5000/api/stop
```

## 配置文件位置

- 启动脚本: `scripts/run_on_car.sh`
- 应用入口: `app.py`
- 运行时配置: `src/inspection_robot/runtime.py`
- 车上默认值: `TIMED_STOP_SCAN_ENABLED=0`, `CRUISE_SPEED=22`, `TIMED_STOP_SCAN_SPEED=15`, `TIMED_STOP_SCAN_DRIVE_SECONDS=0.8`, `BOUNDARY_MIN_BLACK_SENSORS=1`, `MOTION_GUARD_POLL_SECONDS=0.005`, `LINE_FOLLOW_ENABLED=0`

## 联系信息

如有疑问或发现异常，请查看：
- 审查报告: `代码审查完成后的输出`
- 测试清单: 审查报告 D 节（车上验证清单）
- 日志位置: `/home/pi/temp/inspection_robot/app.log`
