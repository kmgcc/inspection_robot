# Raspberry Pi 5 GPIO / I2C Quick Reference

Collected for the inspection robot MPU6050 integration work.

## Sources

- Raspberry Pi 5 product brief: https://pip.raspberrypi.com/documents/RP-008348-DS-raspberry-pi-5-product-brief.pdf
- RP1 peripherals datasheet: https://pip.raspberrypi.com/documents/RP-008370-DS-1-rp1-peripherals.pdf
- Raspberry Pi hardware documentation: https://www.raspberrypi.com/documentation/computers/raspberry-pi.html
- Pinout.xyz I2C pinout: https://pinout.xyz/pinout/i2c

## Pi 5 Hardware Facts We Need

- Raspberry Pi 5 exposes the standard Raspberry Pi 40-pin GPIO header.
- Raspberry Pi 5 uses the RP1 I/O controller for the 40-pin header peripherals.
- Pi GPIO logic is 3.3V. Do not feed 5V logic into GPIO, SDA, SCL, INT, or other signal pins.
- The official Pi 5 supply recommendation is 5V/5A USB-C with Power Delivery support. A weak supply can cause instability during motor movement.

## I2C Pins For MPU6050

Use the primary I2C bus exposed on the 40-pin header:

| Function | Physical Pin | BCM GPIO | Pi Label |
|---|---:|---:|---|
| 3.3V power | 1 or 17 | n/a | 3V3 |
| Ground | 6, 9, 14, 20, 25, 30, 34, or 39 | n/a | GND |
| I2C data | 3 | GPIO2 | SDA1 |
| I2C clock | 5 | GPIO3 | SCL1 |

Pinout.xyz notes that GPIO2/GPIO3 are the I2C1 pins and include fixed pull-up resistors to 3.3V. The MPU6050 module also has 4.7k pull-ups to its local 3.3V rail, so do not add extra pull-ups for the first test.

## First Validation Commands

Enable I2C:

```bash
sudo raspi-config nonint do_i2c 0
sudo reboot
```

Install scan tools:

```bash
sudo apt-get update
sudo apt-get install -y i2c-tools
```

Confirm the bus exists:

```bash
ls /dev/i2c*
```

Scan the default bus:

```bash
sudo i2cdetect -y 1
```

Expected result for our module:

- `0x68` when AD0 is floating or tied to GND.
- `0x69` when AD0 is tied to VDD.

## Project Wiring Recommendation

For the first MPU6050 test, keep the wiring conservative:

```text
MPU6050 VCC  -> Raspberry Pi 5 3.3V, physical pin 1 or 17
MPU6050 GND  -> Raspberry Pi GND, e.g. physical pin 6
MPU6050 SCL  -> Raspberry Pi GPIO3 / SCL1, physical pin 5
MPU6050 SDA  -> Raspberry Pi GPIO2 / SDA1, physical pin 3
MPU6050 AD0  -> leave floating or connect to GND, address 0x68
MPU6050 INT  -> leave disconnected for the first polling-based read test
```

The local module schematic indicates SDA/SCL pull up to the module's 3.3V rail, not 5V. Even so, start with 3.3V module supply to remove uncertainty.
