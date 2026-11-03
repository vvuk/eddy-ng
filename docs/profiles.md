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
- Loading a plate clears the gcode Z offset that the last tap applied, along with the tap offset used by bed meshes. This happens even if the plate is already active, so loading the plate in your print start macro always starts the print from the same state.
- Loading a different plate also un-homes Z, since Z was homed with the old plate's calibration. Loading the plate that's already active keeps Z homed.

> ⚠️ **eddy-ng doesn't remember which plate is active.** After every restart, including the one after `SAVE_CONFIG`, the active plate is the one named by `default_plate:` in `[probe_eddy_ng ...]`, or `default` if that isn't set. If you swapped plates and restarted, homing and probing use the wrong plate's calibration until you load the right one. Load the plate you're using in your print start macro, before homing (see [Printing](printing.md#start-print)).

Plates are stored in `[probe_eddy_ng_plate <sensor> <plate>]` sections in the `SAVE_CONFIG` block at the end of `printer.cfg`.

## Tap profiles

You can create tap profiles with `PROBE_EDDY_NG_TAP_PROFILE CREATE=` (see below), or write them in your config. Only `tap_adjust_z` is required:

```
[probe_eddy_ng_tap textured]
tap_threshold: 220
tap_drive_current: 17
tap_adjust_z: 0.000          # the default
tap_adjust_z_pla: 0.020      # used with MATERIAL=PLA
tap_adjust_z_petg: 0.040
```

> ⚠️ **On Klipper, write tap profiles in `printer.cfg` itself, not in an `[include]`d file.** `PROBE_EDDY_NG_SET_TAP_ADJUST_Z` and `Z_OFFSET_APPLY_PROBE` save `tap_adjust_z` values through `SAVE_CONFIG`, and Klipper's `SAVE_CONFIG` fails with "conflicts with included value" if a value it's saving is also set in an included file. Until you remove the value from the included file, nothing else can be saved either, including calibrations. On Kalico, set `[danger_options] autosave_includes: True` and tap profiles can live in included files. See [Saving and include files](#saving-and-include-files).

A tap profile can set `tap_threshold`, `tap_speed`, `tap_samples`, `tap_max_samples`, `tap_max_consecutive_failures`, `tap_samples_stddev`, `tap_use_median`, `tap_time_position`, `tap_start_z`, `tap_target_z`, `tap_butter_lowcut`, `tap_butter_highcut`, `tap_butter_order`, `tap_position`, `reg_drive_current` and `tap_drive_current`. Settings it doesn't set come from `[probe_eddy_ng ...]`.

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
- Like plates, the active tap profile and material aren't remembered: after a restart, the `default` tap profile is active, with no material. Load the tap profile in your print start macro, before tapping.

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

These commands change values that `SAVE_CONFIG` then writes to the end of `printer.cfg`:

| Command | Saves |
|---|---|
| `PROBE_EDDY_NG_SETUP`, `PROBE_EDDY_NG_CALIBRATE`, `PROBE_EDDY_NG_CLEAR_CALIBRATION` | the calibration and `reg_drive_current`/`tap_drive_current`, in `[probe_eddy_ng ...]` (the `default` plate) or the active plate's section |
| `PROBE_EDDY_NG_SET_TAP_ADJUST_Z`, `Z_OFFSET_APPLY_PROBE` | `tap_adjust_z` or `tap_adjust_z_<material>`, in `[probe_eddy_ng ...]` (the `default` tap profile) or the active tap profile's section |
| `PROBE_EDDY_NG_PLATE_PROFILE CREATE=`, `PROBE_EDDY_NG_TAP_PROFILE CREATE=` | the whole new profile section |

When one of these values is also set in your config files:

- **In `printer.cfg` itself:** `SAVE_CONFIG` comments out the old line and saves the new value at the end of the file. This always works.
- **In an `[include]`d file, on Klipper:** `SAVE_CONFIG` fails with "conflicts with included value", and saves nothing, until you remove the value from the included file.
- **In an `[include]`d file, on Kalico:** `SAVE_CONFIG` fails only if the values differ. With `[danger_options] autosave_includes: True`, Kalico updates the included file instead, and it always works.

So on Klipper, keep `[probe_eddy_ng ...]` and your tap profiles in `printer.cfg`, or leave out of your included files the options listed above, and let the commands save them.

`REMOVE=` only removes profiles stored in the `SAVE_CONFIG` block. Profiles you wrote in your config files have to be removed there.

## Status

`printer["probe_eddy_ng <sensor>"]` has `active_plate`, `active_tap_profile` and `active_material`, plus `plates` (a list of names) and `tap_profiles` (each profile's materials).
