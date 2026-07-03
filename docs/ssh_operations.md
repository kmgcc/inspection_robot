# 小车 SSH 连接与运维手册

本文用于项目成员在电脑上连接 RASPBOT V2 小车、推送项目代码、运行网页看板、进入 ROS2 环境并对小车进行基础操作测试。所有命令默认在项目根目录 `inspection_robot/` 或小车终端中执行。

## 一、账号与网络信息

小车官方镜像默认账号如下：

| 项目 | 值 |
|---|---|
| SSH/VNC 用户名 | `pi` |
| SSH/VNC 密码 | `yahboom` |
| 默认热点名 | `Raspbot`，现场可改为本组名称 |
| 默认热点密码 | `12345678` |
| 默认热点 IP | `192.168.1.11` |
| 本项目默认 SSH 地址 | `pi@192.168.1.11` |
| 本项目默认部署目录 | `/home/pi/temp/inspection_robot` |

如果小车热点名已经改成组名，先连接本组热点，再使用同一个默认 IP 登录。若 IP 发生变化，在小车桌面或 SSH 中执行：

```bash
hostname -I
```

返回结果中形如 `192.168.x.x` 的地址即为当前小车地址。

## 二、SSH 登录方式

电脑连接小车热点后，打开终端：

```bash
ssh pi@192.168.1.11
```

输入密码：

```text
yahboom
```

登录成功后应看到类似提示符：

```text
pi@yahboom:~ $
```

建议先确认当前连到的是自己的小车：

```bash
hostname
hostname -I
pwd
ls /home/pi
```

如果现场有多台车，不能只靠 `192.168.1.11` 判断。连接后应通过蜂鸣器、RGB 灯或物理观察确认当前 SSH 会话对应本组小车。

## 三、VNC 图形登录

需要看摄像头窗口、OpenCV 图像窗口或桌面网络设置时，用 VNC。

1. 电脑连接小车热点。
2. 打开 `RealVNC Viewer`。
3. 地址输入：

```text
192.168.1.11
```

4. 登录：

```text
用户名：pi
密码：yahboom
```

VNC 主要用于图形窗口测试。普通代码推送、启动服务、查看日志，用 SSH 更快。

## 四、开发前清理官方大程序

官方镜像开机后可能自动启动 APP 控制大程序。开发前必须先关闭，否则可能占用摄像头、底盘或 I2C 设备。

SSH 登录小车后执行：

```bash
sh /home/pi/project_demo/raspbot/killprocess.sh
```

若输出类似：

```text
Process xxxx has been terminated.
```

说明关闭成功。若提示没有进程，也可以继续后续操作。

如需永久关闭官方大程序自启动，确认不再使用官方 APP 后再执行：

```bash
sudo rm -rf /home/pi/.config/autostart/start_raspbot.desktop
```

## 五、推送项目代码到小车

本项目已提供部署脚本。电脑在项目根目录执行：

```bash
scripts/deploy_to_car.sh
```

脚本默认把当前仓库同步到：

```text
pi@192.168.1.11:/home/pi/temp/inspection_robot
```

如果小车 IP 不是 `192.168.1.11`，使用环境变量覆盖：

```bash
CAR_HOST=pi@新的IP scripts/deploy_to_car.sh
```

如果要部署到其他目录：

```bash
CAR_DIR=/home/pi/temp/inspection_robot_test scripts/deploy_to_car.sh
```

部署脚本使用 `rsync --delete`，会让小车部署目录与本地仓库保持一致，但会排除 `.git/`、`__pycache__/`、`*.pyc`、`*.log`、`data/*.json` 和 `data/*.csv`。因此运行日志和导出的数据不会被部署覆盖。

## 六、在小车上运行网页看板

推送代码后，可在电脑项目根目录执行：

```bash
scripts/run_on_car.sh
```

脚本会通过 SSH 在小车上执行：

```bash
cd /home/pi/temp/inspection_robot
nohup python3 app.py > app.log 2>&1 &
```

启动成功后，电脑浏览器访问：

```text
http://192.168.1.11:5000
```

如果 IP 变化：

```text
http://新的IP:5000
```

查看小车上的运行日志：

```bash
ssh pi@192.168.1.11
cd /home/pi/temp/inspection_robot
tail -f app.log
```

停止网页看板：

```bash
scripts/stop_on_car.sh
```

或手动在小车上释放 5000 端口：

```bash
fuser -k 5000/tcp
```

## 七、通过蓝牙音箱播放测试音频

项目内置测试音频：

```text
src/inspection_robot/static/audio/youdowhatreversed.wav
```

当前巡检固定音效：

```text
src/inspection_robot/static/audio/obstacle.wav   # 障碍物或非预期禁区
src/inspection_robot/static/audio/first.wav      # 检测到货架
src/inspection_robot/static/audio/following.wav  # 检测到货架上的物品，每个物品一次
```

