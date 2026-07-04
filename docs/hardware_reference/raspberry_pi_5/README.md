# Raspberry Pi 5 Reference

Local reference material for Raspberry Pi 5 GPIO, I2C, and Python GPIO/I2C software.

## Saved Files

- `raspberry-pi-5-product-brief.pdf`
  - Source: https://pip.raspberrypi.com/documents/RP-008348-DS-raspberry-pi-5-product-brief.pdf
  - Use: official Pi 5 overview, 5V/5A power requirement, standard 40-pin header, RP1 I/O controller.

- `rp1-peripherals.pdf`
  - Source: https://pip.raspberrypi.com/documents/RP-008370-DS-1-rp1-peripherals.pdf
  - Use: RP1 GPIO, I2C, SPI, UART, PWM low-level reference.

- `pinout-xyz-home.html`
  - Source: https://pinout.xyz/
  - Use: offline copy of the interactive Raspberry Pi GPIO pinout page.

- `pinout-xyz-i2c.html`
  - Source: https://pinout.xyz/pinout/i2c
  - Use: I2C pin reference for GPIO2/SDA1 and GPIO3/SCL1.

- `blinka-raspberry-pi-5.html`
  - Source: https://circuitpython.org/blinka/raspberry_pi_5/
  - Use: Adafruit Blinka support page for Raspberry Pi 5.

- `adafruit-blinka-install-raspberry-pi.html`
  - Source: https://learn.adafruit.com/circuitpython-on-raspberrypi-linux/installing-circuitpython-on-raspberry-pi
  - Use: practical setup commands for I2C, libgpiod, Blinka, and Raspberry Pi OS virtual environments.

- `raspberry-pi-5-gpio-i2c-quick-reference.md`
  - Use: project-specific wiring and validation notes.

- `raspberry-pi-5-gpio-software-stack.md`
  - Use: project-specific Python GPIO/I2C library choice notes.

## Command-Line Download Gaps

The official Raspberry Pi product and documentation HTML pages returned HTTP 403 to `curl` during collection. The official PDFs were downloaded successfully, and the facts needed for this project are preserved in the Markdown notes above with source URLs.
