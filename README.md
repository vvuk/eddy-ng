# eddy-ng

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

3. Build and flash the firmware for your sensor and configure it, following the [documentation](docs/index.md).

## Documentation

The full documentation is in [`docs/`](docs/index.md): [installation](docs/installation.md), [safety](docs/safety.md), [calibration](docs/calibration.md), [tap](docs/tap.md), [profiles](docs/profiles.md), [commands](docs/commands.md), the [configuration reference](docs/configuration.md) and more.

## Updating

Run a `git pull` and then run `./install.sh` again, and rebuild and flash the sensor firmware if it changed (eddy-ng tells you at startup if it's out of date):

```
cd ~/eddy-ng
git pull
./install.sh
```

