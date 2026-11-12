# Tests

These test eddy-ng's own logic without Klipper or a printer: lift-off fitting, the tap filter and the replay of the firmware's tap detection, measuring and saving the tap threshold, reporting taps that missed, combining taps, calibration fitting and saving, settings and profiles, AUTO_SETUP's search for the bed, and placing sensor samples in time. Most of them run the code on simulated sensor data.

They don't cover the parts that talk to Klipper (homing, moves, the MCU, G-code commands end to end); test those on a printer.

## Running

With [uv](https://docs.astral.sh/uv/), from the top of the repository:

```
uv run pytest
```

This sets up a virtual environment (`.venv`) with numpy, pytest and scipy; the last two are `pyproject.toml`'s `dev` dependencies. The tests need Python 3.8 or newer; to run them on a particular version, add e.g. `--python 3.8`. Without scipy, a few filter tests are skipped; to run them that way, like a printer without scipy:

```
uv run --exact --no-dev --with pytest pytest
```

Without uv, install numpy, pytest and (optionally) scipy, and run `python3 -m pytest`.

## How it works

`conftest.py` puts stand-ins for Klipper's modules in place, so that `probe_eddy_ng` imports as it would inside Klipper. `fakes.py` has fakes for the Klipper objects the code under test uses: a config section, G-code command parameters, the console and the printer. Tests that need part of `ProbeEddy` create one without running its constructor and set only the attributes the code uses (see `test_tap_summary.py`).