这些文件是从 `/Users/kmg/Desktop/sounds/*.MP4` 提取音轨并转换得到的 `PCM 16-bit WAV`、`44.1 kHz`、单声道文件，优先保证 `aplay`、`paplay`、`pw-play`、`ffplay` 等树莓派端播放器稳定播放。

目标效果是让树莓派本机播放声音，而不是让电脑浏览器播放。蓝牙音箱需要先连接到树莓派，并在树莓派桌面中设为默认输出设备。

推荐流程：

1. 使用 VNC 登录小车桌面。
2. 打开桌面右上角蓝牙菜单，连接蓝牙音箱。
3. 在音量或声音设置中把蓝牙音箱设为默认输出。
4. 启动网页看板后，打开 `http://192.168.1.11:5000`。
5. 点击看板上的“播放音频”按钮。

也可以从电脑终端手动触发小车播放：

```bash
scripts/play_audio_on_car.sh
```

或者 SSH 到小车后，在部署目录中执行：

```bash
cd /home/pi/temp/inspection_robot
PYTHONPATH=src python3 -m inspection_robot.audio
```

若只是想确认音频文件内容，可以点击网页上的“浏览器试听”。注意这会从当前浏览器所在设备出声，不一定是小车上的蓝牙音箱。

如果按钮没有声音，先在 VNC 桌面终端里测试：

```bash
paplay /home/pi/temp/inspection_robot/src/inspection_robot/static/audio/youdowhatreversed.wav
paplay /home/pi/temp/inspection_robot/src/inspection_robot/static/audio/obstacle.wav
paplay /home/pi/temp/inspection_robot/src/inspection_robot/static/audio/first.wav
paplay /home/pi/temp/inspection_robot/src/inspection_robot/static/audio/following.wav
```

如果 `paplay` 不存在，再试：

```bash
aplay /home/pi/temp/inspection_robot/src/inspection_robot/static/audio/youdowhatreversed.wav
aplay /home/pi/temp/inspection_robot/src/inspection_robot/static/audio/obstacle.wav
aplay /home/pi/temp/inspection_robot/src/inspection_robot/static/audio/first.wav
aplay /home/pi/temp/inspection_robot/src/inspection_robot/static/audio/following.wav
```

能在 VNC 终端出声后，网页按钮也应能正常触发同一份音频。

## 八、编译与基础检查

本项目主体是 Python，不需要传统 C/C++ 编译。每次推送前，建议在电脑本地做语法检查和测试：

```bash
python3 -m py_compile app.py src/inspection_robot/*.py
python3 -m unittest discover -s tests -v
```

如果电脑上 `python3` 不可用，Windows 可使用：

```bash
py -3 -m py_compile app.py src/inspection_robot/*.py
py -3 -m unittest discover -s tests -v
```

推送到小车后，也可以在小车上检查：

```bash
ssh pi@192.168.1.11
cd /home/pi/temp/inspection_robot
python3 -m py_compile app.py src/inspection_robot/*.py
python3 -m unittest discover -s tests -v
```

若后续修改官方 ROS2 工作空间中的包，才需要进入 Docker 后使用 `colcon build`。当前网页看板和业务代码不要求 `colcon build`。

## 九、进入 ROS2 Docker 环境

小车官方 ROS2 环境在 Docker 中。首次 SSH 登录后执行：

```bash
cd ~
./docker_ros2.sh
```

如果成功，提示符会变为：

```text
root@yahboom:/#
```

SSH 下可能出现：

```text
xhost: unable to open display ""
```

这是因为 SSH 没有图形显示环境。只做终端话题测试时可以忽略；若要显示 OpenCV 窗口、RViz 或 AprilTag 图像窗口，使用 VNC 桌面终端运行。

进入 Docker 后确认 ROS2 工作空间：

```bash
ls /root/yahboomcar_ws/src
```

启动底盘驱动：

```bash
ros2 launch yahboomcar_bringup bringup.launch.py
```

看到：

```text
Successfully started the chassis drive...
```

说明底盘驱动已启动。这个终端保持运行，不要关闭。

## 十、第二终端进入同一容器

底盘驱动启动后，需要再开一个 SSH 终端操作话题。

电脑新开终端：

```bash
ssh pi@192.168.1.11
```

查看正在运行的容器：

```bash
docker ps
```

第一列 `CONTAINER ID` 就是容器 ID。例如：

```text
CONTAINER ID   IMAGE                              COMMAND
abc123def456   yahboomtechnology/ros-humble:0.1.0 "/bin/bash"
```

进入该容器：

```bash
docker exec -it abc123def456 /bin/bash
```

也可以只写前几位：

```bash
docker exec -it abc123 /bin/bash
```

若不想手动复制 ID，可用：

```bash
docker exec -it $(docker ps -q --filter ancestor=yahboomtechnology/ros-humble:0.1.0 | head -n 1) /bin/bash
```

## 十一、ROS2 话题操作测试

进入第二个 Docker 终端后，先查看话题：

```bash
ros2 topic list
```

正常应看到：

```text
/buzzer
/cmd_vel
/line_sensor
/rgblight
/servo
/ultrasonic
```

### 1. 蜂鸣器

