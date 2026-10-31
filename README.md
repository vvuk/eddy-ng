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

## Updating

Run a `git pull` and then run `./install.sh` again:

```
cd ~/eddy-ng
git pull
./install.sh
```

