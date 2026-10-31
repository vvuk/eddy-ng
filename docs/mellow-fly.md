# Mellow Fly

Mellow toolboards with a built-in LDC1612, such as the Fly SHT36 Pro, work with eddy-ng over USB or CAN. This page also applies to other CAN toolboards with an onboard LDC1612.

It assumes your toolboard is already connected and working. For CAN setup, see the [Esoterical CANBus guide](https://canbus.esoterical.online/Getting_Started.html). Build the toolboard firmware with eddy-ng included (see [Installation](installation.md)).

## Mounting

Mount the board so the sensor is about 5–6mm above the nozzle tip. Route cables to keep electrical noise away from the sensor lines.

## Configuration

```
[mcu SHT36]
canbus_uuid: ...   # from canbus_query.py

[temperature_sensor fly_eddy]
sensor_type: Generic 3950
sensor_pin: SHT36:gpio28   # the thermistor on the LDC1612

[probe_eddy_ng fly_eddy]
sensor_type: mellow_fly
i2c_mcu: SHT36
i2c_bus: i2c1e
i2c_address: 43
i2c_speed: 4000000
x_offset: 0
y_offset: 27.8
tap_target_z: -0.350
```

- `sensor_type: mellow_fly` selects the Fly's clock settings and drive current range (14–17, default 15).
- `i2c_address: 43` is the onboard LDC1612's usual address, on bus `i2c1e`.
- `tap_target_z: -0.350` lets a tap move a bit further down than the default -0.250 before giving up. Only lower it if taps fail with "Probe completed movement before triggering" (see [Troubleshooting](troubleshooting.md)).
- Set `x_offset`/`y_offset` to your sensor's position relative to the nozzle.

Then set up [Safety](safety.md) and [Macros](macros.md), and go to [Calibration](calibration.md).

## Notes

- The Fly usually works with drive current 15, which reads from about 0 to 15mm.
- If the lowest height the sensor can read is unusually high, check the mounting height.
- A CAN bus needs a 120Ω terminating resistor at each end. With the printer off and everything connected, you should measure 60Ω between CAN H and CAN L.
