# Configuration reference

These options go in the `[probe_eddy_ng <name>]` section. Defaults are shown; options without a default are required.

## Sensor

- `sensor_type`: `btt_eddy`, `cartographer`, `mellow_fly`, `ldc1612` (a generic LDC1612 with a 12MHz external clock) or `ldc1612_internal_clk` (a generic LDC1612 using its internal oscillator). Sets the clock settings, the default drive current and the drive currents `SETUP` tries.
- `i2c_mcu`, `i2c_bus`: where the sensor is connected. The other standard Klipper I2C options also work.
- `i2c_address: 42`: the sensor's I2C address.
- `i2c_speed: 400000`
- `intb_pin`: the sensor's INTB pin, if connected. Must be on the same MCU.
- `samples_per_second: 250`: the sensor data rate. Values other than 250 or 500 need scipy.
- `ldc_deglitch: 10mhz`, `ldc_settle_time`, `ldc_offset: 0`, `ldc_fref_divider`, `ldc_fin_divider`: LDC1612 settings; see [Sensor tuning](advanced.md#sensor-tuning).

## General

- `x_offset`, `y_offset`: the sensor's position relative to the nozzle. They can't both be 0 (the sensor can't be at the nozzle).
- `reg_drive_current`: the drive current for homing and probing. Defaults to the sensor type's default (15; 26 for Cartographer). `SETUP` saves the value it picks.
- `probe_speed: 5.0`: speed for homing and probing moves.
- `lift_speed: 10.0`: speed for raising the toolhead.
- `move_speed: 50.0`: speed for XY moves during calibration and tap.
- `calibration_z_max: 15.0`: the height calibration starts from. Must be at least `home_trigger_height` + `home_trigger_safe_start_offset` + 1.
- `default_plate`: the [plate profile](profiles.md#plate-profiles) to load at startup. Default: the calibration in this section.

## Homing

- `home_trigger_height: 2.0`: the height at which homing triggers, and the height bed meshes and other probing read at. Must be at least 1.0; 2.0 to 2.5 work well.
- `home_trigger_safe_start_offset: 1.0`: homing must start at least this far above `home_trigger_height`, or it aborts because the toolhead is already too low. Must be at least 0.5.

## Tap

These can also be set per [tap profile](profiles.md#tap-profiles), except `tap_trigger_safe_start_height`.

- `tap_drive_current`: the drive current for tap. Defaults to `reg_drive_current`. It needs its own calibration; `SETUP` saves the value it picks.
- `tap_start_z: 3.0`: the height each tap starts from. The tap drive current must be able to read from here down to `tap_target_z`.
- `tap_target_z: -0.250`: the lowest Z a tap moves to. If no tap is detected by then, the tap fails. Don't set this much lower: in a failed tap, the nozzle is pushed this far into the bed.
- `tap_threshold: 250`: how far (in Hz) the filtered signal must drop from its last peak to detect a tap. Lower is more sensitive but can trigger before contact. See [Tuning](tap.md#tuning).
- `tap_speed: 3.0`: the speed of the tap move. The toolhead keeps moving briefly after the tap is detected, so keep this low.
- `tap_adjust_z: 0.0`: added to the tap height. Positive values raise the nozzle. See [Adjusting tap Z](tap.md#adjusting-tap-z).
- `tap_adjust_z_<material>`: `tap_adjust_z` for a material, e.g. `tap_adjust_z_petg`, used when the tap profile is loaded with that `MATERIAL=`.
- `tap_samples: 3`: how many taps to combine.
- `tap_max_samples: 5`: the most taps to try before giving up.
- `tap_samples_stddev: 0.020`: the largest standard deviation a group of `tap_samples` taps can have to be used.
- `tap_use_median: False`: use the median of the group instead of the mean.
- `tap_time_position: 0.3`: where the tap is placed between the start of detection (0.0) and the moment the threshold was crossed (1.0).
- `tap_trigger_safe_start_height`: a tap can only be detected once the sensor reads below this height. Defaults to half of `home_trigger_height`, and must be below it.
- `tap_butter_lowcut: 5.0`, `tap_butter_highcut: 25.0`, `tap_butter_order: 2`: the band-pass filter used for tap detection. Order 1 to 4. Changing these needs scipy.

## Probing

- `scan_sample_time: 0.100`: how long to read at each point when probing points one at a time (e.g. QGL, or a bed mesh without `METHOD=rapid_scan`). Probe commands can override it with `SAMPLE_TIME=`.
- `scan_sample_time_delay: 0.050`: how long to wait at each point before reading.

## Debugging

- `write_tap_plot: False`: write a graph of the last tap in each `TAP` to `/tmp/tap.html`. Needs plotly.
- `write_every_tap_plot: False`: with `write_tap_plot`, write a graph of every tap.
- `max_errors: 0`: how many sensor errors in a row homing and tap ignore before failing.
- `debug: False`: write detailed logs to `klippy.log` and per-tap sample files to `/tmp` (see [Debug files](advanced.md#debug-files)).
- `allow_unsafe: False`: disable some safety checks. For development only.

## Multiple sensors

- `primary: True`: whether this sensor's commands also work without `SENSOR=`. Defaults to `False` for nozzle alignment sensors. See [Multiple sensors](advanced.md#multiple-sensors).
- `bigfoot: False`, `bigfoot_scan_position`: make this an upward-facing nozzle alignment sensor. See [Nozzle alignment](nozzle-alignment.md).

## Profile sections

- `[probe_eddy_ng_plate <sensor> <plate>]`: a [plate profile](profiles.md#plate-profiles). Written by `PROBE_EDDY_NG_PLATE_PROFILE` and `SAVE_CONFIG`.
- `[probe_eddy_ng_tap <profile>]`: a [tap profile](profiles.md#tap-profiles). Takes the tap options above, plus `reg_drive_current`; `tap_adjust_z` is required.
