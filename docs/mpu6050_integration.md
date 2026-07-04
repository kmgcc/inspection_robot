# MPU6050 加速度传感器接入文档

**状态：** 新增硬件接入说明。  
**前置依据：** `docs/REAL_REQUIREMENTS.md`、`docs/hardware_reference/README.md`  
**目标：** 将 MPU6050 模块接入 Raspberry Pi 5，通过 I2C 读取加速度和陀螺仪数据，用作小车运动状态与转向标定的辅助反馈。

## 一、接入边界

MPU6050 在本项目中只做辅助传感器：

1. 记录小车加速度和角速度。
2. 辅助判断原地 90 度转向是否接近目标角度。
3. 辅助发现明显碰撞、剧烈晃动或异常姿态变化。
4. 为后续运动标定和日志分析提供数据。

它不做以下事情：

1. 不替代四路黑胶带列端触发。
2. 不替代超声波避障。
3. 不替代 AprilTag、OCR 或视觉识别。
4. 不作为 SLAM、自由导航或地图构建的主输入。
5. 不让大模型根据 MPU6050 数据直接控制底盘。

第一阶段只要求完成稳定读数、静止偏置估计和一次 90 度转向对比记录。

## 二、本地资料位置

已经整理好的资料在：

```text
docs/hardware_reference/
```

优先看：

1. `docs/hardware_reference/mpu6050_module/README.md`
2. `docs/hardware_reference/raspberry_pi_5/raspberry-pi-5-gpio-i2c-quick-reference.md`
3. `docs/hardware_reference/raspberry_pi_5/raspberry-pi-5-gpio-software-stack.md`
4. `docs/hardware_reference/mpu6050_module/RM-MPU-6000A.pdf`

当前模块关键信息：

1. 模块排针顺序为 `VCC`、`GND`、`SCL`、`SDA`、`XDA`、`XCL`、`AD0`、`INT`。
2. 模块带 3.3V LDO。
3. `SDA`、`SCL` 已通过 4.7k 电阻上拉到模块 3.3V。
4. `AD0` 默认下拉，悬空或接 GND 时 I2C 地址为 `0x68`，接高电平时为 `0x69`。
5. 第一阶段使用轮询读取，不接 `INT`。

## 三、接线方案

第一版接线保持保守，模块使用树莓派 3.3V 供电：

| MPU6050 | Raspberry Pi 5 | 说明 |
|---|---|---|
| `VCC` | 3.3V，物理引脚 1 或 17 | 首次测试不要接 5V |
| `GND` | GND，例如物理引脚 6 | 必须共地 |
| `SCL` | GPIO3 / SCL1，物理引脚 5 | I2C 时钟 |
| `SDA` | GPIO2 / SDA1，物理引脚 3 | I2C 数据 |
| `AD0` | 悬空或接 GND | 默认地址 `0x68` |
| `INT` | 暂不连接 | 第一阶段轮询读取 |
| `XDA` / `XCL` | 暂不连接 | 不接外部从传感器 |

注意：

1. 树莓派 GPIO 是 3.3V 逻辑，不要把 5V 信号接入 GPIO、SDA、SCL 或 INT。
2. 模块和树莓派 I2C 已有上拉，第一轮测试不要额外加上拉电阻。
3. 电机运动时电源波动可能影响 I2C 稳定性，真车测试前确认 Pi 5 使用稳定供电。
4. 官方 Yahboom App 主程序必须先停止，避免占用 I2C 或底盘资源。

## 四、系统准备

在树莓派上启用 I2C：

```bash
sudo raspi-config nonint do_i2c 0
sudo reboot
```

安装基础工具：

```bash
sudo apt-get update
sudo apt-get install -y i2c-tools python3-venv libgpiod-dev python3-libgpiod
```

如果使用项目虚拟环境：

```bash
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
pip install --upgrade pip
pip install adafruit-blinka adafruit-circuitpython-mpu6050 smbus2
```

推荐第一版使用 `adafruit-circuitpython-mpu6050` + Blinka，原因是接入快、代码短、便于现场先确认硬件读数。后续如果需要减少依赖或精确控制寄存器，再改为 `smbus2` 直接读取寄存器。

