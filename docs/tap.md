# Tap

Tap moves the nozzle down onto the bed and detects the moment it stops getting closer: the sensor reading flattens out while the steppers keep moving. That's where the nozzle touched the bed.

Anything else that stops the nozzle also counts, especially filament on the nozzle tip, which gives a Z that's too high. Clean the nozzle before tapping (see [Printing](printing.md#start-print)).

## First tap

With X, Y and Z homed, and the nozzle clean:

```
PROBE_EDDY_NG_TAP
```

This taps 3 to 5 times at the current position (see `tap_samples` in the [configuration reference](configuration.md#tap)). Each tap moves down at 3mm/s toward `tap_target_z` (-0.250 by default), stopping as soon as contact is detected. This will make contact with your build plate, at most down to `tap_target_z`. The risk of build plate or nozzle damage is minimal, but if you're concerned you can experiment with `TARGET_Z=-0.100`. You can also specify `SAMPLES=1` to only do one sample.

When it succeeds, tap:

- re-homes Z so that Z=0 is `tap_adjust_z` above where the nozzle touched the bed, and resets the G-code Z offset to 0. With `HOME_Z=0`, it leaves the Z homing alone and sets the G-code Z offset instead.
- measures the sensor offset at `home_trigger_height`, which corrects later bed meshes and other probing for temperature drift (the "tap offset").

Z offset changes after the tap (babystepping, slicer G-code) work as usual.

If the tap fails, see [Troubleshooting](troubleshooting.md#homing-and-tap).

## Check the result

1. **Z=0.** Put a sheet of paper under the nozzle and move to Z=0 (or start at Z=0.5 and step down by 0.1). The paper should have a good amount of friction. If it's consistently too tight or too loose, adjust `tap_adjust_z` (below).
2. **Bed mesh.** Run `BED_MESH_CALIBRATE METHOD=rapid_scan`. The mesh at the tap position should be very close to 0 (±0.005).

## Adjusting tap Z

When the nozzle touches the bed, the printer flexes a little before the tap is detected, so the detected Z is usually slightly too low (rarely too high). The amount is consistent, so a fixed `tap_adjust_z` is added to every tap.

To find it: tap, move to Z=0 with paper under the nozzle, and raise Z until the friction feels right for a first layer (go above and come back down to take up backlash). That Z is your `tap_adjust_z`.

You can set it in three ways:

- `tap_adjust_z: 0.025` in your config.
- `PROBE_EDDY_NG_SET_TAP_ADJUST_Z VALUE=0.025` (or `ADJUST=0.005` to add to it), then `SAVE_CONFIG`.
- After a tap, babystep until the first layer looks right, then click the "save Z offset" button in Mainsail/Fluidd (which runs `Z_OFFSET_APPLY_PROBE`) and `SAVE_CONFIG`. eddy-ng replaces `Z_OFFSET_APPLY_PROBE` so that it updates `tap_adjust_z`.

> ⚠️ The last two save `tap_adjust_z` with `SAVE_CONFIG`. On Klipper, that fails if `tap_adjust_z` is also set in an `[include]`d file; see [Saving and include files](profiles.md#saving-and-include-files).

Different filaments often need different values. [Tap profiles](profiles.md#tap-profiles) store a `tap_adjust_z` per material, and the commands above update the value for the loaded material.

## Tuning

- **`tap_threshold`** (default 250): how far, in Hz, the filtered sensor signal must drop from its most recent peak to count as a tap. Lower values make shorter contact with the bed but risk triggering before contact. Use the [tap graph](#tap-graph) to pick the lowest value that doesn't trigger early, plus some margin. Try values with `PROBE_EDDY_NG_TAP THRESHOLD=...`. Different build plates may need different thresholds; [tap profiles](profiles.md) can hold them.

  How strong the tap signal is depends on the printer: `tap_speed` (roughly proportionally), the sensor and how high it's mounted, and how much the toolhead flexes, so there's no threshold that suits every printer. [`PROBE_EDDY_NG_AUTO_SETUP`](calibration.md#automatic-setup-without-a-paper-test) measures one. When a tap doesn't trigger, the error says how high the signal got, e.g. `signal peaked at 210, threshold 250`; if the nozzle touched the bed, use a threshold of about half that peak.
- **`tap_time_position`** (default 0.3): only with `tap_position: descent`. Where the tap is placed between the start of detection (0.0) and the moment the threshold was crossed (1.0). Values from 0.2 to 0.5 tend to be most consistent.
- **`tap_samples`, `tap_max_samples`, `tap_samples_stddev`**: tap takes up to `tap_max_samples` taps until any `tap_samples` of them are within `tap_samples_stddev` of each other, and uses their average (or median, with `tap_use_median: true`).
- **`tap_max_consecutive_failures`**: with a large `tap_max_samples`, stops early when taps keep failing, e.g. when the threshold is too high, instead of tapping the bed over and over. Unset, tap tries all `tap_max_samples`.
- **`tap_speed`** (default 3.0): faster taps can detect contact more clearly, but the toolhead keeps moving briefly after the trigger, so don't go much higher.
- **`tap_start_z`** (default 3.0): the height each tap starts from. The tap drive current must be able to read from here down to `tap_target_z`.

## Tap position

eddy-ng measures each tap two ways:

- **Lift-off** (the default, `tap_position: liftoff`): as the toolhead lifts off the bed after a tap, the sensor's reading stays nearly constant while the nozzle is still pressed against the bed, then changes once it leaves. The toolhead Z at that change is where the nozzle stopped touching the bed. Klipper's own eddy probe tap uses the same idea.
- **Descent** (`tap_position: descent`): where the tap was detected on the way down. This depends on the threshold, the tap speed, and how much the printer flexes, which is what `tap_time_position` and `tap_adjust_z` make up for. This was the only option in older versions of eddy-ng.

Every tap reports both positions, and a summary at the end compares them:

```
Tap 1: z=0.124 (descent 0.101, lift-off 0.124, lift-off - descent +0.023)
...
Tap positions (liftoff used): descent mean 0.103 stddev 0.0047 (3 taps); lift-off mean 0.124 stddev 0.0012 (3 taps); lift-off - descent +0.021 (stddev 0.0040)
Probe computed tap at 0.168 (tap at z=0.124, stddev 0.001) with 3 samples, ..., tap_adjust_z: 0.044 (would be 0.065 with descent)
```

A tap whose lift-off can't be found counts as a failed tap. To use the descent position instead, set `tap_position: descent`, in `[probe_eddy_ng ...]` or in a [tap profile](profiles.md#tap-profiles), or pass `POSITION=descent` to `PROBE_EDDY_NG_TAP` for a single tap.

### Updating from a version that used the descent position

The two positions are different measurements, so the switch to lift-off changes where Z=0 ends up, usually making it higher. If you tuned `tap_adjust_z` with an older version, either:

- set `tap_position: descent` to keep everything as it was, or
- tap, and change `tap_adjust_z` to the "would be" value at the end of the last line, which keeps the same Z=0 with lift-off. It's computed from that tap's own taps, so tap a few times and use a typical value. It's left out when there weren't enough consistent taps of the other kind to say. If you use per-material values, each one changes by the same amount.

Either way, check Z=0 with paper (see [Check the result](#check-the-result)) before printing.

The [tap graph](#tap-graph) shows the start of the lift (dotted gray line) and the lift-off Z (dashed green line).

## Tap graph

With plotly installed, tap can write an interactive graph to `/tmp/tap.html`: set `write_tap_plot: true` in your config, or pass `PLOT=1` to `PROBE_EDDY_NG_TAP`. `PLOT=2` (or `write_every_tap_plot: true`) writes one graph per tap, `/tmp/tap-1.html`, `/tmp/tap-2.html` and so on. Writing graphs takes a few seconds on slower hosts.

![tap graph](https://hackmd.io/_uploads/Sy4yNFVPJe.png)

Click legend entries to show or hide lines; scroll and zoom to look closer.

- **Z** is the height the sensor reports, and **KinZ** is where the steppers put the toolhead. Below 0, **Z (low)** is dashed because it's extrapolated past the calibration. At the calibration temperature, Z and KinZ are close, especially low down. When hot they can differ, usually by less than 1mm.
- **Freq** is the raw sensor frequency.
- **signal** is the band-pass filtered frequency the tap is detected from (needs scipy).
- **accum_mcu** is the drop from the last peak that the probe's MCU compared against the threshold (dashed gray line). **accum_py** is the same, computed on the host, and should match.
- The vertical lines mark the start of tap detection, the reported tap position (see `tap_time_position`) and the trigger. The horizontal line marks the tap Z.

At contact, Z flattens out while KinZ keeps going down. Here's a tap that never triggered (the threshold was set very high on purpose), where the blue Z line flattens out before the red KinZ line:

![untriggered tap graph](https://hackmd.io/_uploads/SJ21FvrDyg.png)


## Tap report

`PROBE_EDDY_NG_TAP REPORT=1` (or `write_tap_report: true` in your config) writes one page about all the taps of a `TAP` to `/tmp/tap-report.html`. It doesn't need plotly on the host; the page loads plotly.js from the internet when you open it.

- The top shows the tap Z, whether enough taps agreed (and if not, by how much they missed), and the settings used.
- **Tap positions** plots each tap's descent and lift-off Z, with mean ± 1σ bands. Filled markers are the taps that made up the tap Z; hollow ones were left out, and failed taps are marked with ✕. Beside it, the spread of each position with error bars.
- **Lift-off − descent** shows the gap between the two positions for each tap.
- **All taps, overlaid** lines up every tap at the moment it triggered: the tap signal against the threshold, and the sensor Z relative to each tap's descent Z, with the median across taps.
- **Individual taps** has a small graph of each tap: sensor Z and toolhead Z, with the tap signal and threshold below.
- **Tap data** lists every number for every tap, including the lift-off fit and why failed taps failed.

## Probing with tap

On Klipper, probe commands take `METHOD=tap` to probe by touching the bed with the nozzle:

```
PROBE METHOD=tap
```

This taps at the current position with the same parameters as `PROBE_EDDY_NG_TAP` (`SAMPLES`, `THRESHOLD` or `TAP_THRESHOLD`, `TARGET_Z`, `ADJUST_Z`, ...), and reports the Z where the nozzle touched the bed plus `tap_adjust_z`. Unlike `PROBE_EDDY_NG_TAP`, it doesn't re-home Z or change the G-code offset. Use it to measure Z offsets yourself (for example per tool), or with other probe commands, like `QUAD_GANTRY_LEVEL METHOD=tap`. The result is in `printer.probe.last_probe_position.z`.

Next: [Printing](printing.md).
