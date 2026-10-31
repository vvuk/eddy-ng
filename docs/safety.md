# Safety

Unlike many other probes, Eddy current probes are able to take a direct sensor reading instead of needing to move up/down in order to "trigger" a sensor. There are three requirements:

1. The probe's coil must be over the bed.
2. The bed must contain a uniform metallic material (i.e. the spring steel in flexible sheets).
3. If the bed has embedded magnets, they must be uniform and weak (i.e. a magnetic sticker, and not separate strong magnets).

If any of the above isn't true, the probe will not be able to read the height correctly, and damage may result.

Homing and homing-like operations (`QUAD_GANTRY_LEVEL`, `Z_TILT_ADJUST`) need adjustment in order to perform safely.

## Safe homing

> ⚠️ Homing uses the "virtual Z endstop" feature. Make sure that your Z stepper's endstop is set to `endstop_pin: probe:z_virtual_endstop`, otherwise homing will not use the eddy device and you may experience a nozzle crash if there is no other functioning endstop.

> ⚠️ The probe must be over the bed when you home Z. Ensure you have `safe_z_home`, `homing_override`, or some other setup to move your sensor to be over the bed before Z is homed. The sensor needs metal underneath it. If the sensor is not over the bed, the toolhead may not stop and damage may result.

See [Macros](macros.md#homing) for a suggested homing sequence.

## Bed mesh

Use `BED_MESH_CALIBRATE METHOD=rapid_scan` on both Klipper and Kalico. On Kalico this needs a version that supports `METHOD=rapid_scan` (October 2026 or newer).

`bed_mesh` will be most accurate if the scan is done at Z=2.0mm (the default `home_trigger_height` for eddy-ng). In the `[bed_mesh]` configuration section, you should set `horizontal_move_z: 2.0`.

Everything `[bed_mesh]` supports works as usual, including `ADAPTIVE=1`, faulty regions, `zero_reference_position` and axis twist compensation. `SCAN_SPEED=` overrides the scan speed.

There is no retraction and Z movement with eddy probes, as reading the height is done directly. There is no advantage to moving the toolhead up and down when probing with Eddy current probes; in fact, it likely reduces precision.

## Quad gantry leveling

> 🛑 Doing a QGL at 2mm height with a highly skewed gantry is dangerous, because there may be more than 2mm of skew, and the nozzle will crash into the bed. Set up QGL as described below before running it.

### Kalico

Kalico includes "adaptive height" support for QGL, which will start the sensor up higher, and then move it down in subsequent rounds as the gantry becomes more and more aligned. Add `min_horizontal_move_z` and `adaptive_horizontal_move_z` to your `quad_gantry_level` section:

```
[quad_gantry_level]
horizontal_move_z: 10.0
min_horizontal_move_z: 2.0
adaptive_horizontal_move_z: true
...  # the rest of your quad_gantry_level section
```

### Klipper

For the most accurate results, leveling with the probe should take place at 2.0mm (the default `home_trigger_height` for eddy-ng). In the `[quad_gantry_level]` configuration section, you should set `horizontal_move_z: 2.0`.

However, doing a QGL at 2mm height with a highly skewed gantry is dangerous, because there may be more than 2mm skew. To safely perform a QGL, add a macro that first performs a QGL at a higher "safe" height, and then moves down to 2mm.

```
[quad_gantry_level]
...  # the rest of your quad_gantry_level section
horizontal_move_z: 2.0

[gcode_macro QUAD_GANTRY_LEVEL]
rename_existing: _QUAD_GANTRY_LEVEL
gcode:
    SAVE_GCODE_STATE NAME=STATE_QGL
    BED_MESH_CLEAR
    # If QGL has not been done yet, correct for any major skew first
    # at 8mm height
    {% if not printer.quad_gantry_level.applied %}
    _QUAD_GANTRY_LEVEL HORIZONTAL_MOVE_Z=8 RETRY_TOLERANCE=1
    {% endif %}
    # Complete with a full QGL at the configured height
    _QUAD_GANTRY_LEVEL
    RESTORE_GCODE_STATE NAME=STATE_QGL
```

## Z tilt adjust

> 🛑 Doing a Z tilt adjust at 2mm height with a highly skewed axis is dangerous, because there may be more than 2mm of skew, and the nozzle will crash into the bed. Set up Z tilt as described below before running it.

### Kalico

Kalico includes "adaptive height" support for Z Tilt Adjust, which will start the sensor up higher, and then move it down in subsequent rounds as the axis becomes more and more aligned. Add `min_horizontal_move_z` and `adaptive_horizontal_move_z` to your `z_tilt` (or `z_tilt_ng`) section:

```
[z_tilt]
horizontal_move_z: 10.0
min_horizontal_move_z: 2.0
adaptive_horizontal_move_z: true
...  # the rest of your z_tilt section
```

### Klipper

Like `quad_gantry_level`, this adjustment will take place at whatever `horizontal_move_z` is set to. If Z Tilt has not been done yet, using too low of a value may cause the toolhead to crash into the bed if there is significant skew. The following macro will first do a coarse tilt adjust at a safe 8mm height, and then a fine-tuned one at 2mm.

```
[z_tilt]
...  # the rest of your z_tilt section
horizontal_move_z: 2.0

[gcode_macro Z_TILT_ADJUST]
rename_existing: _Z_TILT_ADJUST
gcode:
    SAVE_GCODE_STATE NAME=STATE_Z_TILT
    BED_MESH_CLEAR
    # If Z tilt has not been done yet, correct for any major skew first
    # at 8mm height
    {% if not printer.z_tilt.applied %}
    _Z_TILT_ADJUST HORIZONTAL_MOVE_Z=8 RETRY_TOLERANCE=1
    {% endif %}
    # Complete with a full Z tilt adjust at 2mm
    _Z_TILT_ADJUST HORIZONTAL_MOVE_Z=2
    RESTORE_GCODE_STATE NAME=STATE_Z_TILT
```
