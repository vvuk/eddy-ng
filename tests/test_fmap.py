# The calibration: fitting heights to frequencies, and saving and loading it
import numpy as np
import pytest

from fakes import CommandError, ConfigError, FakeConfig, FakeGcode, FakePrinter
from probe_eddy_ng.fmap import ProbeEddyFrequencyMap
from probe_eddy_ng.params import ProbeEddyParams


def eddy_freq(height):
    return 3.0e6 + 9e4 / (np.asarray(height, dtype=float) + 1.5)


class FakeEddy:
    def __init__(self, **params):
        self._sensor = None
        self._printer = FakePrinter()
        self.params = ProbeEddyParams(**params)
        self.gcode = FakeGcode()
        self.errors = []

    def _log_msg(self, msg):
        self.gcode.messages.append(msg)

    _log_info = _log_debug = _log_msg

    def _log_error(self, msg):
        self.errors.append(msg)

    _log_warning = _log_error


# Samples from moving down from 15mm to `bottom`, as calibration records them
def calibration_samples(bottom=0.02, top=15.0, n=3000, noise=0.5, seed=0):
    rng = np.random.default_rng(seed)
    heights = np.linspace(top, bottom, n)
    times = np.linspace(0.0, 10.0, n)
    freqs = eddy_freq(heights) + rng.normal(0, noise, n)
    return times, freqs, heights


def calibrate(eddy=None, dc=16, **kwargs):
    eddy = eddy or FakeEddy()
    fmap = ProbeEddyFrequencyMap(eddy)
    times, freqs, heights = calibration_samples(**kwargs)
    ok = fmap.calibrate_from_values(dc, times, freqs, heights, [], report_errors=True, write_debug_files=False)
    return fmap, ok, eddy


def test_calibration_fit():
    fmap, ok, eddy = calibrate()
    assert ok and fmap.calibrated()
    assert not eddy.errors
    assert fmap.drive_current == 16
    assert fmap.height_range[0] == pytest.approx(0.02)
    assert fmap.height_range[1] == pytest.approx(15.0)
    assert fmap._ftoh_rms < 0.005

    heights = np.linspace(0.05, 14.5, 200)
    got = fmap.freqs_to_heights_np(eddy_freq(heights))
    low = heights <= 5.0
    assert np.abs(got[low] - heights[low]).max() < 0.005
    # above low_z_threshold, accurate enough for homing
    assert np.abs(got[~low] - heights[~low]).max() < 0.05
    assert fmap.freq_to_height(eddy_freq(2.0)) == pytest.approx(2.0, abs=0.003)
    assert "DC 16: valid range 0.020 to 15.000" in format(fmap)


def test_height_to_freq_inverts():
    fmap, _, _ = calibrate()
    for h in (0.1, 1.0, 2.0, 3.5, 8.0):
        f = fmap.height_to_freq(h)
        assert fmap.freq_to_height(f) == pytest.approx(h, abs=0.002)
        assert f == pytest.approx(eddy_freq(h), rel=2e-6)
    with pytest.raises(CommandError, match="outside the calibrated range"):
        fmap.height_to_freq(20.0)


def test_uncalibrated():
    fmap = ProbeEddyFrequencyMap(FakeEddy())
    assert not fmap.calibrated()
    with pytest.raises(CommandError):
        fmap.freq_to_height(3.0e6)


def test_rejects_bad_calibrations():
    # the sensor stopped reading low down: too high for tap or homing
    _, ok, eddy = calibrate(bottom=1.0)
    assert not ok and "min height" in eddy.errors[0]

    # never got high enough
    _, ok, eddy = calibrate(top=2.0)
    assert not ok and "max height" in eddy.errors[0]

    # noisy enough that the fit is off
    _, ok, eddy = calibrate(noise=1500.0)
    assert not ok and "error margin" in eddy.errors[0]

    # allow_unsafe keeps it anyway
    _, ok, _ = calibrate(FakeEddy(allow_unsafe=True), bottom=1.0)
    assert ok


def test_warns_about_low_spread():
    eddy = FakeEddy()
    fmap = ProbeEddyFrequencyMap(eddy)
    times, freqs, heights = calibration_samples(noise=0.0)
    # squash the spread to ~0.1%
    freqs = 3.0e6 + (freqs - 3.0e6) / 20.0
    assert fmap.calibrate_from_values(16, times, freqs, heights, [], True, False)
    assert any("EXTREMELY low" in e for e in eddy.errors)


def test_no_samples():
    fmap = ProbeEddyFrequencyMap(FakeEddy())
    assert not fmap.calibrate_from_values(16, [], [], [], [], True, False)


def test_save_and_load():
    fmap, _, eddy = calibrate()
    fmap.save_calibration("probe_eddy_ng test")
    saved = eddy._printer.lookup_object("configfile").saved["probe_eddy_ng test"]
    assert set(saved) == {"calibration_16"}

    loaded = ProbeEddyFrequencyMap(FakeEddy())
    assert loaded.load_from_config(FakeConfig(saved), 16)
    freqs = eddy_freq(np.linspace(0.05, 14.5, 50))
    # floats round-trip exactly through the JSON
    assert np.array_equal(loaded.freqs_to_heights_np(freqs), fmap.freqs_to_heights_np(freqs))
    assert loaded.height_range == tuple(fmap.height_range)
    assert loaded.freq_range == tuple(fmap.freq_range)
    assert loaded._ftoh_rms == fmap._ftoh_rms

    assert not ProbeEddyFrequencyMap(FakeEddy()).load_from_config(FakeConfig(saved), 17)
    with pytest.raises(ConfigError, match="drive current mismatch"):
        ProbeEddyFrequencyMap(FakeEddy()).load_from_config(FakeConfig({"calibration_17": saved["calibration_16"]}), 17)
