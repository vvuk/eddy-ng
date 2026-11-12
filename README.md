# eddy-ng

eddy-ng improves the Eddy current probe support in Klipper to add accurate Z-offset setting by physically making contact with the build surface. These probes are very accurate, but suffer from drifts due to changes in conductivity in the target surface as well as changes in coil parameters as temperatures change. Instead of doing temperature compensation (which is guesswork at best), eddy-ng takes a more physical approach:

1. Calibration is performed at any temperature (cold).
2. Z-homing via the sensor happens using this calibration, regardless of current temperatures. This is a "coarse" Z-home -- it is not accurate enough for printing, but is sufficient for homing, gantry leveling, and other preparation.
3. A precise Z-offset is taken with a "tap" just before printing, with the bed at print temps and the nozzle warm (but not hot -- you don't want filament drooling or damage to your build plate).
4. At the same time as the tap, the difference between the actual height (now known after the tap) and what the sensor reads at that height is saved. This offset then gets taken into account when doing a bed mesh, because it indicates the delta (due to temperatures) between what height the sensor thinks it is vs. where it actually is.

This is a standalone `eddy-ng` repository, intended to be integrated into your own Klipper installation.

## What's new?

* **Profiles:** you can now create separate [calibration profiles](docs/profiles.md) for each of your build plates, and for each plate define different `tap_adjust_z` values for different materials.
* **Setup without a paper test:** `PROBE_EDDY_NG_AUTO_SETUP` finds Z=0 by touching the bed with the nozzle, and measures the tap threshold for you (see [Calibration](docs/calibration.md#automatic-setup-without-a-paper-test)).
* **Tap reports:** `TAP REPORT=1` writes one page about all the taps of a `TAP`, with graphs of each one.
* **More accurate taps:** more accurate sample timing, and tap detection on the sensor's raw readings. When a tap doesn't trigger, the error tells you how close it came to the threshold.
* **Lift-off tap position:** taps are now measured where the nozzle leaves the bed as the toolhead lifts afterwards, instead of where contact was detected on the way down. It's more consistent, and doesn't depend on the tap threshold or speed (see [Tap position](docs/tap.md#tap-position)).
* **Probing with tap** on Klipper (`METHOD=tap`), and `BED_MESH_CALIBRATE METHOD=rapid_scan` on both Klipper and Kalico.
* **Works with newer Klipper**, including v0.13.0-665 and newer, which home Z through the probe.
* Experimental [nozzle alignment](docs/nozzle-alignment.md) for toolchangers.
* New [documentation](docs/index.md), covering installation, calibration, tap, profiles and every command and option.

**Updating from an older version?** Run `./install.sh` again, rebuild and flash the sensor firmware, and recalibrate. See the [changes](docs/changes.md) for everything else that needs your attention.

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

3. Build and flash the firmware for your sensor and configure it, following the [documentation](docs/index.md).

## Documentation

The full documentation is in [`docs/`](docs/index.md): [installation](docs/installation.md), [safety](docs/safety.md), [calibration](docs/calibration.md), [tap](docs/tap.md), [printing](docs/printing.md), [profiles](docs/profiles.md), [commands](docs/commands.md), the [configuration reference](docs/configuration.md) and more.

## Updating

Run a `git pull` and then run `./install.sh` again, and rebuild and flash the sensor firmware if it changed (eddy-ng tells you at startup if it's out of date):

```
cd ~/eddy-ng
git pull
./install.sh
```

