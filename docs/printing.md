# Printing

Once [calibration](calibration.md) and [tap](tap.md) work, set up your print start and end macros.

## Start print

A typical print start:

1. If you use [plate profiles](profiles.md#plate-profiles), load the plate: `PROBE_EDDY_NG_PLATE_PROFILE LOAD=...`.
2. Home (`G28`).
3. Heat and soak.
4. Level the gantry (QGL or Z tilt), set up as in [Safety](safety.md#quad-gantry-leveling).
5. Clean the nozzle at about 150°C. Hotter works, but filament oozing from the nozzle throws off the tap.
6. Move to where you want to tap: the center of the bed, near the print, or anywhere else.
7. If you use [tap profiles](profiles.md#tap-profiles), load one: `PROBE_EDDY_NG_TAP_PROFILE LOAD=... MATERIAL=...`.
8. `PROBE_EDDY_NG_TAP`.
9. Clean the nozzle again at printing temperature, if needed.
10. Bed mesh: `BED_MESH_CALIBRATE METHOD=rapid_scan`.
11. Print.

Order matters:

- Load the plate before homing, because loading a different plate un-homes Z.
- Tap after leveling, because leveling changes the Z of the tap position.
- Tap before the bed mesh, because the mesh uses the [tap offset](#the-tap-offset) the tap measures.

[Profiles](profiles.md#names) has a `PRINT_START` that takes the plate and material from the slicer.

## Bed mesh

Use `BED_MESH_CALIBRATE METHOD=rapid_scan` on both Klipper and Kalico. The probe reads continuously while the toolhead moves over the bed, which is much faster than stopping at each point. On Kalico, this needs a version that supports `METHOD=rapid_scan` (October 2026 or newer).

Everything `[bed_mesh]` supports works as usual, including `ADAPTIVE=1`, faulty regions, `zero_reference_position` and axis twist compensation. `SCAN_SPEED=` overrides the scan speed.

The scan happens at `horizontal_move_z`; set it to `home_trigger_height` (2.0mm by default), as described in [Safety](safety.md#bed-mesh).

Without `METHOD=rapid_scan`, the mesh probes one point at a time, reading for `scan_sample_time` at each.

## The tap offset

The sensor's readings drift with temperature, so at print temperature, the height it reports is a little off. When you tap, eddy-ng also reads the sensor at `home_trigger_height` and records how far that reading is from the real height. This is the tap offset. Bed meshes and other probing correct every reading by it.

The tap offset is only right for the temperatures at the time of the tap. If it's left set, the next bed mesh at different temperatures (for example, the next print's mesh before its own tap, or a mesh on a cold printer) is shifted by the old offset. Loading a plate clears it, even if that plate is already active, so a print start macro that loads its plate also clears any stale tap offset.

## End print

Clear the tap offset at the end of each print:

```
PROBE_EDDY_NG_SET_TAP_OFFSET VALUE=0
```

If you build an adaptive mesh for each print, also `BED_MESH_CLEAR`.

Next, if you use more than one build plate or want different tap settings per filament: [Profiles](profiles.md).
