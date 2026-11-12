# Calibration

The sensor must be calibrated before it can home or tap. Calibration maps the sensor's frequency readings to heights, separately for each drive current used.

If you use [plate profiles](profiles.md), calibration always applies to the active plate.

## Check the sensor

Run `PROBE_EDDY_NG_STATUS` (or `PES`) a few times. A working sensor gives something like:

```
Last coil value: 3189314.38 (-infmm) raw: 0x4409e8b (Not calibrated) status: 0x48 UNREADCONV1 DRDY
```

If the raw value is `0xffffffff` or similar, eddy-ng can't talk to the sensor. Check your wiring and config, and look in `klippy.log`.

## Automatic setup

`PROBE_EDDY_NG_SETUP` finds working drive currents for homing and tap and calibrates them.

1. Home X and Y: `G28 X Y`.
2. Heat the bed to 50–60°C. Setup can otherwise pick settings that work cold but fail once the bed is warm.
3. Move the nozzle to the center of the bed and run `PROBE_EDDY_NG_SETUP`.
4. Do the paper test: lower the nozzle with `TESTZ Z=...` until it just grips a sheet of paper, then `ACCEPT`. "Good enough" is fine.
5. Wait while setup calibrates each drive current in the sensor's range.

Setup picks the drive current with the widest usable range for homing (needs 0.5mm to 5mm) and for tap (needs 0.025mm to 3mm); these may be the same. It saves both calibrations and un-homes Z. If it can't find a tap drive current, homing still works; see [Troubleshooting](troubleshooting.md#setup-cant-find-homing-or-tap-values).

## Automatic setup without a paper test

`PROBE_EDDY_NG_AUTO_SETUP` does the same as `SETUP`, but finds Z=0 itself, by touching the bed with the nozzle. It works without a calibration and with Z not homed.

1. Clean the nozzle. Filament on the nozzle makes Z=0 too high, just like it does for [tap](tap.md).
2. Home X and Y, and heat the bed, as for `SETUP`.
3. Move the nozzle to the center of the bed, anywhere above it, and run `PROBE_EDDY_NG_AUTO_SETUP`.

   ***⚠️ Watch your printer the first time and be ready to hit the e-stop or pull the power in case something goes wrong!***

Auto setup:

1. Raises the toolhead 5mm (`LIFT=`), then moves it down in steps, reading the sensor while the toolhead is still. The steps start small and grow to 2mm while the sensor doesn't see the bed yet, and are 0.2mm once it does.
2. Stops when a step raises the sensor's frequency by much less than the steps before it. Until the nozzle touches the bed, each step down raises the frequency more than the last one. Once it touches, the toolhead can't get any closer. The nozzle presses into the bed by at most about one step.
3. Measures the tap threshold. It taps 3 times without one, so these taps don't stop at contact; each pushes into the bed by a few tenths of a millimeter (more at a higher `tap_speed`). It sets `tap_threshold` to half the weakest tap's signal, for the active tap profile. To use a threshold you already have instead, pass `THRESHOLD=`. If `tap_threshold` is set in your config files, `SAVE_CONFIG` can't save a new value for it, so auto setup only uses the measured one until Klipper restarts and tells you to change your config files.
4. Taps from just above the contact, like `PROBE_EDDY_NG_TAP`, to find Z=0 accurately. Z=0 includes `tap_adjust_z`. If a tap doesn't trigger, auto setup stops instead of trying again: the bed is known to be there, so the threshold is too high.
5. Calibrates drive currents like `SETUP`.

Z stays homed afterwards. Then test the calibration as described below, and `SAVE_CONFIG`, which also saves the measured `tap_threshold`.

If auto setup reports that it found no contact, check that both the nozzle and the sensor are over the bed. If its tap doesn't agree with where it found contact, clean the nozzle and try again. On printers where the sensor keeps getting closer to the bed while the nozzle presses into it (a very flexible toolhead mount), contact can't be detected this way; use `SETUP`.

## Manual calibration

