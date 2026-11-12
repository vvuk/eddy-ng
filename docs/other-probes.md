# Other LDC1612 probes

eddy-ng should work with any probe built around a TI LDC1612, but probes other than the [BTT Eddy](btt-eddy.md), [Cartographer](cartographer.md) and [Mellow Fly](mellow-fly.md) haven't been tested. Please report how it goes.

## What you need to know about your probe

- **The MCU and I2C bus** the LDC1612 is connected to, and its I2C address: 42 (0x2A) by default, or 43 (0x2B) if the board pulls the ADDR pin high.
- **The LDC1612's clock:**
  - a 12MHz external clock, as on the BTT Eddy, or
  - no external clock, using the LDC1612's internal oscillator (about 43MHz).

  Other external clock frequencies aren't supported yet.
- **The INTB pin**, if it's connected to the MCU. Optional.
- **The coil's height above the nozzle tip.** This decides which heights the sensor can read; see [Drive current](advanced.md#drive-current).

## Firmware

Build and flash Klipper firmware with eddy-ng for the MCU the LDC1612 is connected to, as described in [Installation](installation.md). eddy-ng's firmware doesn't do anything board-specific for these sensor types. If your board needs a pin set to power or enable the sensor, use an `[output_pin]` section, or `menuconfig`'s "GPIO pins to set at micro-controller startup".

## Configuration

```
[probe_eddy_ng my_probe]
sensor_type: ldc1612              # 12MHz external clock
#sensor_type: ldc1612_internal_clk  # no external clock
i2c_mcu: ...
i2c_bus: ...
#i2c_address: 43
#intb_pin: ...
x_offset: ...
y_offset: ...
```

Both sensor types start with drive current 15, and `PROBE_EDDY_NG_SETUP` tries 14 to 20. For a better starting point, run `LDC_NG_CALIBRATE_DRIVE_CURRENT` with the sensor about 10mm above the bed, and set `reg_drive_current` to the value it reports.

If the defaults don't work, [Sensor tuning](advanced.md#sensor-tuning) describes the LDC1612 settings you can change.

Next: [Safety](safety.md).
