# MPU6050 与底盘直行测试记录

测试日期：2026-07-05  
测试对象：RASPBOT V2 小车，MPU6050，厂商 `McLumk_Wheel_Sports` 底盘库  
测试目的：确认 yaw 轴、零漂、90 度转向积分是否可靠，并给后续直行纠偏提供依据。

## 当前结论

1. MPU6050 的水平旋转轴是 `z` 轴。
2. 左转时 `z` 轴角速度为正，右转时为负。
3. 当前界面里的 YAW 是陀螺仪积分出来的相对角度，不是磁罗盘绝对朝向。
4. 90 度转向积分基本可靠，可以用于闭环转向。
5. patched stop 后，`speed=30` 短直线测试偏航约 `+2.1 deg / 0.8 s`，可作为后续直行纠偏基线。
6. 已把本项目 `motion.stop()` 改成对 4 个电机重复写零，后续测试均以该版本为准。

## 环境状态

测试前已停止并禁用项目自启动服务：

```text
inspection-robot.service: disabled / inactive
```

确认无 `app.py` 进程干扰。后续如果要重新运行看板，需要手动启动或重新启用服务。

## 零漂数据

多次静止采样结果接近：

```text
x: -2.8 dps
y: +1.0 dps
z: -1.1 dps
```

静止噪声跨度大约：

```text
x: 0.8 ~ 1.3 dps
y: 0.4 ~ 0.6 dps
z: 0.4 ~ 0.6 dps
```

后续必须先做静止 bias 校准，再积分 yaw。不能直接用 raw gyro。

## 90 度转向测试

### speed=30，左转

```text
目标停止角度: 88 deg
停止时间: 0.718 s
z 轴积分: 88.36 deg
x/y 串扰: x=0.807 deg, y=-0.958 deg
停车后 z 轴余转: 0.004 deg
```

结论：`speed=30` 可用于闭环 90 度转向；停车后几乎没有余转。

## 直线测试

```text
speed=30
forward duration: 0.8 s
动作期间 z 轴积分: +2.093 deg
停车后 z 轴余转: +0.015 deg
总 yaw: +2.108 deg
```

结论：当前可按 `speed=30`、`0.8s` 约 `+2.1 deg` 的量级做轻微纠偏。

## 单轮与组合轮测试

已架空测试：

```text
motor 0 正/反转：正常
motor 1 正/反转：正常
motor 2 正/反转：正常
motor 3 正/反转：正常
四轮同时 forward speed=30：正常
四轮同时 forward speed=50：正常
```

结论：电机编号和基本方向没有明显错误。

## 已做代码修复

文件：

```text
inspection_robot/src/inspection_robot/robot/motion.py
```

修改点：

1. 新增 `ROBOT_STOP_REPEAT`，默认 `8`。
2. 新增 `ROBOT_STOP_REPEAT_GAP_SECONDS`，默认 `0.08`。
3. `motion.stop()` 优先直接访问厂商库的 `bot`，对电机 `0/1/2/3` 重复执行：

```text
Ctrl_Muto(motor_id, 0)
Ctrl_Car(motor_id, 0, 0)
```

4. 如果拿不到 `bot`，再回退到厂商 `stop_robot()` / `stop()`。

该修复已同步到小车：

```text
/home/pi/temp/inspection_robot/src/inspection_robot/robot/motion.py
```

## 后续纠偏建议

### 1. 转向闭环

继续使用 MPU6050 z 轴积分：

```text
yaw_axis = z
left turn = positive z
right turn = negative z
```

90 度转向建议：

```text
speed = 30
target stop angle = 88 deg
```

因为停车和惯性会带来少量过冲，停止阈值不要直接设 90。

### 2. 直行巡航

先使用：

```text
speed = 30
```

不要再用过低速度作为主巡航速度。低速更容易受死区、地面摩擦和单轮力矩差影响。

### 3. 直行纠偏方式

不要用“走一段，停下，原地转一下”的粗纠偏作为主方案。更建议在持续前进时做小幅左右轮差补偿：

```text
heading_error = integrated_yaw_deg
correction = Kp * heading_error + Kd * yaw_rate
```

当前可信样本只有 `0.8s -> +2.1 deg`，建议先用很小的补偿，避免过度摆动。

## 后续测试清单

1. 确认 patched stop 后，连续直行 3 次，每次 `speed=30, 0.8s`，记录 yaw 是否稳定在同一方向。
2. 如果 yaw 稳定偏正，试很小的反向补偿。
3. 如果 yaw 每次方向不同，优先检查地面、轮胎接触、供电和 I2C 丢包，而不是加大纠偏。
4. 重新启用看板前，确认 `inspection-robot.service` 是否需要恢复自启动。
