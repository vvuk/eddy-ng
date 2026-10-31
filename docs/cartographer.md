# Cartographer

_Cartographer support has only been lightly tested. Please report how it goes._

eddy-ng supports the **Cartographer V3** (STM32F042), over USB or CAN. The Cartographer V4 (STM32G431) isn't supported yet.

The firmware settings and pins below come from Timofey Titavets' [Cartographer reverse engineering thread](https://klipper.discourse.group/t/cartographer-v3-v4-reverse-engineering/25018) on the Klipper forum.

## Cartographer V4

The V4 uses an STM32G431 with different pins, and its LDC1612 clock is derived from the MCU's 170MHz clock (e.g. 21.25 or 28.33MHz). eddy-ng doesn't support it yet: its Cartographer firmware support is STM32F0-only, and it has no setting for a non-standard LDC1612 clock frequency. See the [forum thread](https://klipper.discourse.group/t/cartographer-v3-v4-reverse-engineering/25018) for the V4's build settings and pins.

## Firmware

You'll replace the Cartographer firmware with Klipper firmware that includes eddy-ng. To go back, follow the [Cartographer firmware instructions](https://docs.cartographer3d.com/cartographer-probe/firmware).

The Cartographer's LDC1612 gets its clock from the MCU, through Klipper's `[static_pwm_clock]`. This needs Klipper from October 2025 or newer, or Kalico from January 2026 or newer.

### Bootloader

The firmware goes after an 8KiB [Katapult](https://github.com/Arksine/katapult) bootloader. The easiest way to flash the Cartographer is over USB in DFU mode.

If flashing over CAN with the Katapult that came with the Cartographer doesn't work, flash a standard Katapult over DFU first. Build it with these `make menuconfig` settings:

```
Micro-controller Architecture (STMicroelectronics STM32)
Processor model (STM32F042)
Build Katapult deployment application (Do not build)
Clock Reference (24 MHz crystal)
Communication interface (USB (on PA9/PA10))       # or CAN bus (on PA9/PA10)
Application start offset (8KiB offset)
(1000000) CAN bus speed                           # CAN only
(PA1) GPIO pins to set on bootloader entry        # CAN only; empty for USB
[*] Support bootloader entry on rapid double click of reset button
[*] Enable Status LED
(PB5) Status LED GPIO Pin
```

On CAN, PA1 has to be high to connect the MCU to the CAN transceiver, so it's set on bootloader entry and at startup.

### Klipper

1. [Install eddy-ng](installation.md).
2. In `make menuconfig`:

   ```
   [*] Enable extra low-level configuration options
   Micro-controller Architecture (STMicroelectronics STM32)
   Processor model (STM32F042)
   Bootloader offset (8KiB bootloader)
   Clock Reference (24 MHz crystal)
   Communication interface (USB (on PA9/PA10))       # or CAN bus (on PA9/PA10)
   (1000000) CAN bus speed                           # CAN only
   (PA1) GPIO pins to set at micro-controller startup  # CAN only; empty for USB
   ```

   The F042 only has room for about 24KiB of firmware, so under "Optional features (to reduce code size)" enable only:

   ```
   [*] Support micro-controller based ADC (analog to digital)
   [*] Support communicating with external chips via I2C bus
   [*] Support hardware PWM (pulse width modulation)
   [*] Support ldc1612 eddy current sensor
   ```

   Disable everything else, including SPI. There's no room for the onboard accelerometer (ADXL345) together with eddy-ng. Make sure the built `out/klipper.bin` is smaller than 24576 bytes; the build doesn't check this.

   On Kalico, also enable "Include eddy-ng ldc1612 support" under "Firmware Extras".
3. Run `make`, and flash `out/klipper.bin` with Katapult, e.g. `python3 ~/katapult/scripts/flashtool.py -d /dev/serial/by-id/usb-katapult_...` (USB) or `-i can0 -u <uuid>` (CAN).

## Configuration

```
[mcu carto]
serial: /dev/serial/by-id/usb-Klipper_stm32f042x6_...-if00
#canbus_uuid: ...
restart_method: command

# The LDC1612's 24MHz clock
[static_pwm_clock ldc1612_clk_in]
pin: carto:PB4
frequency: 24000000

[probe_eddy_ng carto]
sensor_type: cartographer
i2c_mcu: carto
i2c_bus: i2c1_PB6_PB7
intb_pin: carto:PB3
x_offset: ...
y_offset: ...

[thermistor carto_47k]
temperature1: 25
resistance1: 47000
beta: 4041.0

[temperature_sensor carto]
sensor_type: carto_47k
sensor_pin: carto:PA4
pullup_resistor: 10000
min_temp: 0
max_temp: 125
```

- With `sensor_type: cartographer`, the eddy-ng firmware enables the LDC1612 (PA15) and turns on the status LED (PB5) itself, so don't add `output_pin` or `led` sections for those pins.
- The Cartographer uses a fixed drive current of 26, so `PROBE_EDDY_NG_SETUP` only tries that value.
- The thermistor (PA4) is optional.

Next: [Safety](safety.md).
