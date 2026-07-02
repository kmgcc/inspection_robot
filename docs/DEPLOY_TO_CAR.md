# 小车部署说明

## 1. 连接小车

电脑连小车热点后执行：

```bash
ssh pi@192.168.1.11
```

默认密码：

```text
yahboom
```

如果 IP 不是 `192.168.1.11`，在小车终端执行：

```bash
hostname -I
```

## 2. 开发前关闭官方大程序

官方镜像开机会启动 APP 大程序，开发前先关闭：

```bash
sh /home/pi/project_demo/raspbot/killprocess.sh
```

## 3. 同步代码到小车

在电脑仓库根目录执行：

```bash
scripts/deploy_to_car.sh
```

如果小车 IP 变了：

```bash
CAR_HOST=pi@新的IP scripts/deploy_to_car.sh
```

## 4. 启动网页看板

```bash
scripts/run_on_car.sh
```

浏览器访问：

```text
http://192.168.1.11:5000
```

## 5. 停止网页看板

```bash
scripts/stop_on_car.sh
```

## 6. 手动运行方式

如果脚本不可用，可以 SSH 到小车后执行：

```bash
cd /home/pi/temp/inspection_robot
python3 app.py
```
