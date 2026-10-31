<img align="left" alt="eddy-ng" src="https://github.com/user-attachments/assets/b4fe8581-0d81-40e6-a82c-b0a96e82b1f9" width="150px">

# eddy-ng

_eddy-ng_ improves Eddy current probe support in Klipper and Kalico by setting the Z offset from actual nozzle contact with the build surface.

Eddy current probes are precise, but their readings drift as the conductivity of the bed and the coil change with temperature. Instead of trying to compensate for temperature, eddy-ng takes a physical approach:

1. **Calibrate** once, at any temperature.
2. **Home Z** with the sensor. This is a coarse home: good enough for homing, gantry leveling and other preparation, but not for printing.
3. **Tap** just before printing, with the bed at print temperature and the nozzle warm (but not hot -- you don't want filament drooling or damage to your build plate). The nozzle touches the bed, which sets Z precisely.
4. At the same time, eddy-ng records the difference between the real height and what the sensor reads at that height. Bed meshes use this offset to correct for temperature drift.

The tap makes contact with the bed. By default it never goes below Z=-0.250 (relative to the homed zero), which is no worse than jogging Z one notch too far. A successful tap stops as soon as contact is detected, usually well above that.

> 🛑 Make sure that you read the [quad gantry leveling](safety.md#quad-gantry-leveling) and [Z tilt adjust](safety.md#z-tilt-adjust) sections in [Safety](safety.md). Failure to set this up can cause your toolhead to crash into your build plate during these operations! Also read [safe homing](safety.md#safe-homing) before homing for the first time.

## Requirements

- Recent Klipper or Kalico, running Python 3.8 or newer.
- An LDC1612-based Eddy current probe. eddy-ng has been tested with:
  - [BTT Eddy (USB, Duo and Coil)](btt-eddy.md)
  - [Cartographer v3 (USB/CAN)](cartographer.md)
  - [Mellow Fly toolboards with an LDC1612](mellow-fly.md)

  Other LDC1612 probes should also work; see [Other LDC1612 probes](other-probes.md).
- You must be comfortable building and flashing Klipper firmware to the probe's MCU and editing your printer config. On Klipper, eddy-ng also changes two Klipper source files, so Klipper will show as "dirty". Kalico supports add-ons and needs no source changes.

## Setup checklist

1. [Install eddy-ng](installation.md).
2. Build and flash the firmware, and configure your probe: [BTT Eddy](btt-eddy.md), [Cartographer](cartographer.md), [Mellow Fly](mellow-fly.md) or [another LDC1612 probe](other-probes.md).
3. Set up [safe homing, bed mesh, and QGL or Z tilt](safety.md).
4. Check that eddy-ng can talk to the sensor, run `PROBE_EDDY_NG_SETUP`, test homing, and `SAVE_CONFIG` ([Calibration](calibration.md)).
5. Do a first [tap](tap.md), check Z=0 with paper, and adjust `tap_adjust_z`.
6. Add tap and the bed mesh to your [print start and end macros](printing.md).
7. Optionally, set up [profiles](profiles.md) for more build plates or per-material tap settings.

Each page ends with a link to the next step.

## Documentation

Setup, in order:

1. [Installation](installation.md)
2. Probe setup: [BTT Eddy](btt-eddy.md), [Cartographer](cartographer.md), [Mellow Fly](mellow-fly.md), [other LDC1612 probes](other-probes.md)
3. [Safety](safety.md): homing, bed mesh, QGL and Z tilt
4. [Calibration](calibration.md)
5. [Tap](tap.md)
6. [Printing](printing.md): print start and end macros, bed mesh, the tap offset
7. [Profiles](profiles.md): build plates and per-material tap settings

Reference:

- [Troubleshooting](troubleshooting.md)
- [Commands](commands.md)
- [Configuration reference](configuration.md)
- [Advanced](advanced.md): drive current, sensor tuning, debug files
- [Nozzle alignment](nozzle-alignment.md) (experimental)
- [Changes](changes.md)

## Help and support

File bugs in the [eddy-ng GitHub repository](https://github.com/vvuk/eddy-ng/issues).

If you find eddy-ng useful, please consider [supporting its development](https://ko-fi.com/vvuk).
