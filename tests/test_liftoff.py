# Fitting where the nozzle leaves the bed as the toolhead lifts after a tap
import numpy as np
import pytest

from probe_eddy_ng import liftoff
from probe_eddy_ng.liftoff import fit_liftoff

F_BASE = 3.1e6


# Samples from a simulated lift: the toolhead stands still at z_low for
# `pre` seconds, then accelerates up. The nozzle leaves the bed `depress`
# above z_low; until then the frequency changes by contact_slope per mm, and
# after that it follows the free-air curve. Returns (zs, freqs, lift-off z).
def simulated_lift(
    depress,
    sps=250,
    accel=100.0,
    vmax=10.0,
    noise=2.0,
    free_slope=-9000.0,
    free_curve=1500.0,
    contact_slope=-150.0,
    pre=0.02,
    z_low=-0.1,
    seed=0,
):
    rng = np.random.default_rng(seed)
    t = np.maximum(np.arange(-pre, liftoff.LIFTOFF_WINDOW_TIME, 1.0 / sps), 0.0)
    t_acc = vmax / accel
    z = z_low + np.where(t < t_acc, 0.5 * accel * t**2, 0.5 * accel * t_acc**2 + vmax * (t - t_acc))
    z = z[z <= z_low + liftoff.LIFTOFF_WINDOW_HEIGHT]
    zc = z_low + depress
    d = z - zc
    f = F_BASE + np.where(d <= 0, contact_slope * d, free_slope * d + free_curve * d * d)
    return z, f + rng.normal(0, noise, len(z)), zc


@pytest.mark.parametrize("sps", [250, 500])
@pytest.mark.parametrize("depress", [0.02, 0.05, 0.1, 0.2])
def test_finds_liftoff(sps, depress):
    errors = []
    for seed in range(10):
        z, f, zc = simulated_lift(depress, sps=sps, seed=seed)
        fit = fit_liftoff(z, f)
        assert fit.error is None, str(fit)
        assert fit.depress == pytest.approx(fit.z - z.min())
        assert fit.contact_samples >= 2
        errors.append(fit.z - zc)
    # a few microns, against tens of microns of spread for descent taps
    assert np.abs(errors).max() < 0.005
    assert abs(np.mean(errors)) < 0.002


def test_fit_parameters():
    z, f, zc = simulated_lift(0.08, noise=0.0)
    fit = fit_liftoff(z, f)
    assert fit.z == pytest.approx(zc, abs=0.0002)
    assert fit.freq == pytest.approx(F_BASE, abs=1.0)
    assert fit.contact_slope == pytest.approx(-150.0, rel=0.05)
    assert fit.free_slope == pytest.approx(-9000.0, rel=0.01)
    assert fit.rms < 0.5
    assert fit.slope_ratio < 0.05


def test_independent_of_frequency_scale():
    # the fit works around the mean, so MHz offsets don't hurt
    z, f, _ = simulated_lift(0.05, seed=3)
    a = fit_liftoff(z, f)
    b = fit_liftoff(z, f + 20e6)
    assert a.z == pytest.approx(b.z, abs=1e-6)


def test_error_samples_are_ignored():
    z, f, zc = simulated_lift(0.05)
    f[::7] = np.nan
    fit = fit_liftoff(z, f)
    assert fit.error is None
    assert fit.samples == np.count_nonzero(np.isfinite(f))
    assert fit.z == pytest.approx(zc, abs=0.005)


def test_no_contact():
    # free air the whole way: nothing pressed against the bed
    z, f, _ = simulated_lift(0.1, contact_slope=-9000.0, free_curve=0.0)
    fit = fit_liftoff(z, f)
    assert fit.error is not None and "no contact" in fit.error
    assert "failed" in str(fit)


def test_not_enough_samples():
    fit = fit_liftoff(np.linspace(0, 1, 9), np.linspace(1, 2, 9))
    assert "not enough samples" in fit.error


def test_insufficient_lift():
    fit = fit_liftoff(np.linspace(0, 0.2, 30), np.linspace(1, 2, 30))
    assert "insufficient lift" in fit.error


def test_liftoff_beyond_search_range():
    # still pressed into the bed past LIFTOFF_MAX_DEPRESS
    z, f, _ = simulated_lift(0.45, noise=0.5)
    fit = fit_liftoff(z, f)
    assert "edge of the search range" in fit.error


def test_contact_samples_exclude_standing_still():
    # samples from before the lift are at the lowest z, and don't count as
    # lifting with the nozzle on the bed
    z, f, _ = simulated_lift(0.003, pre=0.1, noise=0.0)
    fit = fit_liftoff(z, f)
    standing = np.count_nonzero(z <= z.min() + 1e-4)
    assert standing > 20
    assert fit.contact_samples < 2
    assert "too few samples in contact" in fit.error
