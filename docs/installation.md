# Installation

These instructions assume a standard setup like the one KIAUH creates: Klipper in `~/klipper` (or Kalico in `~/kalico`) and its Python environment in `~/klippy-env`. Adjust the paths if yours differ.

## Install

1. Clone eddy-ng:

   ```
   cd ~
   git clone https://github.com/vvuk/eddy-ng
   ```

2. Run the install script:

   ```
   cd ~/eddy-ng
   ./install.sh
   ```

   If Klipper/Kalico isn't in `~/klipper` or `~/kalico`, pass its path: `./install.sh ~/my-klipper`. Add `--copy` to copy the files instead of linking them.

   - **Klipper:** links the Python code into `klippy/extras/probe_eddy_ng/` and the firmware code into `src/`, adds the firmware code to `src/Makefile`, and patches `klippy/extras/bed_mesh.py` so that `BED_MESH_CALIBRATE METHOD=rapid_scan` works with eddy-ng.
   - **Kalico:** links the Python code as a plugin and the firmware code as a firmware extra. No Kalico files are changed.

3. Build and flash the firmware for the MCU your sensor is connected to, then configure the probe:
   - [BTT Eddy](btt-eddy.md)
   - [Cartographer](cartographer.md)
   - [Mellow Fly](mellow-fly.md)

   In `make menuconfig`:
   - **Klipper:** eddy-ng is built along with Klipper's own LDC1612 support, so make sure "Support ldc1612 eddy current sensor" is enabled under "Optional features" (it is by default).
   - **Kalico:** enable "Include eddy-ng ldc1612 support" under "Firmware Extras".

## Install plotly and scipy (recommended)

eddy-ng can write graphs of calibrations and taps, which help a lot in diagnosing problems. These need `plotly`; `scipy` adds the tap filter signal to the tap graph.

```
~/klippy-env/bin/pip3 install plotly==5.24.1 scipy
```

Stick to plotly 5.24.1; newer versions have caused problems.

`scipy` is also required for a custom tap filter (`tap_butter_*` settings), for `samples_per_second` values other than 250 or 500, and for [nozzle alignment](nozzle-alignment.md).

## Update eddy-ng

```
cd ~/eddy-ng
git pull
./install.sh
```

Re-running `install.sh` is only strictly needed when updating from an older eddy-ng that installed its files differently, but it's harmless otherwise.

When an update changes the firmware, rebuild and flash it. eddy-ng checks the firmware version at startup and tells you if the sensor firmware is out of date.

## Update Klipper or Kalico

**Kalico:** update Kalico and eddy-ng independently with `git pull`. Nothing needs to be reinstalled.

**Klipper:** because eddy-ng modifies two Klipper files, uninstall it before updating Klipper and reinstall it afterwards:

```
cd ~/eddy-ng
./install.sh --uninstall
cd ~/klipper
git pull
cd ~/eddy-ng
./install.sh
```

If `git pull` in `~/klipper` complains about local changes, discard eddy-ng's changes by hand, then update and run `install.sh` again:

```
cd ~/klipper
git restore src/Makefile klippy/extras/bed_mesh.py
```

## Uninstall

```
cd ~/eddy-ng
./install.sh --uninstall
```

Then remove the `[probe_eddy_ng ...]` sections from your config, and rebuild and flash the stock firmware.
