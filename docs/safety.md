# Safety

An Eddy current probe reads the distance to the bed directly; it doesn't need to move down until something triggers. That only works when:

1. the probe's coil is over the bed,
2. the bed contains a uniform metal layer (e.g. the spring steel of a flexible sheet), and
3. any magnets in the bed are uniform and weak (a magnetic sticker, not separate strong magnets).

If any of these isn't true, the probe will misread the height and the toolhead may crash into the bed.

Homing and anything that probes like homing (`QUAD_GANTRY_LEVEL`, `Z_TILT_ADJUST`) need some setup to be safe.

## Homing

- Set your Z endstop to the probe: `endstop_pin: probe:z_virtual_endstop` in the `[stepper_z]` section. Otherwise homing won't use the sensor.
- The probe must be over the bed when Z homes. Use `safe_z_home` or a `homing_override` to move it there first. If the sensor isn't over the bed, the toolhead may not stop.

See [Macros](macros.md#homing) for a suggested homing sequence.

## Bed mesh

Use `BED_MESH_CALIBRATE METHOD=rapid_scan` on both Klipper and Kalico. On Kalico this needs a version that supports `METHOD=rapid_scan` (October 2026 or newer).

The sensor is most accurate at `home_trigger_height` (2.0mm by default), so set `horizontal_move_z` in `[bed_mesh]` to match:

```
[bed_mesh]
horizontal_move_z: 2.0
...
```

Everything `[bed_mesh]` supports works as usual, including `ADAPTIVE=1`, faulty regions, `zero_reference_position` and axis twist compensation. `SCAN_SPEED=` overrides the scan speed.

There's no benefit to moving the toolhead up and down between points with an Eddy probe; it reads the height directly.

## Quad gantry leveling and Z tilt

Like bed meshes, these are most accurate at `horizontal_move_z: 2.0`. But on a badly skewed gantry, one corner can be more than 2mm lower than another, and the nozzle will hit the bed. Level at a safe height first, then at 2mm.

### Kalico

Kalico can do this automatically: it starts high and moves down as the gantry gets more level. Add `min_horizontal_move_z` and `adaptive_horizontal_move_z` to `[quad_gantry_level]` or `[z_tilt]` (or `[z_tilt_ng]`):

```
[quad_gantry_level]
horizontal_move_z: 10.0
min_horizontal_move_z: 2.0
adaptive_horizontal_move_z: true
...
```

### Klipper

Wrap the command in a macro that first levels coarsely at 8mm if leveling hasn't been done yet, then levels at `horizontal_move_z`:

```
[quad_gantry_level]
horizontal_move_z: 2.0
...

[gcode_macro QUAD_GANTRY_LEVEL]
rename_existing: _QUAD_GANTRY_LEVEL
gcode:
    SAVE_GCODE_STATE NAME=STATE_QGL
    BED_MESH_CLEAR
    {% if not printer.quad_gantry_level.applied %}
      _QUAD_GANTRY_LEVEL HORIZONTAL_MOVE_Z=8 RETRY_TOLERANCE=1
    {% endif %}
    _QUAD_GANTRY_LEVEL
    RESTORE_GCODE_STATE NAME=STATE_QGL
```

For Z tilt:

```
[z_tilt]
horizontal_move_z: 2.0
...

[gcode_macro Z_TILT_ADJUST]
rename_existing: _Z_TILT_ADJUST
gcode:
    SAVE_GCODE_STATE NAME=STATE_Z_TILT
    BED_MESH_CLEAR
    {% if not printer.z_tilt.applied %}
      _Z_TILT_ADJUST HORIZONTAL_MOVE_Z=8 RETRY_TOLERANCE=1
    {% endif %}
    _Z_TILT_ADJUST
    RESTORE_GCODE_STATE NAME=STATE_Z_TILT
```