打开：

```bash
ros2 topic pub -1 /buzzer std_msgs/msg/Bool "data: 1"
```

关闭：

```bash
ros2 topic pub -1 /buzzer std_msgs/msg/Bool "data: 0"
```

### 2. RGB 灯

红灯：

```bash
ros2 topic pub -1 /rgblight std_msgs/msg/Int32MultiArray "data: [255, 0, 0]"
```

绿灯：

```bash
ros2 topic pub -1 /rgblight std_msgs/msg/Int32MultiArray "data: [0, 255, 0]"
```

蓝灯：

```bash
ros2 topic pub -1 /rgblight std_msgs/msg/Int32MultiArray "data: [0, 0, 255]"
```

关灯：

```bash
ros2 topic pub -1 /rgblight std_msgs/msg/Int32MultiArray "data: [0, 0, 0]"
```

### 3. 超声波

```bash
ros2 topic echo /ultrasonic
```

把手放到超声波前方，距离值应变化。停止显示按 `Ctrl+C`。

### 4. 四路巡线传感器

```bash
ros2 topic echo /line_sensor
```

将车底传感器对准黑线和白底，数据应变化。停止显示按 `Ctrl+C`。

### 5. 云台舵机

居中：

```bash
ros2 topic pub -1 /servo yahboomcar_msgs/msg/ServoControl "{servo_s1: 90, servo_s2: 25}"
```

左转一点：

```bash
ros2 topic pub -1 /servo yahboomcar_msgs/msg/ServoControl "{servo_s1: 60, servo_s2: 25}"
```

右转一点：

```bash
ros2 topic pub -1 /servo yahboomcar_msgs/msg/ServoControl "{servo_s1: 120, servo_s2: 25}"
```

### 6. 电机低速测试

测试电机前必须架空轮子，避免小车冲出桌面。

前进：

```bash
ros2 topic pub -1 /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.08, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}"
```

停车：

```bash
ros2 topic pub -1 /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}"
```

左转：

```bash
ros2 topic pub -1 /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.5}}"
```

右转：

```bash
ros2 topic pub -1 /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: -0.5}}"
```

每次运动测试后都应执行停车命令。

## 十二、AprilTag 识别测试

AprilTag 官方程序会打开图像窗口，建议在 VNC 桌面终端中运行。

进入 Docker 后执行：

```bash
ros2 run yahboomcar_apriltag apriltag_identify
```

将 TAG36H11 标签方块放到摄像头前，窗口中应框出标签并显示 ID。

如果只在 SSH 中运行，可能出现图形窗口相关错误；这不一定表示识别库不可用，而是 SSH 没有显示环境。

## 十三、常见问题

### 1. SSH 能连，但网页打不开

先确认服务是否启动：

```bash
ssh pi@192.168.1.11
cd /home/pi/temp/inspection_robot
tail -n 80 app.log
```

再确认端口：

```bash
ss -lntp | grep 5000
```

必要时重启：

```bash
fuser -k 5000/tcp 2>/dev/null || true
cd /home/pi/temp/inspection_robot
nohup python3 app.py > app.log 2>&1 &
```

### 2. 连到了别人的车

现场多台车默认热点名和 IP 可能相同。连接后用蜂鸣器或 RGB 灯确认物理车辆。确认后建议把本组小车热点名改成 `Raspbot-TeamXX`。

### 3. `docker ps` 没有容器

说明 ROS2 容器尚未启动。执行：

```bash
cd ~
./docker_ros2.sh
```

### 4. `ros2 topic list` 看不到底盘话题

通常是底盘驱动没启动。进入 Docker 后执行：

```bash
ros2 launch yahboomcar_bringup bringup.launch.py
```

保持该终端运行，再开第二终端测试话题。

### 5. 电机不动或传感器没数据

先关闭官方大程序：

```bash
sh /home/pi/project_demo/raspbot/killprocess.sh
```

再重启底盘驱动。如果仍不正常，检查电池、电源开关、扩展板连接和 Docker 设备挂载。

### 6. OpenCV 或 AprilTag 窗口打不开

SSH 没有图形显示环境。使用 VNC 登录小车桌面，在桌面终端中进入 Docker 并运行视觉程序。

## 十四、推荐日常流程

每次开发建议按以下顺序执行：

1. 电脑连接本组小车热点。
2. SSH 登录小车，确认 IP 和主机。
3. 关闭官方大程序。
4. 本地运行测试。
5. 使用 `scripts/deploy_to_car.sh` 推送代码。
6. 使用 `scripts/run_on_car.sh` 启动网页看板。
7. 浏览器访问 `http://192.168.1.11:5000`。
8. 需要真实硬件时，进入 ROS2 Docker，启动底盘驱动并测试话题。
9. 结束后停止网页看板，必要时关闭 Docker 或重启小车。

这套流程的目标不是把所有操作自动化，而是让每个人都知道自己在哪个环境里：电脑本地、小车宿主机，还是 ROS2 Docker 容器。环境分清楚，调试会少走很多弯路。