## 五、I2C 冒烟验证

确认 I2C 设备节点：

```bash
ls /dev/i2c*
```

扫描默认 I2C 总线：

```bash
sudo i2cdetect -y 1
```

预期：

1. `AD0` 悬空或接 GND 时看到 `68`。
2. `AD0` 接高电平时看到 `69`。
3. 如果看不到地址，先检查供电、共地、SDA/SCL 是否接反、I2C 是否启用。
4. 如果地址偶尔消失，先降低电机活动干扰，只在静止状态下复测。

## 六、最小读数脚本

第一版可以用下面脚本确认三类数据都能读取：

```python
import time

import adafruit_mpu6050
import board
import busio

i2c = busio.I2C(board.SCL, board.SDA)
mpu = adafruit_mpu6050.MPU6050(i2c)

while True:
    print("acceleration:", mpu.acceleration)
    print("gyro:", mpu.gyro)
    print("temperature:", mpu.temperature)
    print("---")
    time.sleep(0.2)
```

验证要求：

1. 静止放置时读数连续输出，不报 I2C 错误。
2. 轻微晃动模块时，加速度或角速度有明显变化。
3. 静止 5 秒，记录 Z 轴角速度平均值，作为第一版转向积分的偏置。

## 七、runtime 接入建议

建议新增或扩展 `src/inspection_robot/robot/sensors.py`，提供窄接口：

```python
def read_motion_sample() -> dict | None: ...
def estimate_gyro_bias(seconds: float = 5.0) -> dict | None: ...
```

返回字段建议：

```json
{
  "accel_mps2": {"x": 0.0, "y": 0.0, "z": 0.0},
  "gyro_rps": {"x": 0.0, "y": 0.0, "z": 0.0},
  "temperature_c": 0.0,
  "source": "mpu6050",
  "ok": true
}
```

接入顺序：

1. 启动时检测 MPU6050 是否可读。
2. 可读则在硬件状态中标记 `mpu6050=true`。
3. 不可读时标记为不可用，但不能阻止网页、巡逻、避障和急停。
4. 90 度转向标定时，先静止估计 `gyro_z` 偏置，再对转向过程积分，记录估计角度与人工观察结果。
5. 后续再用加速度突变或角速度异常作为碰撞/剧烈晃动事件证据。

零漂与闭环转向策略：

1. 网页实时读数显示 `gyro_bias_dps`，展示当前陀螺仪零漂估计值；展示用的角速度为扣除 bias 后的值。
2. 每次 MPU6050 闭环 90 度转向前，都重新静止采样估计 Z 轴零漂，避免直接积分原始角速度。
3. 转向先执行一次标定时长脉冲，再按积分角度误差决定继续同向补转或反向回调。
4. 多次纠偏仍未进入容差时，保持停车并记录不收敛状态，不再盲目补一个完整开环 90 度。

## 八、验收清单

1. 文档资料能在 `docs/hardware_reference/` 中找到。
2. MPU6050 使用 3.3V、GND、SCL、SDA 正确接入。
3. `sudo i2cdetect -y 1` 能看到 `0x68` 或 `0x69`。
4. Python 最小脚本能连续读出 acceleration、gyro、temperature。
5. 静止状态下能估计陀螺仪偏置。
6. 手动转动车体或模块时，角速度读数有明显变化。
7. 一次原地 90 度转向后，日志中能看到估计角度和实际观察备注。
8. 传感器不可用时，系统降级运行，不影响急停、网页和基本巡逻。

## 九、后续任务

1. 在硬件冒烟矩阵中增加 MPU6050 验证行。
2. 新增 `scripts/test_mpu6050_on_car.py`，用于真车现场单独测试。
3. 在 `/api/status` 或硬件状态中显示 MPU6050 可用性。
4. 用 MPU6050 辅助标定 `rotate_left_slow` 和 `rotate_right_slow` 的 90 度时长。
5. 记录碰撞/剧烈晃动事件，但不让它直接覆盖主巡逻状态机。
