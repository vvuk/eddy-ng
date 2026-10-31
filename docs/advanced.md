# Advanced

## Drive current

The LDC1612's drive current setting (0–31) controls the current through the sensor coil. Higher drive current gives stronger oscillation, which may be needed when the coil is close to the bed and eddy currents damp it more.

At each drive current, the sensor can only read heights in a certain range. Too close or too far, the oscillation amplitude is out of range and the sensor reports errors instead of readings. What matters physically is the distance from the coil to the bed, so the coil's mounting height relative to the nozzle shifts this range.

- **Homing** needs readings from about 5mm down to 0.5mm (setup requires this range). Homing must start above `home_trigger_height` + `home_trigger_safe_start_offset` (3mm by default) and triggers at `home_trigger_height` (2mm).
- **Tap** needs readings from `tap_start_z` (3mm by default) down to contact.

One drive current may not cover both ranges. `reg_drive_current` is used for homing and everything else, and `tap_drive_current` for tap (it defaults to `reg_drive_current`). Each needs its own calibration: `PROBE_EDDY_NG_SETUP` does both, or use `PROBE_EDDY_NG_CALIBRATE DRIVE_CURRENT=<n>`.

The coil design matters a lot. The BTT Eddy's small coil is sensitive to temperature and nearby metal and may need manual tuning; other probes usually work after setup.

### Finding drive currents by hand

Always start with `PROBE_EDDY_NG_SETUP`. If it sets up homing but not tap, or tap stops working when the bed is hot, test other drive currents with Z homed:

```
PROBE_EDDY_NG_TEST_DRIVE_CURRENT DRIVE_CURRENT=16
```

This moves from `START_Z` (default `calibration_z_max`, 15mm) down to `TARGET_Z` (default 0) and reports the range of heights where the sensor gave readings. No paper test is needed.

Start with your homing drive current plus 1 or 2. If the range is too high (doesn't reach low enough), try one higher; if it's too low, one lower. Don't go more than a few steps from the default. Once you find a value, set `tap_drive_current` (or `reg_drive_current`) in your config and calibrate it.

If no drive current covers what you need, move the sensor: higher if it can't read low enough, lower if it can't read high enough.

To look at the raw sensor state at a problem height, move there and run `PROBE_EDDY_NG_STATUS` a few times. `LDC_NG_SET_DRIVE_CURRENT VAL=<n>` changes the drive current until the next command that sets it.

## Sensor tuning

These `[probe_eddy_ng ...]` options change how the LDC1612 is configured. The `sensor_type` defaults are right for the supported probes, so you'll mainly need these for other hardware.

- `samples_per_second` (default 250): the sensor data rate. Values other than 250 or 500 need scipy.
- `ldc_deglitch` (`1mhz`, `3.3mhz`, `10mhz` or `33mhz`; default `10mhz`): the input deglitch filter. Pick the lowest setting above the sensor's oscillation frequency.
- `ldc_settle_time`: the settle time before each conversion, in seconds.
- `ldc_offset`: a frequency offset in Hz that the chip subtracts before conversion.
- `ldc_fref_divider`, `ldc_fin_divider`: the reference and input clock dividers.
- `sensor_type: ldc1612_internal_clk`: a generic LDC1612 running from its internal oscillator, for boards with no external clock.

`LDC_NG_DUMP` shows the current register settings, and `LDC_NG_SET` (`DEGLITCH=`, `OFFSET=`, `SETTLE=` in ms, `RCOUNT=` in ms) changes them at runtime for experimenting. `LDC_NG_CALIBRATE_DRIVE_CURRENT` asks the chip for a starting drive current; run it with the sensor about 10mm above the bed.

## Multiple sensors

Every `[probe_eddy_ng <name>]` section's commands take `SENSOR=<name>` (and the `LDC_NG_*` commands `CHIP=<name>`). They also work without it for the primary sensor, which is any section without `primary: false`. Only one section can be the Z probe; an extra sensor, like a [nozzle alignment](nozzle-alignment.md) sensor (which defaults to `primary: false`), must not be primary.

## Debug files

| File | Written by |
|---|---|
| `/tmp/eddy-calibration.csv`, `/tmp/eddy-calibration.html` | `CALIBRATE`, and `SETUP DEBUG=1` |
| `/tmp/tap.html`, `/tmp/tap-<n>.html` | `TAP` with `write_tap_plot`/`PLOT=` (see [Tap graph](tap.md#tap-graph)) |
| `/tmp/tap-samples-<n>.csv` | every tap, with `debug: true` |
| `/tmp/eddy-probe-static.csv` | `PROBE_EDDY_NG_PROBE_STATIC SAVE=1` |
| `/tmp/stream.csv` | `PROBE_EDDY_NG_STREAM_START` … `PROBE_EDDY_NG_STREAM_STOP` |

`debug: true` also writes much more detail to `klippy.log`. Include these files when reporting problems.

## Moving from other probes

There's no specific guide yet. You need a `[probe_eddy_ng ...]` section with your sensor's `sensor_type` and I2C settings (`i2c_mcu`, `i2c_bus`, and `i2c_address` if it isn't the default 42), and possibly some of the [sensor tuning](#sensor-tuning) options.
