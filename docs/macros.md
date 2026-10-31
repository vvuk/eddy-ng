# Macros

## Homing

> ⚠️ Use `safe_z_home` or a `homing_override` to move the probe over the bed before homing Z (see [Safety](safety.md#homing)).

A plain `G28 Z` works. For a slightly more precise home, re-read the height at the home trigger height after homing, e.g. in a `homing_override`:

```
G28 Z                                ; coarse home with the sensor
G90
G0 Z2 F1000                          ; to home_trigger_height, where the sensor is most accurate
G4 P1000
M400
PROBE_EDDY_NG_PROBE_STATIC HOME_Z=1  ; set Z to the height the sensor reads
G0 Z5 F1000
```

Either way, the tap before printing sets the precise Z.

## Start print

A typical print start:

1. If you use [plate profiles](profiles.md), load the plate: `PROBE_EDDY_NG_PLATE_PROFILE LOAD=...`.
2. Home (`G28`).
3. Heat and soak.
4. Level the gantry (QGL or Z tilt).
5. Clean the nozzle at about 150°C. Hotter works, but filament oozing from the nozzle throws off the tap.
6. Move to where you want to tap: the center of the bed, near the print, or anywhere else.
7. If you use [tap profiles](profiles.md#tap-profiles), load one: `PROBE_EDDY_NG_TAP_PROFILE LOAD=... MATERIAL=...`.
8. `PROBE_EDDY_NG_TAP`.
9. Clean the nozzle again at printing temperature, if needed.
10. Bed mesh (`BED_MESH_CALIBRATE METHOD=rapid_scan`).
11. Print.

Tap must come after leveling and before the bed mesh, because the mesh uses the sensor offset that tap measures.

See [Profiles](profiles.md#names) for a `PRINT_START` that takes the plate and material from the slicer.

## End print

The tap offset is only valid for the print it was measured for. Clear it at the end of each print:

```
PROBE_EDDY_NG_SET_TAP_OFFSET VALUE=0
```

If you build an adaptive mesh for each print, also `BED_MESH_CLEAR`.
