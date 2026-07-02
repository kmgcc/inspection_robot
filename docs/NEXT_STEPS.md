# 下一步开发任务

## 第一阶段：接入真实 AprilTag

目标：把网页里的“模拟正常标签 / 模拟异常标签”替换成官方 AprilTag 识别结果。

操作顺序：

1. 在小车 VNC 或屏幕终端进入 Docker：

```bash
cd ~
./docker_ros2.sh
```

2. 启动官方 AprilTag 识别：

```bash
ros2 run yahboomcar_apriltag apriltag_identify
```

3. 另开一个 Docker 终端查看话题：

```bash
ros2 topic list
ros2 topic echo /single_apriltag_id
```

4. 在本项目里新增 ROS2 订阅模块，收到 ID 后调用现有的 `store.handle_tag(tag_id)`。

## 第二阶段：接入声光告警

异常时发布：

```bash
ros2 topic pub -1 /buzzer std_msgs/msg/Bool "data: 1"
ros2 topic pub -1 /rgblight std_msgs/msg/Int32MultiArray "data: [255, 0, 0]"
```

人工确认后发布：

```bash
ros2 topic pub -1 /buzzer std_msgs/msg/Bool "data: 0"
ros2 topic pub -1 /rgblight std_msgs/msg/Int32MultiArray "data: [0, 255, 0]"
```

## 第三阶段：接入巡线和超声波

先只做低速巡线、遇障停车、障碍消失继续。不要一开始做自由绕障。

需要关注的话题：

```text
/line_sensor
/ultrasonic
/cmd_vel
```

## 答辩前必须稳定的闭环

```text
打开网页 -> 开始巡检 -> 识别标签 -> 异常上报 -> 声光告警 -> 人工确认 -> 事件关闭
```
