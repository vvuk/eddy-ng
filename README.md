# eddy-ng

> ***Note: October 2025 -- life has gotten quite busy lately, so I've been much slower to respond to issues and make updates. Apologies, will get back to it soon!***

eddy-ng improves the Eddy current probe support in Klipper to add accurate Z-offset setting by physically making contact with the build surface. These probes are very accurate, but suffer from drifts due to changes in conductivity in the target surface as well as changes in coil parameters as temperatures change. Instead of doing temperature compensation (which is guesswork at best), eddy-ng takes a more physical approach:

1. Calibration is performed at any temperature (cold).
2. Z-homing via the sensor happens using this calibration, regardless of current temperatures. This is a "coarse" Z-home -- it is not accurate enough for printing, but is sufficient for homing, gantry leveling, and other preparation.
3. A precise Z-offset is taken with a "tap" just before printing, with the bed at print temps and the nozzle warm (but not hot -- you don't want filament drooling or damage to your build plate).
4. At the same time as the tap, the difference between the actual height (now known after the tap) and what the sensor reads at that height is saved. This offset then gets taken into account when doing a bed mesh, because it indicates the delta (due to temperatures) between what height the sensor thinks it is vs. where it actually is.

This is a standalone `eddy-ng` repository, intended to be integrated into your own Klipper installation.

## Support

Questions? Come ask on the Sovol 3D Printers Discord at `https://discord.gg/Zg45rA52G7` in the eddy-ng forum. (Nothing Sovol-specific in `eddy-ng`, just where all this work started! You can also find the server via the Discover tab in Discord, then Sovol 3D Printers)

You can also file issues in [this `eddy-ng` github repo](https://github.com/vvuk/eddy-ng/issues).

## Installation

1. Clone this repository:

```
cd ~
git clone https://github.com/vvuk/eddy-ng
```

2. Run the install script:

```
cd ~/eddy-ng
./install.sh
```

(If your klipper isn't installed in `~/klipper`, provide the path as the first argument, i.e. `./install.sh ~/my-klipper`.)

3. Follow the rest of the full `eddy-ng` setup instructions that are [available in the wiki](https://github.com/vvuk/eddy-ng/wiki).

## Bed mesh

On both Klipper and Kalico, use the standard bed mesh command with the rapid scan method:

```
BED_MESH_CALIBRATE METHOD=rapid_scan
```

Everything `[bed_mesh]` supports works as usual, including `ADAPTIVE=1`, faulty regions, `zero_reference_position` and axis twist compensation. The scan runs at `horizontal_move_z`, so set it close to `home_trigger_height` (e.g. `horizontal_move_z: 2`) for the best accuracy. Use `SCAN_SPEED=` to override the speed. On Kalico, this needs a version that supports `METHOD=rapid_scan` (October 2026 or newer).

The old `EDDYNG_BED_MESH_EXPERIMENTAL` command has been removed.

## Probing with tap

On Klipper, probe commands accept `METHOD=tap` to probe by touching the bed with the nozzle, for example:

```
PROBE METHOD=tap
```

This taps at the current position, takes the same parameters as `PROBE_EDDY_NG_TAP` (`SAMPLES`, `THRESHOLD` or `TAP_THRESHOLD`, `TARGET_Z`, `ADJUST_Z`, ...), and reports the Z where the nozzle touched the bed plus `tap_adjust_z`. Unlike `PROBE_EDDY_NG_TAP`, it doesn't re-home Z or change the G-code offset, so it can be used to measure Z offsets yourself (e.g. per tool), or with other probe commands such as `QUAD_GANTRY_LEVEL METHOD=tap`. The result is available as `printer.probe.last_probe_position.z`.

## Profiles

There are two kinds of profiles, and one of each is active at a time:

- **Plate profiles** hold the calibration for a build surface (the frequency to height mapping and the drive currents `PROBE_EDDY_NG_SETUP` picked). Switch plate profiles when you swap build plates.
- **Tap profiles** hold tap settings (`tap_threshold`, `tap_speed`, `tap_samples`, `tap_max_samples`, `tap_samples_stddev`, `tap_use_median`, `tap_time_position`, `tap_start_z`, `tap_target_z`, `tap_butter_*`, and optionally `reg_drive_current`/`tap_drive_current`), plus a default `tap_adjust_z` and optional `tap_adjust_z` values per material. Load one before tapping.

The calibration and tap settings in `[probe_eddy_ng ...]` itself are the plate and tap profile named `default`, so nothing changes until you create profiles.

### Plate profiles

```
PROBE_EDDY_NG_PLATE_PROFILE CREATE=textured_pei   # create an empty plate and switch to it
PROBE_EDDY_NG_SETUP                               # calibrate the active plate
SAVE_CONFIG

PROBE_EDDY_NG_PLATE_PROFILE LOAD=textured_pei     # switch plates
PROBE_EDDY_NG_PLATE_PROFILE REMOVE=old_plate
PROBE_EDDY_NG_PLATE_PROFILE                       # list plates
```

`SETUP` and `CALIBRATE` always calibrate the active plate. Switching plates un-homes Z and clears the offsets from the last tap, since they came from the old plate's calibration. After a restart (including the one after `SAVE_CONFIG`), the plate named by `default_plate:` in `[probe_eddy_ng ...]` is active (or `default`), so load the plate you're using in your `PRINT_START` macro.

Plate profiles are stored in `[probe_eddy_ng_plate <sensor> <plate>]` sections in the `SAVE_CONFIG` block.

### Tap profiles

Write tap profiles in your config. Only `tap_adjust_z` is required:

```
[probe_eddy_ng_tap textured]
tap_threshold: 220
tap_drive_current: 17
tap_adjust_z: 0.000          # the default
tap_adjust_z_pla: 0.020      # tap_adjust_z for MATERIAL=PLA
tap_adjust_z_petg: 0.040
```

```
PROBE_EDDY_NG_TAP_PROFILE LOAD=textured MATERIAL=PETG
PROBE_EDDY_NG_TAP_PROFILE CREATE=hot      # copy the active tap profile and switch to the copy
PROBE_EDDY_NG_TAP_PROFILE REMOVE=hot
PROBE_EDDY_NG_TAP_PROFILE                 # list tap profiles
```

Material names are case-insensitive and can contain letters, digits, `_`, `-`, `+` and `.` (e.g. `ASA-GF`). Spaces are turned into underscores, so `MATERIAL="PLA Silk"` uses `tap_adjust_z_pla_silk`. If the profile has no value for the material, the default `tap_adjust_z` is used, with a warning. `PROBE_EDDY_NG_SET_TAP_ADJUST_Z` and `Z_OFFSET_APPLY_PROBE` change the value for the loaded tap profile and material, and `SAVE_CONFIG` saves it. If a tap profile changes the homing drive current, loading it un-homes Z. A tap profile can only use drive currents the active plate is calibrated for.

Klipper's `SAVE_CONFIG` refuses to save a value that's also set in an `[include]`d file, so set the per-material values you'll adjust from the console in `printer.cfg` itself, or let the commands create them. (Kalico can update included files with `[danger_options] autosave_includes: True`.) `REMOVE=` can only remove profiles created with `CREATE=`.

Profile names are case-insensitive, and spaces in them are turned into underscores, so a quoted name like `LOAD="Textured PEI Plate"` loads the plate `textured_pei_plate`. That lets you pass the slicer's bed type straight through, e.g. `PRINT_START PLATE="{curr_bed_type}" MATERIAL="{filament_type[0]}"` in OrcaSlicer, with this `PRINT_START`:

```
PROBE_EDDY_NG_PLATE_PROFILE LOAD="{params.PLATE}"
G28
; heat and soak
PROBE_EDDY_NG_TAP_PROFILE LOAD=textured MATERIAL="{params.MATERIAL}"
PROBE_EDDY_NG_TAP
```

The active plate, tap profile and material are available in `printer["probe_eddy_ng <sensor>"]` as `active_plate`, `active_tap_profile` and `active_material`.

## Updating

Run a `git pull` and then run `./install.sh` again:

```
cd ~/eddy-ng
git pull
./install.sh
```

