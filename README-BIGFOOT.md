# Experimental Eddy-based Nozzle Alignment

This branch (`bigfoot`) contains code for experimental nozzle alignment
using upwards-facing Eddy current probes. It's been tested with
Cartographer and BTT Eddy hardware on a [Mad Max](https://github.com/zruncho3d/madmax)
toolchanger. (It has been developed and tested by exactly one person though,
so YMMV for now.)

These are _very light_ documentation about how to use this. Real documentation
and eventual integration into mainline `eddy-ng` code will happen over the next
few weeks.

## Requirements

* Eddy current sensor, such as Cartographer or BTT Eddy. Standalone sensors
  work best. In theory you could use e.g. a Mellow Fly toolboard but you'd have
  to mount the toolboard in a fixed location.
* The sensor mounted facing _up_ at bed level (or slightly below it). Your nozzles
  should be able to move to the area directly above the sensor.

Install this `eddy-ng` branch into your Klipper/Kalico installation and flash
updated firmware to your sensor. (See the [main eddy-ng docs for some info about that](https://github.com/vvuk/eddy-ng).)

## Configuration

You'll need a separate `probe_eddy_ng` configuration for this sensor with `bigfoot` set
to `true`. You can also optionally specify `bigfoot_scan_position` which should be the
X, Y of the center of your sensor, and Z to be the height at which the nozzle scan
happens. Each of these values (XYZ) are able to be overridden in the SCAN command;
if `bigfoot_scan_position` is not present, XYZ are required to the command.

```

[mcu bigfoot]
serial: /dev/serial/by-id/usb-Bigfoot_stm32f042x6_060001001143304146393320-if00
is_non_critical: true

[probe_eddy_ng bigfoot]
bigfoot: true
sensor_type: cartographer
i2c_mcu: bigfoot
i2c_bus: i2c1_PB6_PB7
bigfoot_scan_position: 188.0, 267.0, 2.0

```

## Usage

There are three main commands:

`EDDYNG_NOZZLE_POSITION_SCAN`: Perform a scan of the current tool's nozzle and display the detected center. The offset is computed relative to the last set reference (see next command).

Arguments:
  * `X=`, `Y=`, `Z=`: XYZ position at which the scan should happen
  * `RADIUS=` radius from XYZ to use for scanning
  * `PASSES=5` number of passes to do. 5 is fine, the more passes the more data there is.
  * `SPEED=` the speed at which to do the scan.
  * `USE_LAST=1`: if specified, XYZ will be taken from the result of the last SCAN. This way, a second refinement pass can be done at a slower speed (and smaller radius) to get a more accurate result.
  * `Z_USE_PROBE=1`: if specified, the computed "Z" result will be taken from the last `PROBE` result. Otherwise, Z will be 0.

`EDDYNG_NOZZLE_POSITION_SET_REFERENCE`: Set the reference coordinate for scans, i.e. what will be treated as offset 0,0,0.

Arguments:
  * `X=`, `Y=`, `Z=`: XYZ values to use as the offset. If not specified, the last SCAN result is used.
  * `Z_USE_PROBE=1`: if specified, the computed "Z" result will be taken from the last `PROBE` result.

`EDDYNG_SET_TOOL_OFFSET`: Sets the `klipper-toolchanger` tool offset from the last SCAN offset. Tool is specified using `TOOL=`

You can experiment with `POSITION_SCAN` manually to see how this works; the result is printed out to the console.

## Macros

Here's a sample macro for 2-toolhead alignment. Note that this uses an added `ASSUME_ACTUVE_TOOL_Z_OFFSET` argument to `SELECT_TOOL` which is present in my branch of `klipper-toolchanger`: this tells `SELECT_TOOL` to not muck with the gcode offset when changing tools so that the offset we just set is properly saved to the tool.

```
  # always start with T0, and reset and re-home
  SELECT_TOOL T=0

  # reset all this
  SET_GCODE_OFFSET X=0 Y=0 Z=0
  EDDYNG_SET_TOOL_OFFSET TOOL=T0 X=0 Y=0 Z=0

  # re-home
  G28 Z
  _ADJUST_Z_HOME_FOR_TOOL_OFFSET # TODO: this should happen after every home but I don't want to deal with a homing_override atm

  # Scan T0 to get the base reference coordinate
  CENTER
  SELECT_TOOL T=0 # we should already be T0..
  PROBE # do a Z probe
  EDDYNG_NOZZLE_POSITION_SCAN PASSES=5 # Scan for XY
  # We have to use Z from probe here, because _ADJUST_Z_HOME_FOR_TOOL_OFFSET broke the probe values/Z values relationship,
  # and we need this value to be something we can relate to the next tool
  EDDYNG_NOZZLE_POSITION_SET_REFERENCE Z_USE_PROBE=1

  # Then scan T1 to get offsets from T0
  CENTER
  SELECT_TOOL T=1 ASSUME_ACTIVE_TOOL_Z_OFFSET=0.0
  PROBE
  EDDYNG_NOZZLE_POSITION_SCAN PASSES=5 Z_USE_PROBE=1
  EDDYNG_SET_TOOL_OFFSET TOOL=T1

  # Go back to T0 without touching offsets so we have a clean slate,
  # but with T1 and beyond offsets properly set. Required.
  SELECT_TOOL T=0 ASSUME_ACTIVE_TOOL_Z_OFFSET=0.0
```