Use `PROBE_EDDY_NG_CALIBRATE` if setup fails, or to calibrate a specific drive current.

1. Home X and Y and warm the bed as above.
2. Move the nozzle to a convenient spot and run `PROBE_EDDY_NG_CALIBRATE`:
   - `DRIVE_CURRENT=<n>`: the drive current to calibrate. Default: the current homing drive current.
   - `USE_FOR=homing`, `USE_FOR=tap` or `USE_FOR=homing,tap`: make the active plate use this drive current for homing, tap, or both. Without it, the plate keeps the drive currents it had, and the new calibration is only used if one of them is this drive current.
3. Do the paper test, then `ACCEPT`.

The result looks like:

```
DC 15: valid range 0.025 to 14.950, spread 2.12% (freq 3011234.0 to 3075681.0), fit 0.003
```

- **valid range** is the range of heights the sensor could read. A BTT Eddy often doesn't reach quite down to 0; that's fine.
- **spread** is how much the frequency changes over that range. Very low values (under 0.3%) usually mean the sensor is mounted too high.
- **fit** is the error of the height fit, in mm.

Calibration writes `/tmp/eddy-calibration.csv` and, with plotly installed, an interactive graph in `/tmp/eddy-calibration.html`.

For example, to calibrate drive current 16 and use it for tap:

```
PROBE_EDDY_NG_CALIBRATE DRIVE_CURRENT=16 USE_FOR=tap
```

The drive currents are saved with the plate's calibration. For the `default` plate, you can also set `reg_drive_current` and `tap_drive_current` in `[probe_eddy_ng ...]` instead.

## Test before saving

Calibration is staged but not saved yet. First check it:

1. Run `PROBE_EDDY_NG_PROBE_STATIC` (`PEPS`). You should get a plausible height.
2. Home Z.

   > ⚠️ Homing uses the "virtual Z endstop" feature. Make sure that your Z stepper's endstop is set to `endstop_pin: probe:z_virtual_endstop`, otherwise homing will not use the eddy device and you may experience a nozzle crash if there is no other functioning endstop.

   > ⚠️ You must have `safe_z_home` or a `homing_override` set up to move your toolhead to be over the bed before doing a Z home. The sensor needs metal underneath it, and if you try to home at 0,0 you'll have a bad time depending on the location of your sensor. See [Safety](safety.md#safe-homing).

   ***⚠️ Watch your printer the first time and be ready to hit the e-stop or pull the power in case something goes wrong! If homing fails, your printer will keep trying to push your toolhead through your build plate.***

   ```
   G28 Z
   ```

3. Move to Z=2 and run `PEPS` again. It should read close to 2.0, within ±0.025mm or so. Try a few heights in the 1–3mm range. If it's far off, recalibrate.
4. Run `SAVE_CONFIG`.

   > ⚠️ On Klipper, `SAVE_CONFIG` fails with "conflicts with included value" if `[probe_eddy_ng ...]` is in an `[include]`d file that sets `reg_drive_current` or `tap_drive_current`, and calibration picked a different value. Remove those options from the included file (calibration saves them), or move the section to `printer.cfg`. See [Saving and include files](profiles.md#saving-and-include-files).

If homing fails with sensor errors, see [Troubleshooting](troubleshooting.md#sensor-errors-during-homing).

`PROBE_EDDY_NG_CALIBRATION_STATUS` shows the active plate's calibrated drive currents and their ranges.

## Final checks

1. `PROBE_EDDY_NG_PROBE_STATIC` is accurate from 1mm to 3mm, and close up to 5mm.
2. `PROBE_EDDY_NG_PROBE_ACCURACY` reports small deviations.
3. Make sure you have `QUAD_GANTRY_LEVEL` or `Z_TILT_ADJUST` set up according to the instructions in [Safety](safety.md#quad-gantry-leveling) to avoid potential nozzle crashes, then check that it succeeds.
4. `BED_MESH_CALIBRATE METHOD=rapid_scan` succeeds.

Next: [Tap](tap.md).
