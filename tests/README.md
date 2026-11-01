# Tests

These test eddy-ng's own logic without Klipper or a printer: lift-off fitting, the tap filter and the replay of the firmware's tap detection, combining taps, calibration fitting and saving, settings and profiles, AUTO_SETUP's search for the bed, and placing sensor samples in time. Most of them run the code on simulated sensor data.

They don't cover the parts that talk to Klipper (homing, moves, the MCU, G-code commands end to end); test those on a printer.

## Running

You need Python 3.8 or newer with numpy and pytest. With scipy installed, a few more filter tests run.

```
pip install -r tests/requirements.txt
python3 -m pytest tests
```

Or, with [uv](https://docs.astral.sh/uv/):

```
uv run --no-project --with-requirements tests/requirements.txt python -m pytest tests
```

Run `pytest` on the `tests` directory (or from inside it), not from the repository root: the root `__init__.py` (for Kalico) makes pytest try to import the repository as a package.

## How it works

`conftest.py` puts stand-ins for Klipper's modules in place, so that `probe_eddy_ng` imports as it would inside Klipper. `fakes.py` has fakes for the Klipper objects the code under test uses: a config section, G-code command parameters, the console and the printer. Tests that need part of `ProbeEddy` create one without running its constructor and set only the attributes the code uses (see `test_tap_summary.py`).
