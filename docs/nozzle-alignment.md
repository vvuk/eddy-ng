# Nozzle alignment (experimental)

eddy-ng can align the nozzles of a toolchanger with an upward-facing Eddy current sensor ("bigfoot"). It has been tested with Cartographer and BTT Eddy sensors on a [Mad Max](https://github.com/zruncho3d/madmax) toolchanger, by one person, so expect rough edges.

## Requirements

- An Eddy current sensor such as a Cartographer or BTT Eddy, mounted facing up at (or slightly below) bed level, where every nozzle can reach it. Standalone sensors work best; a toolboard sensor would have to be mounted in a fixed spot.
- scipy (see [Installation](installation.md#install-plotly-and-scipy-recommended)).
- [klipper-toolchanger](https://github.com/viesturz/klipper-toolchanger), for `EDDYNG_SET_TOOL_OFFSET`.

Flash the sensor with eddy-ng firmware as for any other eddy-ng sensor.

## Configuration

Add a separate `[probe_eddy_ng ...]` section for this sensor, with `bigfoot: true`, alongside your normal Z probe:

```
[mcu bigfoot]
serial: /dev/serial/by-id/usb-Klipper_stm32f042x6_060001001143304146393320-if00
is_non_critical: true

[probe_eddy_ng bigfoot]
bigfoot: true
sensor_type: cartographer
i2c_mcu: bigfoot
i2c_bus: i2c1_PB6_PB7
bigfoot_scan_position: 188.0, 267.0, 2.0
```

`bigfoot_scan_position` is the X and Y of the center of the sensor, and the Z to scan the nozzle at. It's optional; without it, every scan needs `X`, `Y` and `Z`.

A nozzle alignment sensor isn't the Z probe, so its section doesn't need calibration, offsets or tap settings.

It defaults to `primary: false`, so commands without `SENSOR=` (and `LDC_NG_*` commands without `CHIP=`) still go to your Z probe. To use `PROBE_EDDY_NG_STATUS` or the `LDC_NG_*` commands on the nozzle alignment sensor, pass `SENSOR=bigfoot` (or `CHIP=bigfoot`), using your section's name.

## Commands

These work without `SENSOR=` as long as there's only one nozzle alignment sensor.

**`EDDYNG_NOZZLE_POSITION_SCAN`**: scan the current tool's nozzle over the sensor and report its center, and its offset from the reference.
- `X=`, `Y=`, `Z=`: where to scan. Default `bigfoot_scan_position`.
- `USE_LAST=1`: scan at the XY found by the last scan. Use it for a slower, smaller second pass.
- `RADIUS=`: how far from the center to scan. Default 5.0.
- `PASSES=`: how many passes to make. Default 3; more passes give more data.
- `SPEED=`: scan speed. Default `probe_speed`.
- `MOVE_SPEED=`: speed of the moves to the scan position. Default 100.
- `SCAN_Z_USE_PROBE=1`: use the last Z probe result as the scan's Z result. Otherwise it's 0.

**`EDDYNG_NOZZLE_POSITION_SET_REFERENCE`**: set the position that scans are measured against (offset 0, 0, 0).
- `X=`, `Y=`: default is the last scan result.
- `Z=` or `Z_USE_PROBE=1` (use the last Z probe result): one is required.

**`EDDYNG_SET_TOOL_OFFSET TOOL=<name>`**: set a klipper-toolchanger tool's G-code offset to the last scan's offset, or to `X=`, `Y=`, `Z=`.

The last result and offset are in `printer["probe_eddy_ng <name>"]` as `bigfoot_scan_result` and `bigfoot_scan_offset`.

## Example

A macro to align T1 to T0. `CENTER` stands for your own macro that moves the probe over the bed. It uses `ASSUME_ACTIVE_TOOL_Z_OFFSET`, an argument to `SELECT_TOOL` in vvuk's branch of klipper-toolchanger, which keeps `SELECT_TOOL` from changing the G-code offset so the offset just set is saved to the tool.

```
# start with T0 and clear its offsets
SELECT_TOOL T=0
SET_GCODE_OFFSET X=0 Y=0 Z=0
EDDYNG_SET_TOOL_OFFSET TOOL=T0 X=0 Y=0 Z=0
G28 Z

# scan T0 for the reference position
CENTER
PROBE                                         # Z probe, for the reference Z
EDDYNG_NOZZLE_POSITION_SCAN PASSES=5          # XY
EDDYNG_NOZZLE_POSITION_SET_REFERENCE Z_USE_PROBE=1

# scan T1 for its offset from T0
CENTER
SELECT_TOOL T=1 ASSUME_ACTIVE_TOOL_Z_OFFSET=0.0
PROBE
EDDYNG_NOZZLE_POSITION_SCAN PASSES=5 SCAN_Z_USE_PROBE=1
EDDYNG_SET_TOOL_OFFSET TOOL=T1

# back to T0 without changing offsets
SELECT_TOOL T=0 ASSUME_ACTIVE_TOOL_Z_OFFSET=0.0
```
