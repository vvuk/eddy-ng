# BTT Eddy

eddy-ng works well with the BTT Eddy (USB, Duo and Coil). Its small size makes it easy to fit on most toolheads, but its small coil makes it harder to set up, especially for tap. Once it's set up, it works very well.

## Mounting

Tap with the BTT Eddy is very sensitive to the distance between the coil and the nozzle tip. Mount the **bottom of the coil PCB** (not the bottom of the case; the case wall is about 1.2mm thick) about 2.95mm above the nozzle tip.

Known good mounts for the Sovol SV08 stock toolhead:

- [Rappetor's mount (low version only)](https://www.printables.com/model/968900-sovol-sv08-btt-eddy-mount)
- [HVFdesigns' minimal SV08 mount](https://makerworld.com/en/models/972760#profileId-944824)
- [Vlad's hacky mount (probably not preferred any more)](https://www.printables.com/model/1147774-sovol-sv08-btt-eddy-mount-for-tap)

## Coming from BTT's Eddy setup

If the BTT Eddy was set up with Klipper's own `probe_eddy_current` before:

- Remove BTT's `eddy.cfg` (or stop including it). Its macros don't apply to eddy-ng and will cause problems.
- Delete the `probe_eddy_current` and `temperature_probe` sections from the saved settings at the bottom of `printer.cfg`.
- If your `[bed_mesh]` section was in `eddy.cfg`, move it to `printer.cfg`.

## Firmware

1. [Install eddy-ng](installation.md).
2. Build and flash Klipper firmware for the Eddy USB/Duo, or, for the Eddy Coil, for the MCU it's connected to. BTT's [Compiling Firmware instructions](https://github.com/bigtreetech/Eddy?tab=readme-ov-file#compiling-firmware) cover this, but **skip the `git checkout master` step**. On Kalico, enable "Include eddy-ng ldc1612 support" under "Firmware Extras" in `menuconfig`.

## Configuration

Eddy USB/Duo (replace the serial ID and offsets with yours; if you already have an `[mcu eddy]` section, keep it):

```
[mcu eddy]
# USB: your serial port ID; CAN: use canbus_uuid instead
serial: /dev/serial/by-id/usb-Klipper_rp2040_5044340310A9D51C-if00
restart_method: command

[probe_eddy_ng btt_eddy]
sensor_type: btt_eddy
i2c_mcu: eddy
i2c_bus: i2c0f
x_offset: ...  # your probe's offset from the nozzle
y_offset: ...

[temperature_sensor btt_eddy_mcu]
sensor_type: temperature_mcu
sensor_mcu: eddy
min_temp: 10
max_temp: 100

[temperature_sensor btt_eddy]
sensor_type: Generic 3950
sensor_pin: eddy:gpio26
```

Eddy Coil:

```
[probe_eddy_ng btt_eddy]
sensor_type: btt_eddy
i2c_mcu: ...   # the MCU the coil is connected to
i2c_bus: ...   # that MCU's I2C bus
x_offset: ...
y_offset: ...
```

This is the minimum. Also set up [Safety](safety.md) and [Macros](macros.md), then go to [Calibration](calibration.md).

## Going back to BTT's Eddy support

1. Run `./install.sh --uninstall` in `~/eddy-ng`.
2. Restore your previous Eddy config.
3. Rebuild and reflash Klipper.
