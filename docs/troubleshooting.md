# Troubleshooting

## Setup and installation

### "sensor firmware is out of date" or "Unknown command: ldc1612_ng_..."

The sensor's MCU is running firmware without eddy-ng, or from an older eddy-ng. Rebuild the firmware for that MCU and flash it. On Kalico, enable "Include eddy-ng ldc1612 support" under "Firmware Extras" in `menuconfig`; on Klipper, run `install.sh` before building. See [Installation](installation.md).

### Unknown command "PROBE_EDDY_NG_TAP" (or another eddy-ng command)

Either `install.sh` didn't run successfully, or there's no `[probe_eddy_ng ...]` section in your config. On Klipper, if you updated eddy-ng from an older version with `git pull`, run `./install.sh` again.

### "x_offset and y_offset are both 0.0"

Set `x_offset` and `y_offset` to the sensor's position relative to the nozzle.

### "only tap_mode 'butter' is supported"

The old `wma` tap mode has been removed. Remove `tap_mode` from your config, and `tap_threshold` too if you'd tuned it for `wma`.

### "calibration data invalid, please recalibrate"

The saved calibration is from an eddy-ng version whose calibration format can't be loaded. Run `PROBE_EDDY_NG_SETUP` again.

### Setup can't find homing or tap values

- If setup fails entirely (no homing drive current), try moving the sensor and running setup again, or calibrate by hand with `PROBE_EDDY_NG_CALIBRATE`.
- If setup finds a homing drive current but not a tap one, look for one with `PROBE_EDDY_NG_TEST_DRIVE_CURRENT` (see [Advanced](advanced.md#finding-drive-currents-by-hand)):
  - Homing ideally needs valid readings across the whole 0–15mm range, but something like 1–10mm is fine. Calibrate it with `PROBE_EDDY_NG_CALIBRATE DRIVE_CURRENT=<n> USE_FOR=homing`.
  - Tap needs valid readings from a few hundredths of a mm up to at least `tap_start_z` (3mm). Calibrate it with `PROBE_EDDY_NG_CALIBRATE DRIVE_CURRENT=<n> USE_FOR=tap`.
  - If the range starts too high, try a drive current one higher; if it ends too low, one lower.
- If no drive current works, move the sensor. If the lowest readable height is too high, move the sensor 1mm higher.

## Homing and tap

### Sensor errors during homing

```
Error during homing z: Sensor error (ldc status: UNREADCONV1 ERR_ALE)
```

This is usually caused by either:

- the coil mounted too high or too low relative to the nozzle, or
- the wrong drive current for the coil.

The BTT Eddy defaults to drive current 15. Other probes (Cartographer, Mellow Fly) have their own defaults that generally shouldn't change; if you see this error with one of them, try mounting the sensor 1mm higher.

With a BTT Eddy, a slightly higher drive current can help, but it can also cause errors at the other end of the range. See [Setup can't find homing or tap values](#setup-cant-find-homing-or-tap-values).

If the sensor occasionally reports a single error in the middle of good readings, `max_errors` lets homing and tap ignore that many errors in a row.

### Sensor errors during tap

These have the same causes as errors during homing. If they only happen during tap, use a different drive current for tap only (`USE_FOR=tap`), as above.

### "Probe completed movement before triggering"

The toolhead reached `tap_target_z` (-0.250 by default) without detecting a tap. Either:

1. the tap threshold is too high to detect the tap in time, or
2. the calibration is off, and the real Z=0 is below `tap_target_z`.

To check the second: put paper under the nozzle, move to Z=0, then step down by 0.1 until the paper can't move. If that's below `tap_target_z`, you can redo calibration, or you can set a lower `tap_target_z`; but be aware that this increases the amount of nozzle-build plate contact.

Otherwise, look at the [tap graph](tap.md#tap-graph) to see what's happening with the threshold.

### "Tap detected too close to target z"

`tap_target_z` is where each tap is told to move to, and the move stops when contact is detected. It acts as a final safety stop: "this is the lowest position the toolhead is told to move to, so if something bad happens, we're not going to keep grinding the toolhead into the plate". Set it so that if the toolhead gets moved to this Z coordinate, it will absolutely have made solid contact with the build plate before it gets there.

Tap detects contact as the toolhead slowing down while the steppers keep moving. But the toolhead also slows down at the end of every move. A tap detected right at the end of the move can't be told apart from that deceleration, so it's rejected.

Move the toolhead to `tap_target_z` by hand and check:

- **No contact at all:** homing or calibration is off. Check Z=0 with paper, and measure the gap at Z=2. If it's off by more than 0.2–0.3mm, recalibrate.
- **Light contact:** lower `tap_target_z` (or pass `TARGET_Z=`) by 0.1 so there's room to make contact before the move ends.
- **Solid contact:** the tap isn't being detected. Raising `tap_speed` by 0.5–1mm/s may help. Lowering the threshold may also help, but can cause early triggers. Check the [tap graph](tap.md#tap-graph).

### Tap stops with the nozzle in the air

The threshold is too low and triggers before contact, or there's filament on the nozzle. Clean the nozzle, and check the [tap graph](tap.md#tap-graph).

### "Tap failed: stddev ... > ..."

The taps weren't consistent enough. Check that the nozzle is clean and the bed and toolhead are solid, look at the [tap graph](tap.md#tap-graph), and if needed adjust `tap_samples`, `tap_max_samples` or `tap_samples_stddev`.

## Profiles

### "Tap profile ... uses drive current N, which plate ... isn't calibrated for"

The tap profile sets a drive current the plate has no calibration for. Load a different tap profile, or calibrate the plate for that drive current with `PROBE_EDDY_NG_CALIBRATE DRIVE_CURRENT=<n>`.

### SAVE_CONFIG fails with "conflicts with included value"

Klipper's `SAVE_CONFIG` can't save a value that's also set in an `[include]`d file, and saves nothing until that's fixed. The error names the section and option. Remove that option from the included file, or move the section to `printer.cfg`. On Kalico, `[danger_options] autosave_includes: True` avoids this. See [Saving and include files](profiles.md#saving-and-include-files) for which commands save which values.

## Other

### "EDDYng: Already sampling!"

A previous operation didn't stop the sensor. Run `FIRMWARE_RESTART`. If this happens again, please file a bug describing what you did before it.
