# Raspberry Pi 5 GPIO / I2C Software Stack

Collected for the inspection robot MPU6050 integration work.

## Sources

- GPIO Zero docs: https://gpiozero.readthedocs.io/
- GPIO Zero pin factory environment variables: https://gpiozero.readthedocs.io/en/stable/cli_env.html
- libgpiod docs: https://libgpiod.readthedocs.io/
- Adafruit Blinka on Raspberry Pi 5: https://circuitpython.org/blinka/raspberry_pi_5/
- Blinka install guide for Raspberry Pi: https://learn.adafruit.com/circuitpython-on-raspberrypi-linux/installing-circuitpython-on-raspberry-pi
- Raspberry Pi forum discussion on Pi 5 GPIO changes: https://forums.raspberrypi.com/viewtopic.php?t=359742
- rpi-lgpio PyPI: https://pypi.org/project/rpi-lgpio/
- smbus2 docs: https://smbus2.readthedocs.io/

## Recommended Choices For This Project

Use two different layers:

- MPU6050 I2C sensor reads:
  - Preferred first path: `adafruit-circuitpython-mpu6050` with Blinka.
  - Good low-level path: `smbus2` directly reading MPU6050 registers.

- General car GPIO:
  - Preferred simple path: `gpiozero`, backed by `lgpio` on Pi 5.
  - Lower-level path: `libgpiod` if gpiozero behavior is insufficient.

## Avoid For New Pi 5 Code

- Do not start new code with old `RPi.GPIO` tutorials.
- Do not rely on old `pigpio` examples for Pi 5 GPIO control.
- If old sample code imports `RPi.GPIO`, either rewrite to gpiozero/libgpiod or use `rpi-lgpio` only as a compatibility bridge.

## Suggested Pi Setup Commands

On Raspberry Pi OS Bookworm/Trixie:

```bash
sudo apt-get update
sudo apt-get install -y python3-venv i2c-tools libgpiod-dev python3-libgpiod
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
pip install --upgrade pip
pip install adafruit-blinka adafruit-circuitpython-mpu6050 smbus2 gpiozero
```

Enable I2C:

```bash
sudo raspi-config nonint do_i2c 0
sudo reboot
```

Confirm I2C:

```bash
ls /dev/i2c*
sudo i2cdetect -y 1
```

## Minimal MPU6050 Read Direction

For first validation, use polling instead of `INT`.

Read:

- acceleration
- gyro angular velocity
- temperature

Then keep the car still for several seconds and average the Z gyro value to estimate bias. For a first 90-degree rotation test, integrate `(gyro_z - bias) * dt` over time and compare the estimated angle against a manually measured turn.
