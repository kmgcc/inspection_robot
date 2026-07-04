# Yahboom OLED 显示屏接入说明

**状态：** 已接入为可选硬件显示。  
**用途：** 在小车 OLED 上显示运行摘要和 MPU6050 yaw 数值，便于不看网页时现场判断姿态读数。

## 一、官方资料中的操作方法

当前仓库保留的官方示例位于：

```text
docs/RASPBOT-V2_Clean_Docs/19.程序源码汇总/05.Comprehensive_gameplay/6.ir_controlled_miniature_car.ipynb
```

示例中的 OLED 用法是：

```python
sys.path.append("/home/pi/software/oled_yahboom/")
from yahboom_oled import *

oled = Yahboom_OLED(debug=False)
oled.init_oled_process()
oled.clear()
oled.add_line("text", 1)
oled.add_line("text", 2)
oled.add_line("text", 3)
oled.refresh()
```

官方示例也会用下面命令启动 OLED 后台程序：

```bash
python3 /home/pi/software/oled_yahboom/yahboom_oled.py &
```

## 二、项目内接入方式

项目通过 `src/inspection_robot/robot/oled_display.py` 做轻量适配：

1. 只在树莓派上能导入 `yahboom_oled` 时启用。
2. 默认从 `/home/pi/software/oled_yahboom` 加载官方库。
3. OLED 刷新挂在 `RobotRuntime.refresh_motion_sensor()` 上，和网页 `/api/status` 使用同一次 MPU6050 样本。
4. 如果 OLED 库不存在、屏幕初始化失败或 I2C 异常，适配器会静默禁用，不阻塞巡逻和网页。

当前显示内容使用 16 字符定宽短行，避免旧文本残影和错位。内容为：

1. `INSPECT ROBOT`
2. `MPU OK` 或 `MPU ERR`
3. `YAW +xx.x deg`
4. 最近一次 90 度转向方向和误差，例如 `TURN R err -1.2`

## 三、可调环境变量

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `OLED_DISPLAY_ENABLED` | `1` | 设为 `0` 可关闭 OLED 刷新。 |
| `YAHBOOM_OLED_PATH` | `/home/pi/software/oled_yahboom` | 官方 OLED Python 库路径。 |
| `OLED_REFRESH_SECONDS` | `0.75` | OLED 最小刷新间隔，避免频繁写屏影响主循环。 |
| `OLED_LINE_WIDTH` | `16` | 每行定宽字符数，默认适配 4 行小屏。 |

## 四、现场验证

部署并启动机器人模式后：

```bash
curl http://192.168.1.11:5000/api/status
```

确认 `motion_sensor.orientation_deg.yaw` 有数值后，OLED 第三行应显示同一个 yaw。把小车放在地面水平旋转时，网页姿态和 OLED yaw 都应变化；静止时 yaw 应基本稳定。

如果网页有 yaw、OLED 没显示，先检查：

1. `OLED_DISPLAY_ENABLED` 是否为 `0`。
2. `/home/pi/software/oled_yahboom/yahboom_oled.py` 是否存在。
3. 官方 OLED 示例能否单独运行。
4. 官方 App 主程序是否仍在占用 I2C。
