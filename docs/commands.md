# Commands

Every `PROBE_EDDY_NG_<name>` command is also available as `EDDYNG_<name>`. All of them take `SENSOR=<name>` to choose a `[probe_eddy_ng <name>]` section; without it, they use the primary sensor (see [Multiple sensors](advanced.md#multiple-sensors)).

## Setup and calibration

**`PROBE_EDDY_NG_SETUP`**: find and calibrate drive currents for homing and tap, starting with a paper test (see [Calibration](calibration.md)). Calibrates the active plate.
- `START_DRIVE_CURRENT=`, `END_DRIVE_CURRENT=`: the range of drive currents to try, from high to low. Defaults to the sensor type's range.
- `TRUST_Z=1`: skip the paper test and treat the current Z position as accurate.
- `DEBUG=1`: report why each drive current failed, and write the calibration debug files.

**`PROBE_EDDY_NG_CALIBRATE`**: calibrate one drive current, starting with a paper test. Calibrates the active plate.
- `DRIVE_CURRENT=`: default is the current homing drive current.
- `START_Z=`: height to calibrate down from. Default `calibration_z_max`.
- `TARGET_Z=`: height to calibrate down to. Default 0.
- `SPEED=`, `LIFT_SPEED=`: move speeds.

**`PROBE_EDDY_NG_CALIBRATION_STATUS`**: show the active plate, its homing and tap drive currents, and the range of each calibration.

**`PROBE_EDDY_NG_CLEAR_CALIBRATION`**: remove the active plate's calibration. `SAVE_CONFIG` to make it permanent.
- `DRIVE_CURRENT=`: only remove this drive current's calibration.

**`PROBE_EDDY_NG_TEST_DRIVE_CURRENT`**: with Z homed, report the range of heights where a drive current gives readings (see [Advanced](advanced.md#finding-drive-currents-by-hand)).
- `DRIVE_CURRENT=`: default is the current homing drive current.
- `START_Z=`, `TARGET_Z=`: the range to test. Defaults `calibration_z_max` and 0.
- `DEBUG=1`: write the calibration debug files.

## Probing

**`PROBE_EDDY_NG_STATUS`** (`PES`): read the sensor once and show the raw value, status and height.

**`PROBE_EDDY_NG_PROBE_STATIC`** (`PEPS`): read the height at the current position without moving.
- `DURATION=`: how long to sample, in seconds. Default 0.1.
- `DRIVE_CURRENT=`: use a different (calibrated) drive current.
- `HOME_Z=1`: set the toolhead's Z to the height read, instead of printing it.
- `SAVE=1`: write the samples to `/tmp/eddy-probe-static.csv`.

**`PROBE_EDDY_NG_PROBE`** (`PEP`): move to a height, then do `PROBE_STATIC` there. Takes the same parameters, plus:
- `Z=`: the height to probe at. Default `home_trigger_height`.

**`PROBE_EDDY_NG_PROBE_ACCURACY`**: probe at a series of heights and report the spread and deviation. Z must be homed.
- `Z=`: the starting height. Default 5.0.
- `OFFSETS=`: comma-separated offsets from `Z` to probe at, e.g. `OFFSETS=-1,-2,-3`. Default: every 0.5mm from `Z` down to 0.5.
- `DURATION=`: how long to sample at each height. Default 0.1.
- `DRIVE_CURRENT=`, `SPEED=`, `LIFT_SPEED=`.

## Tap

**`PROBE_EDDY_NG_TAP`** (`PETAP`): tap the bed at the current position to set Z (see [Tap](tap.md)). Parameters override the active tap profile's settings for this tap only:
- `THRESHOLD=` (or `TT=`, `TAP_THRESHOLD=`): `tap_threshold`.
- `SAMPLES=`, `MAX_SAMPLES=`, `SAMPLES_STDDEV=`, `USE_MEDIAN=`: `tap_samples`, `tap_max_samples`, `tap_samples_stddev`, `tap_use_median`.
- `START_Z=`, `TARGET_Z=`: `tap_start_z`, `tap_target_z`.
- `SPEED=`: `tap_speed`.
- `RETRACT_SPEED=`: speed to raise the toolhead.
- `DRIVE_CURRENT=`: `tap_drive_current`.
- `ADJUST_Z=`: `tap_adjust_z`.
- `HOME_Z=0`: set the G-code Z offset to the tap height instead of re-homing Z. Default 1.
- `RETRACT=0`: leave the toolhead at `home_trigger_height` afterwards instead of raising it.
- `PLOT=1`: write a graph of the last tap; `PLOT=2`: one per tap; `PLOT=0`: none.

**`PROBE_EDDY_NG_SET_TAP_ADJUST_Z`**: set `tap_adjust_z` for the active tap profile and material. `SAVE_CONFIG` to make it permanent.
- `VALUE=`: the new value.
- `ADJUST=`: add this to the current value.

**`Z_OFFSET_APPLY_PROBE`**: add the current G-code Z offset (e.g. from babystepping after a tap) to `tap_adjust_z` for the active tap profile and material. `SAVE_CONFIG` to make it permanent. Replaces Klipper's command of the same name.

**`PROBE_EDDY_NG_SET_TAP_OFFSET`**: set the sensor offset that tap measured and probing uses. Set it to 0 at the end of a print.
- `VALUE=`: the new value.
- `ADJUST=`: add this to the current value.

Klipper's own probe commands (`PROBE`, `QUAD_GANTRY_LEVEL` and so on) take `METHOD=tap` to probe with a tap (see [Probing with tap](tap.md#probing-with-tap)).

## Profiles

See [Profiles](profiles.md).

**`PROBE_EDDY_NG_PLATE_PROFILE`**: with no parameters, list the plates.
- `LOAD=<plate>`: switch to a plate. Un-homes Z.
- `CREATE=<plate>`: create an uncalibrated plate and switch to it. Add `COPY=1` to copy the active plate's calibration.
- `REMOVE=<plate>`: remove a plate. It can't be the active plate or `default_plate`.

**`PROBE_EDDY_NG_TAP_PROFILE`**: with no parameters, list the tap profiles.
- `LOAD=<profile>`: switch to a tap profile.
- `MATERIAL=<material>`: with `LOAD=`, the material whose `tap_adjust_z` to use.
- `CREATE=<profile>`: create a tap profile with the default settings and switch to it. Add `COPY=1` to copy the active tap profile.
- `REMOVE=<profile>`: remove a tap profile. It can't be the active one.

## Low-level

**`PROBE_EDDY_NG_STREAM_START`**, **`PROBE_EDDY_NG_STREAM_STOP`**: record every sensor reading between the two commands to `/tmp/stream.csv`.

These take `CHIP=<name>` instead of `SENSOR=` (see [Sensor tuning](advanced.md#sensor-tuning)):

**`LDC_NG_CALIBRATE_DRIVE_CURRENT`**: ask the LDC1612 for a starting drive current. Run it with the sensor about 10mm above the bed.

**`LDC_NG_SET_DRIVE_CURRENT VAL=<n>`**: set the drive current register.

**`LDC_NG_DUMP`**: show the LDC1612's configuration registers.

**`LDC_NG_SET`**: change LDC1612 settings until the next restart, then show them.
- `DEGLITCH=`: `1mhz`, `3.3mhz`, `10mhz` or `33mhz`.
- `OFFSET=`: frequency offset, in Hz.
- `SETTLE=`: settle time, in ms.
- `RCOUNT=`: conversion time, in ms.

**`LDC_NG_RESET`**: reset and reinitialize the LDC1612.

For the nozzle alignment commands, see [Nozzle alignment](nozzle-alignment.md).
