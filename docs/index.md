<img align="left" alt="eddy-ng" src="https://github.com/user-attachments/assets/b4fe8581-0d81-40e6-a82c-b0a96e82b1f9" width="150px">

# eddy-ng

_eddy-ng_ improves Eddy current probe support in Klipper and Kalico by setting the Z offset from actual nozzle contact with the build surface.

Eddy current probes are precise, but their readings drift as the conductivity of the bed and the coil change with temperature. Instead of trying to compensate for temperature, eddy-ng takes a physical approach:

1. **Calibrate** once, at any temperature.
2. **Home Z** with the sensor. This is a coarse home: good enough for homing, gantry leveling and other preparation, but not for printing.
3. **Tap** just before printing, with the bed at print temperature and the nozzle warm (but not hot enough to ooze). The nozzle touches the bed, which sets Z precisely.
4. At the same time, eddy-ng records the difference between the real height and what the sensor reads at that height. Bed meshes use this offset to correct for temperature drift.

The tap makes contact with the bed. By default it never goes below Z=-0.250 (relative to the homed zero), which is no worse than jogging Z one notch too far. A successful tap stops as soon as contact is detected, usually well above that.

> 🛑 Read [Safety](safety.md) before homing. Without the right `quad_gantry_level`/`z_tilt` and homing setup, the toolhead can crash into the bed.

## Requirements

- Recent Klipper or Kalico, running Python 3.8 or newer.
- An LDC1612-based Eddy current probe. eddy-ng has been tested with:
  - [BTT Eddy (USB, Duo and Coil)](btt-eddy.md)
  - [Cartographer v3 (USB/CAN)](cartographer.md)
  - [Mellow Fly toolboards with an LDC1612](mellow-fly.md)

  Other LDC1612 probes should work with `sensor_type: ldc1612` or `ldc1612_internal_clk` and some [tuning](advanced.md#sensor-tuning).
- You must be comfortable building and flashing Klipper firmware to the probe's MCU and editing your printer config. On Klipper, eddy-ng also changes two Klipper source files, so Klipper will show as "dirty". Kalico supports add-ons and needs no source changes.

## Documentation

1. [Installation](installation.md)
2. [Safety](safety.md): homing, QGL, Z tilt and bed mesh setup
3. Probe setup: [BTT Eddy](btt-eddy.md), [Cartographer](cartographer.md), [Mellow Fly](mellow-fly.md)
4. [Calibration](calibration.md)
5. [Tap](tap.md)
6. [Profiles](profiles.md): build plates and per-material tap settings
7. [Macros](macros.md): homing and print start/end
8. [Advanced](advanced.md): drive current, sensor tuning, multiple sensors, debug files
9. [Troubleshooting](troubleshooting.md)
10. [Commands](commands.md)
11. [Configuration reference](configuration.md)
12. [Nozzle alignment](nozzle-alignment.md) (experimental)
13. [Changes](changes.md)

## Help and support

Ask questions in the `eddy-ng` forum on the Sovol 3D Printers Discord (`https://discord.gg/Zg45rA52G7`, or search for "Sovol 3D Printers" in Discord's Discover tab). Nothing about eddy-ng is Sovol-specific; it's just where this work started.

File bugs in the [eddy-ng GitHub repository](https://github.com/vvuk/eddy-ng/issues).

If you find eddy-ng useful, please consider [supporting its development](https://ko-fi.com/vvuk).
