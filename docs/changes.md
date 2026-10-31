# Changes

## Unreleased

**Action needed when updating:**

* Rebuild and flash the sensor firmware. eddy-ng now detects out-of-date sensor firmware at startup.
* On Klipper, run `./install.sh` again. eddy-ng is now installed as a package in `klippy/extras/probe_eddy_ng/`.
* The `wma` tap mode has been removed. If your config has `tap_mode: wma`, remove it, along with any `tap_threshold` tuned for it.
* `EDDYNG_BED_MESH_EXPERIMENTAL` has been removed. Use `BED_MESH_CALIBRATE METHOD=rapid_scan` on Kalico too (needs an October 2026 or newer Kalico).

**New:**

* [Profiles](profiles.md): plate profiles hold a calibration per build plate, and tap profiles hold tap settings with a `tap_adjust_z` per material. Commands: `PROBE_EDDY_NG_PLATE_PROFILE` and `PROBE_EDDY_NG_TAP_PROFILE`; config: `default_plate`, `tap_adjust_z_<material>`.
* `PROBE_EDDY_NG_CALIBRATE` takes `USE_FOR=homing`, `tap` or `homing,tap` to make the active plate use the calibrated drive current.
* On Klipper, probe commands accept `METHOD=tap` to probe with a tap (see [Probing with tap](tap.md#probing-with-tap)).
* Z homing works with Klipper v0.13.0-665 and newer, which home Z through the probe.
* Experimental [nozzle alignment](nozzle-alignment.md) for toolchangers with an upward-facing sensor.
* All commands are also available as `EDDYNG_<name>`, and take `SENSOR=` to choose a sensor.
* New sensor options: `ldc_offset`, `ldc_settle_time`, `ldc_fref_divider`, `ldc_fin_divider`, and `sensor_type: ldc1612_internal_clk`. New commands: `LDC_NG_DUMP`, `LDC_NG_SET`, `LDC_NG_RESET`, `PROBE_EDDY_NG_STREAM_START`/`_STOP`.

**Changed and fixed:**

* Calibrations are saved in a new format (`calibration_version: 6`). Older calibrations still load, and are converted the next time they're saved.
* `debug` now defaults to off, so taps no longer write `/tmp/tap-samples-*.csv` unless it's enabled.
* `[bed_mesh]` is no longer required.
* `SETUP` and `CALIBRATE` no longer drop a `tap_adjust_z` saved by `SAVE_CONFIG`.
* `Z_OFFSET_APPLY_PROBE` uses the runtime `tap_adjust_z`.
* More accurate sample timing, tap detection on the sensor's raw readings, and fixes for bed mesh interpolation of missing points and for retrying failed taps.

## 28-Apr-2025
* Restructured the entire page layout to be more accommodating for GitHub Wiki
* Added more detailed instructions for Mellow Fly SHT-36v3, also applicable to other CAN toolboards with integrated LDC1612 sensors.

## 22-Feb-2025

* Added `HOME_Z=1` optional parameter to `TAP`. If set, tap will now home the Z axis to the computed tap position instead of setting a G-Code offset.
* Support Z offset modifying and saving. After a tap (with or without `HOME_Z=1`), if the Z offset is manually modified, calling `Z_OFFSET_APPLY_PROBE` (via the save icons in Mainsail/Fluidd) will update `tap_adjust_z`.
* Added `max_errors` configuration to allow changing the number of sequential sensor errors that are to be ignored, instead of aborting the operation. The default is 0, meaning that any sensor error will abort tap/homing (this was the previous behaviour).
* New status values: `tap_adjust_z`: current tap_adjust_z value. `last_tap_z`: the last raw Z tap value (before adding tap_adjust_z).
* Wiki documentation updates will follow.

## 11-Feb-2025

* Fixed a bug that would cause tap to be detected later than it should. The value is the same, but the toolhead was moving more than it needed to be.
* Properly update `last_probe_value` status value, which is accessible to macros after a `EDDY_NG_PROBE`/`EDDY_NG_PROBE_STATIC` command.

## 8-Feb-2025

* Introduced `EDDY_NG_SETUP`, which attempts to set up and calibrate both homing and tap configurations automatically. If it doesn't work, the regular process can be used.
* `EDDY_NG_PROBE_STATIC` with `HOME_Z=1` will no longer print the probe results to the console. (This is typically used during homing, and the results are not very meaningful to view in the middle of homing.)

## 1-Feb-2025

* Changed the calibration Z start to 15.0 from 5.0. This significantly improves accuracy in the 5-10mm range, which is helpful for doing QGL or Z-Tilt adjustments higher up off the build plate in case drastic adjustments are needed. It's recommended to recalibrate, but not necessary. There is no need to recalibrate a separate tap drive current, if you have one.
* Added support for Mellow Fly boards. Use `sensor_type: mellow_fly`. _If you already already using a Mellow Fly board with the generic ldc1612 sensor type, you must recalibrate if you switch your sensor type! Don't home Z without recalibrating!_
* Improved `PROBE_EDDY_NG_PROBE_ACCURACY` to do something useful and report useful results. This command this will run a probe every 0.5mm starting from `Z` (5.0 by default) down to 0.5mm and report results.
* Introduced `write_tap_plot` boolean config, to disable writing the plot if desired. Computing the plot takes a few seconds on underpowered CPUs.
* Fixed `Already sampling!` error after a failed QGL.
* Fixed a miscalculation for LDC settle time, which could cause the sensor to occasionally incorrectly report sensor errors.
* Fixed old-API `run_probe` return value (for Kalico)

## 28-Jan-2025

* Made `butter` mode the default. `scipy` is not required to use it as long as the filter parameters are kept to the default, and the data rate is 250 (default) or 500. To use the previous detection mode, set `tap_mode: wma`, though there should be little reason to do so. If you have a tuned threshold, consider starting from the default (250) and re-tuning.

## 26-Jan-2025

* Introduced `tap_samples`, `tap_max_samples`, and `tap_samples_stddev` to specify the number of samples to average together, the maximum number of samples to take, and the maximum standard deviation any group of `tap_samples` needs to have in order to be used. This can greatly increase the accuracy of tap results by throwing out outliers. Default values are 3 samples, 5 max samples, and 0.025 standard deviation.
* Introduced `tap_mode` to specify different approaches for calculating the tap. Each mode has a different threshold value, so if you switch modes, either remove your threshold to use the default, or calculate it beforehand by using the `TAP` command with an explicit `MODE` and `THRESHOLD`. The two modes are:
  * `wma`: Computes tap position based on moving averages of the frequency and their derivatives. Very sensitive to noise. 
  * `butter`: Use a Butterworth filter to compute tap position. Much more accurate than `wma` and less sensitive to noise, it can greatly improve tap accuracy. Recommended, and is likely to become the default soon.
* Removed `scipy_*` options. These became the `butter` tap mode, which is now computed on the MCU.

