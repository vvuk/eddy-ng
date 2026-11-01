# ProbeEddy's lift-off code, on a ProbeEddy with only what it uses set up:
# collecting the samples of a tap's lift, and the summary after a TAP

import numpy as np
import pytest

from fakes import FakeGcmd, FakeGcode, FakePrinter
from probe_eddy_ng.liftoff import LiftoffFit
from probe_eddy_ng.params import ProbeEddyParams
from probe_eddy_ng.probe_eddy_ng import ProbeEddy
from probe_eddy_ng.profiles import TapProfile
from probe_eddy_ng.tap import TapResult


def make_eddy(tap_adjust_z=0.065, profile="default", material=None):
    e = object.__new__(ProbeEddy)
    e._printer = FakePrinter()
    e._gcode = FakeGcode()
    e._name = "probe_eddy_ng test"
    e.params = ProbeEddyParams()
    e._tap_profile = TapProfile(name=profile, section="probe_eddy_ng test", tap_adjust_z=tap_adjust_z)
    e._material = material
    return e


# --- collecting the lift ---

LIFT_T, Z_LOW, DEPRESS = 10.0, -0.1, 0.08


def toolhead_z(t):
    # standing at Z_LOW, then lifting at 100mm/s^2 up to 10mm/s
    if t < LIFT_T:
        return Z_LOW
    dt = t - LIFT_T
    return Z_LOW + (0.5 * 100 * dt * dt if dt < 0.1 else 0.5 + 10 * (dt - 0.1))


class FakeSampler:
    def __init__(self, times, freqs, memos):
        self.times, self.freqs, self.memos = times, freqs, memos

    def memo(self, name, value):
        self.memos[name] = value


def lift_sampler():
    rng = np.random.default_rng(1)
    zc = Z_LOW + DEPRESS
    times = list(np.arange(9.5, 10.6, 1 / 250))

    def freq(z):
        d = z - zc
        return 3.1e6 + (-100 * d if d <= 0 else -9000 * d + 1500 * d * d)

    freqs = [freq(toolhead_z(t)) + rng.normal(0, 2) for t in times]
    freqs[40] = float("nan")
    return FakeSampler(times, freqs, {"lift_start_time": LIFT_T})


def test_fit_last_liftoff():
    e = make_eddy()
    e._get_trapq_position = lambda t: ((0, 0, toolhead_z(t)), 0.0)
    e._last_sampler = lift_sampler()
    fit = e._fit_last_liftoff()
    assert fit.error is None
    assert fit.z == pytest.approx(Z_LOW + DEPRESS, abs=0.002)
    assert e._last_sampler.memos["liftoff"] is fit


def test_fit_last_liftoff_without_lift():
    e = make_eddy()
    e._last_sampler = lift_sampler()
    del e._last_sampler.memos["lift_start_time"]
    assert e._fit_last_liftoff().error == "no lift recorded"
    e._last_sampler = None
    assert e._fit_last_liftoff() is None


def test_fit_last_liftoff_never_raises():
    e = make_eddy()
    e._last_sampler = lift_sampler()

    def broken(t):
        raise RuntimeError("trapq")

    e._get_trapq_position = broken
    assert "internal error" in e._fit_last_liftoff().error


# --- the summary after a TAP ---


def result(descent, liftoff=None, error=None):
    fit = None if liftoff is None and error is None else LiftoffFit(z=liftoff or 0.0, error=error)
    return TapResult(probe_z=descent, descent_z=descent, liftoff=fit)


def summary(e, results, tap_position, tap_z, gcmd=None, samples=3, stddev=0.02, use_median=False):
    e._log_tap_comparison(results, tap_position, tap_z, samples, stddev, use_median, gcmd or FakeGcmd())
    return e._gcode.messages


def test_summary_suggests_liftoff_adjust():
    e = make_eddy()
    results = [result(0.100, 0.140), result(0.110, 0.142), result(0.105, 0.144)]
    msgs = summary(e, results, "descent", tap_z=0.105)
    assert msgs[0].startswith("Tap positions (descent used): descent mean 0.105")
    assert "lift-off mean 0.142" in msgs[0]
    assert "lift-off - descent +0.037" in msgs[0]
    # the same Z=0: 0.105 + 0.065 = 0.142 + 0.028
    assert msgs[1] == "With tap_position: liftoff, tap_adjust_z would be 0.028 (it's 0.065 with descent)."


def test_summary_suggests_descent_adjust():
    e = make_eddy(tap_adjust_z=0.028, profile="textured", material="PETG")
    e._tap_profile.set_adjust_z("PETG", 0.030)
    results = [result(0.100, 0.140), result(0.110, 0.142), result(0.105, 0.144)]
    msgs = summary(e, results, "liftoff", tap_z=0.142)
    assert msgs[1] == (
        "With tap_position: descent, tap_adjust_z for PETG in tap profile 'textured' would be 0.067 (it's 0.030 with liftoff)."
    )


def test_summary_uses_adjust_z_override():
    e = make_eddy()
    results = [result(0.100, 0.140), result(0.110, 0.142), result(0.105, 0.144)]
    msgs = summary(e, results, "descent", tap_z=0.105, gcmd=FakeGcmd(ADJUST_Z=0.1))
    assert msgs[1] == "With tap_position: liftoff, tap_adjust_z would be 0.063 (it's 0.100 with descent)."


def test_summary_combines_like_the_tap():
    # the other position's taps are picked the same way: here the median
    # of the tightest 3
    e = make_eddy(tap_adjust_z=0.0)
    results = [result(0.1, 0.20), result(0.1, 0.21), result(0.1, 0.30), result(0.1, 0.215)]
    msgs = summary(e, results, "descent", tap_z=0.1, use_median=True)
    assert msgs[1] == "With tap_position: liftoff, tap_adjust_z would be -0.110 (it's 0.000 with descent)."


def test_summary_without_enough_liftoffs():
    e = make_eddy()
    results = [result(0.100, 0.140), result(0.110, error="no contact found"), result(0.105)]
    msgs = summary(e, results, "descent", tap_z=0.105)
    assert "lift-off mean 0.140" in msgs[0] and "(1 taps)" in msgs[0]
    assert msgs[1].startswith("Not enough consistent liftoff taps")

    e = make_eddy()
    msgs = summary(e, [result(0.1), result(0.1), result(0.1)], "descent", tap_z=0.1)
    assert msgs[0].endswith("; no lift-off fits")
