# MPU6050 Module Reference

Local vendor/module files copied from:

`/Users/kmg/Downloads/MPU6050模块镀金板焊直针`

## Saved Files

- `RM-MPU-6000A.pdf`
  - MPU-6000/MPU-6050 register map and descriptions.
  - Use this when writing or checking low-level I2C register reads.

- `MPU6050-V1-SCH.jpg`
  - Module schematic.
  - Key finding: the board includes a 3.3V LDO. MPU6050 `VDD` and `VLOGIC` are on `VCC_3.3V`.
  - Key finding: `SDA` and `SCL` are pulled up through 4.7k resistors to `VCC_3.3V`.
  - Key finding: no I2C level-shifter IC is shown.

- `MPU6050-V1.jpg`
  - Board outline and pin order drawing.
  - Header order: `VCC`, `GND`, `SCL`, `SDA`, `XDA`, `XCL`, `AD0`, `INT`.

- `引脚定义.docx`
  - Vendor pin description document.
  - States that the board has a 3.3V low-dropout regulator, so external supply may be 3.3V or 5V.
  - States that I2C SDA/SCL include 4.7k pull-up resistors.
  - States that AD0 has a 10k pull-down, so floating AD0 defaults to I2C address `0x68`; tied high changes address to `0x69`.

## Wiring Recommendation For Raspberry Pi 5

Use 3.3V supply for the first test:

```text
MPU6050 VCC  -> Raspberry Pi 5 3.3V, physical pin 1 or 17
MPU6050 GND  -> Raspberry Pi GND, e.g. physical pin 6
MPU6050 SCL  -> Raspberry Pi GPIO3 / SCL1, physical pin 5
MPU6050 SDA  -> Raspberry Pi GPIO2 / SDA1, physical pin 3
MPU6050 AD0  -> leave floating or connect to GND, address 0x68
MPU6050 INT  -> leave disconnected for first polling test
```

Although the schematic suggests I2C pull-ups are to 3.3V even if the module VCC pin is supplied from 5V, 3.3V supply keeps the first Raspberry Pi test simpler and safer.
