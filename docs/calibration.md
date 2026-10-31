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

## Manual calibration

Use `PROBE_EDDY_NG_CALIBRATE` if setup fails, or to calibrate a specific drive current.

1. Home X and Y and warm the bed as above.
2. Move the nozzle to a convenient spot and run `PROBE_EDDY_NG_CALIBRATE DRIVE_CURRENT=<n>`. Without `DRIVE_CURRENT`, it calibrates the current homing drive current.
3. Do the paper test, then `ACCEPT`.

The result looks like:

```
DC 15: valid range 0.025 to 14.950, spread 2.12% (freq 3011234.0 to 3075681.0), fit 0.003
```

- **valid range** is the range of heights the sensor could read. A BTT Eddy often doesn't reach quite down to 0; that's fine.
- **spread** is how much the frequency changes over that range. Very low values (under 0.3%) usually mean the sensor is mounted too high.
- **fit** is the error of the height fit, in mm.

Calibration writes `/tmp/eddy-calibration.csv` and, with plotly installed, an interactive graph in `/tmp/eddy-calibration.html`.

If you calibrate a drive current other than the homing one, set it as `tap_drive_current` (or `reg_drive_current` for homing) in your config.

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

If homing fails with sensor errors, see [Troubleshooting](troubleshooting.md#sensor-errors-during-homing).

`PROBE_EDDY_NG_CALIBRATION_STATUS` shows the active plate's calibrated drive currents and their ranges.

## Final checks

1. `PROBE_EDDY_NG_PROBE_STATIC` is accurate from 1mm to 3mm, and close up to 5mm.
2. `PROBE_EDDY_NG_PROBE_ACCURACY` reports small deviations.
3. Make sure you have `QUAD_GANTRY_LEVEL` or `Z_TILT_ADJUST` set up according to the instructions in [Safety](safety.md#quad-gantry-leveling) to avoid potential nozzle crashes, then check that it succeeds.
4. `BED_MESH_CALIBRATE METHOD=rapid_scan` succeeds.

Then set up [Tap](tap.md).
