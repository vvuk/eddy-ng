# Combining taps, the tap filter, and the replay of the firmware's tap detection
import math

import numpy as np
import pytest

from fakes import ConfigError, FakeGcmd
from probe_eddy_ng import tap
from probe_eddy_ng.params import ProbeEddyParams
from probe_eddy_ng.tap import TapResult, compute_tap_z, is_tap_request, replay_tap_signal, tap_filter_sos


def taps(*zs, overshoots=None):
    overshoots = overshoots or [0.0] * len(zs)
    return [TapResult(probe_z=z, overshoot=o) for z, o in zip(zs, overshoots)]


# --- compute_tap_z ---


def test_tap_z_uses_tightest_cluster():
    z, std, overshoot = compute_tap_z(
        taps(0.10, 0.30, 0.11, 0.12, -0.20, overshoots=[1, 2, 3, 4, 5]), samples=3, req_stddev=0.02, use_median=False
    )
    assert z == pytest.approx(0.11)
    assert std == pytest.approx(np.std([0.10, 0.11, 0.12]))
    # the overshoots of the same taps
    assert overshoot == pytest.approx((1 + 3 + 4) / 3)


def test_tap_z_median():
    z, _, overshoot = compute_tap_z(taps(0.10, 0.12, 0.13, overshoots=[1, 2, 3]), 3, 0.02, use_median=True)
    assert z == pytest.approx(0.12)
    assert overshoot == 2


def test_tap_z_not_enough_taps():
    assert compute_tap_z(taps(0.1, 0.1), 3, 0.02, False) == (None, None, None)


def test_tap_z_too_spread_out():
    z, std, overshoot = compute_tap_z(taps(0.0, 0.1, 0.2), 3, 0.02, False)
    assert z is None and overshoot is None
    assert std == pytest.approx(np.std([0.0, 0.1, 0.2]))


def test_tap_z_cluster_anywhere():
    # the best cluster isn't necessarily at either end
    z, _, _ = compute_tap_z(taps(0.0, 0.5, 0.51, 0.52, 1.0), 3, 0.02, False)
    assert z == pytest.approx(0.51)


# --- the tap filter ---


@pytest.mark.parametrize("data_rate", [250, 500])
def test_precomputed_filter_matches_scipy(data_rate):
    signal = pytest.importorskip("scipy.signal")
    params = ProbeEddyParams()
    expected = signal.butter(2, [5.0, 25.0], btype="bandpass", fs=data_rate, output="sos")
    assert np.allclose(tap_filter_sos(params, data_rate, ConfigError), expected)


def test_custom_filter_needs_scipy(monkeypatch):
    monkeypatch.setattr(tap, "scipy", None)
    params = ProbeEddyParams(tap_butter_highcut=30.0)
    with pytest.raises(ConfigError, match="requires scipy"):
        tap_filter_sos(params, 250, ConfigError)
    # the defaults don't
    assert len(tap_filter_sos(ProbeEddyParams(), 250, ConfigError)) == 2


def test_custom_filter():
    pytest.importorskip("scipy.signal")
    sos = tap_filter_sos(ProbeEddyParams(tap_butter_order=3), 250, ConfigError)
    assert len(sos) == 3


# --- replaying the firmware's tap detection ---

SPS = 250


def eddy_freq(gap):
    # roughly how an eddy sensor's frequency goes up as it gets closer
    return 3.0e6 + 9e4 / (np.asarray(gap) + 1.5)


# A tap at tap_speed from start_z: the nozzle stops at the bed (z=0) while
# the toolhead keeps going down, with the sensor getting closer by
# `compliance` of that. Returns (freqs, toolhead zs).
def simulated_tap(speed=3.0, start_z=3.0, end_z=-0.25, compliance=0.0, noise=10.0, seed=0):
    rng = np.random.default_rng(seed)
    z = np.arange(start_z, end_z, -speed / SPS)
    gap = np.where(z > 0, z, compliance * z)
    return eddy_freq(gap) + rng.normal(0, noise, len(z)), z


def sos():
    return tap_filter_sos(ProbeEddyParams(), SPS, ConfigError)


def test_replay_detects_contact():
    freqs, z = simulated_tap()
    signal = replay_tap_signal(sos(), list(freqs), eddy_freq(2.0), eddy_freq(1.0))
    sig = np.array([np.nan if s is None else s for s in signal])

    # nothing until the trigger frequency
    assert all(s is None for s, zz in zip(signal, z) if zz > 1.0 + 3.0 / SPS)
    assert all(s is not None for s, zz in zip(signal, z) if zz < 1.0 - 3.0 / SPS)

    before = np.nanmax(sig[z > 0.05])
    after = np.nanmax(sig[(z <= 0) & (z > -0.2)])
    assert after > 10 * before
    # the default threshold of 250 would trigger within ~50ms of contact
    first = np.argmax(np.nan_to_num(sig, nan=0.0) > 250.0)
    assert -3.0 * 0.050 < z[first] <= 0.0


def test_replay_tap_signal_grows_with_speed():
    peaks = []
    for speed in (1.5, 3.0, 6.0):
        freqs, _ = simulated_tap(speed=speed)
        sig = replay_tap_signal(sos(), list(freqs), eddy_freq(2.0), eddy_freq(1.0))
        peaks.append(max(s for s in sig if s is not None))
    assert peaks[0] < peaks[1] < peaks[2]


def test_replay_skips_error_samples():
    freqs, _ = simulated_tap(noise=0.0)
    with_errors = list(freqs)
    for i in range(0, len(with_errors), 10):
        with_errors[i] = math.nan
    sig = replay_tap_signal(sos(), with_errors, eddy_freq(2.0), eddy_freq(1.0))
    assert all(s is None for s, f in zip(sig, with_errors) if f != f)
    assert max(s for s in sig if s is not None) > 250


def test_replay_matches_scipy_filter():
    # the replay's filter is the same as scipy's, applied from the sample
    # after the one at safe_freq, relative to it
    signal = pytest.importorskip("scipy.signal")
    freqs, _ = simulated_tap()
    safe, trigger = eddy_freq(2.0), eddy_freq(1.0)
    replay = replay_tap_signal(sos(), list(freqs), safe, trigger)

    i0 = int(np.argmax(freqs >= safe))
    filtered = signal.sosfilt(np.array(sos()), freqs[i0 + 1 :] - freqs[i0])
    expected = [None] * (i0 + 1)
    armed, start, last = False, 0.0, 0.0
    for f, v in zip(freqs[i0 + 1 :], filtered):
        if not armed and f < trigger:
            expected.append(None)
            continue
        armed = True
        if v > last:
            start = v
        expected.append(start - v)
        last = v

    assert [s is None for s in replay] == [s is None for s in expected]
    got = np.array([s for s in replay if s is not None])
    want = np.array([s for s in expected if s is not None])
    assert np.allclose(got, want, rtol=1e-9, atol=1e-6)


def test_replay_never_reaches_safe_freq():
    freqs, _ = simulated_tap()
    assert all(s is None for s in replay_tap_signal(sos(), list(freqs), 1e9, 1e9))


# --- METHOD=tap ---


def test_is_tap_request():
    assert is_tap_request(FakeGcmd(METHOD="tap"))
    assert is_tap_request(FakeGcmd(METHOD="TAP"))
    assert not is_tap_request(FakeGcmd(METHOD="scan"))
    assert not is_tap_request(FakeGcmd())
    assert not is_tap_request(None)
