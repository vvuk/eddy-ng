# Profiles

There are two kinds of profiles, and one of each is active at a time:

- **Plate profiles** hold the calibration for a build surface: the frequency-to-height mapping, and the homing and tap drive currents. Switch plates when you swap build plates.
- **Tap profiles** hold tap settings, a default `tap_adjust_z`, and optionally a `tap_adjust_z` per material. Load one before tapping.

The calibration and tap settings in `[probe_eddy_ng ...]` itself are the plate and tap profile named `default`. Until you create other profiles, nothing changes.

## Plate profiles

```
PROBE_EDDY_NG_PLATE_PROFILE CREATE=textured_pei          # create an empty plate and switch to it
PROBE_EDDY_NG_SETUP                                      # calibrate it
SAVE_CONFIG

PROBE_EDDY_NG_PLATE_PROFILE CREATE=textured_pei_2 COPY=1 # create a plate with the active plate's calibration
PROBE_EDDY_NG_PLATE_PROFILE LOAD=textured_pei            # switch plates
PROBE_EDDY_NG_PLATE_PROFILE REMOVE=old_plate
PROBE_EDDY_NG_PLATE_PROFILE                              # list plates
```

- `SETUP`, `CALIBRATE` and `CLEAR_CALIBRATION` always work on the active plate.
- `COPY=1` is useful for a plate that behaves like one you've already calibrated. Recalibrating the copy later doesn't change the original.
- Switching plates un-homes Z and clears the offsets from the last tap, since they came from the old plate's calibration.
- After a restart (including the one after `SAVE_CONFIG`), the plate named by `default_plate:` in `[probe_eddy_ng ...]` is active, or `default` if it isn't set. Load the plate you're using in your print start macro.

Plates are stored in `[probe_eddy_ng_plate <sensor> <plate>]` sections in the `SAVE_CONFIG` block at the end of `printer.cfg`.

## Tap profiles

You can write tap profiles in your config. Only `tap_adjust_z` is required:

```
[probe_eddy_ng_tap textured]
tap_threshold: 220
tap_drive_current: 17
tap_adjust_z: 0.000          # the default
tap_adjust_z_pla: 0.020      # used with MATERIAL=PLA
tap_adjust_z_petg: 0.040
```

A tap profile can set `tap_threshold`, `tap_speed`, `tap_samples`, `tap_max_samples`, `tap_samples_stddev`, `tap_use_median`, `tap_time_position`, `tap_start_z`, `tap_target_z`, `tap_butter_lowcut`, `tap_butter_highcut`, `tap_butter_order`, `reg_drive_current` and `tap_drive_current`. Settings it doesn't set come from `[probe_eddy_ng ...]`.

```
PROBE_EDDY_NG_TAP_PROFILE LOAD=textured MATERIAL=PETG
PROBE_EDDY_NG_TAP_PROFILE CREATE=hot          # create a tap profile with the default settings and switch to it
PROBE_EDDY_NG_TAP_PROFILE CREATE=hot COPY=1   # ... or start from a copy of the active tap profile
PROBE_EDDY_NG_TAP_PROFILE REMOVE=hot
PROBE_EDDY_NG_TAP_PROFILE                     # list tap profiles
```

- Without `COPY=1`, a new tap profile has the tap settings from `[probe_eddy_ng ...]` and a `tap_adjust_z` of 0.
- If the profile has no value for the material, the default `tap_adjust_z` is used, with a warning.
- `PROBE_EDDY_NG_SET_TAP_ADJUST_Z` and `Z_OFFSET_APPLY_PROBE` change the value for the loaded tap profile and material, and `SAVE_CONFIG` saves it (creating `tap_adjust_z_<material>` if needed).
- If a tap profile changes the homing drive current, loading it un-homes Z.
- A tap profile can only use drive currents that the active plate is calibrated for.
- After a restart, the `default` tap profile is active.

## Names

Profile and material names are case-insensitive, and spaces in them become underscores. Profile names can contain letters, digits, `_` and `-`; material names can also contain `+` and `.` (e.g. `ASA-GF`). So `MATERIAL="PLA Silk"` uses `tap_adjust_z_pla_silk`, and `LOAD="Textured PEI Plate"` loads the plate `textured_pei_plate`.

That lets you pass the slicer's bed type and filament type straight through. In OrcaSlicer's machine start G-code:

```
PRINT_START PLATE="{curr_bed_type}" MATERIAL="{filament_type[0]}"
```

With this in `PRINT_START`:

```
PROBE_EDDY_NG_PLATE_PROFILE LOAD="{params.PLATE}"
G28
; heat, soak, level, clean the nozzle
PROBE_EDDY_NG_TAP_PROFILE LOAD=textured MATERIAL="{params.MATERIAL}"
PROBE_EDDY_NG_TAP
```

Keep the quotes: without them, a name with spaces is cut off at the first space.

## Saving and include files

Klipper's `SAVE_CONFIG` refuses to save a value that's also set in an `[include]`d file. Put the per-material values you'll adjust from the console in `printer.cfg` itself, or let the commands create them. Kalico can update included files with `[danger_options] autosave_includes: True`.

`REMOVE=` only removes profiles stored in the `SAVE_CONFIG` block. Profiles you wrote in your config files have to be removed there.

## Status

`printer["probe_eddy_ng <sensor>"]` has `active_plate`, `active_tap_profile` and `active_material`, plus `plates` (a list of names) and `tap_profiles` (each profile's materials).
