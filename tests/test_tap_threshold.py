# The tap threshold: AUTO_SETUP measuring it with taps that don't trigger,
# staging it for SAVE_CONFIG, replaying a tap's signal to find its peak, and
# reporting taps that missed against the threshold
import types

import numpy as np
import pytest

from fakes import CommandError, ConfigError, FakeGcmd, FakeGcode, FakePrinter
from probe_eddy_ng.autosetup import ProbeEddyZeroFinder
from probe_eddy_ng.params import ProbeEddyParams
from probe_eddy_ng.probe_eddy_ng import ProbeEddy
from probe_eddy_ng.profiles import TapProfile
from probe_eddy_ng.tap import TapConfig, TapResult, tap_filter_sos

SPS = 500.0


def make_eddy(profile="default", hand_written_threshold=False):
    e = object.__new__(ProbeEddy)
    e._printer = FakePrinter()
    e._gcode = FakeGcode()
    e._name = "probe_eddy_ng test"
    e.params = e._base_params = ProbeEddyParams()
    section = "probe_eddy_ng test" if profile == "default" else f"probe_eddy_ng_tap {profile}"
    e._tap_profile = TapProfile(name=profile, section=section, hand_written_threshold=hand_written_threshold)
    e._tap_sos = tap_filter_sos(e.params, SPS, ConfigError)
    e._last_sampler = None
    e.save_samples_path = None
    return e


def errors(e):
    return [m for m in e._gcode.messages if m.startswith("!!")]


# --- staging the threshold ---


def test_set_threshold_default_profile():
    e = make_eddy()
    e._set_tap_threshold(103.0)
    assert e.params.tap_threshold == 103.0
    assert e._base_params.tap_threshold == 103.0
    assert e._printer.objects["configfile"].saved == {"probe_eddy_ng test": {"tap_threshold": "103"}}


def test_set_threshold_other_profile():
    e = make_eddy(profile="textured")
    e._set_tap_threshold(150.0)
    assert e.params.tap_threshold == 150.0
    # the section's own setting, used by other profiles, is unchanged
    assert e._base_params.tap_threshold == ProbeEddyParams().tap_threshold
    assert e._tap_profile.params["tap_threshold"] == 150.0
    assert e._printer.objects["configfile"].saved == {"probe_eddy_ng_tap textured": {"tap_threshold": "150"}}


def test_set_threshold_hand_written():
    # SAVE_CONFIG would refuse to save over a value in the config files
    e = make_eddy(hand_written_threshold=True)
    e._set_tap_threshold(103.0)
    assert e.params.tap_threshold == 103.0
    assert e._printer.objects["configfile"].saved == {}
    assert "can't be saved" in errors(e)[0]


# --- replaying the last tap's signal ---


class FakeSampler:
    def __init__(self, times, freqs, memos):
        self.times, self.freqs, self.memos = times, freqs, memos


def eddy_freq(gap):
    return 3.0e6 + 9e4 / (gap + 1.5)


# A tap at 3mm/s: accelerating from 3mm, contact at 0, and stopping at
# -0.3, then lifting at LIFT_T. Returns the sampler and the toolhead's
# position and speed at a time.
def tap_sampler(speed=3.0, accel=100.0, bed_z=0.0):
    t_acc = speed / accel
    z_acc = 0.5 * accel * t_acc**2
    t_end = t_acc + (3.0 - z_acc + 0.3) / speed
    lift_t = t_end + 0.05

    def kin(t):
        if t < t_acc:
            return 3.0 - 0.5 * accel * t * t, accel * t
        if t < t_end:
            return 3.0 - z_acc - speed * (t - t_acc), speed
        if t < lift_t:
            return -0.3, 0.0
        return -0.3 + 5.0 * (t - lift_t), 5.0

    rng = np.random.default_rng(0)
    times = list(np.arange(0.0, lift_t + 0.2, 1.0 / SPS))
    freqs = []
    for t in times:
        z = kin(t)[0] - bed_z
        # past contact the sensor still gets closer as the mount flexes
        freqs.append(eddy_freq(z if z > 0 else 0.1 * z) + rng.normal(0, 1.0))
    memos = {"tap_safe_freq": eddy_freq(2.0), "tap_trigger_freq": eddy_freq(1.0), "lift_start_time": lift_t}
    return FakeSampler(times, freqs, memos), kin


def replay_eddy(sampler, kin):
    e = make_eddy()
    e._last_sampler = sampler
    e._get_trapq_position = lambda t: ((0.0, 0.0, kin(t)[0]), kin(t)[1])
    return e


def test_last_tap_signal():
    sampler, kin = tap_sampler()
    e = replay_eddy(sampler, kin)
    tapcfg = TapConfig(threshold=1e9, sos=e._tap_sos)
    peak, before = e.last_tap_signal(tapcfg, 3.0, above_z=0.05)
    assert peak > 10 * before
    assert e.last_tap_signal(tapcfg, 3.0)[1] is None


def test_last_tap_signal_ignores_the_lift():
    # the lift's signal is far larger than the tap's, and doesn't count
    sampler, kin = tap_sampler()
    e = replay_eddy(sampler, kin)
    tapcfg = TapConfig(threshold=1e9, sos=e._tap_sos)
    peak, _ = e.last_tap_signal(tapcfg, 3.0)
    del sampler.memos["lift_start_time"]
    assert e.last_tap_signal(tapcfg, 3.0)[0] > peak


def test_last_tap_signal_at_tap_speed_only():
    # only samples taken while the toolhead moved at the tap speed count
    sampler, kin = tap_sampler()
    e = replay_eddy(sampler, kin)
    tapcfg = TapConfig(threshold=1e9, sos=e._tap_sos)
    assert e.last_tap_signal(tapcfg, 3.0)[0] is not None
    assert e.last_tap_signal(tapcfg, 4.0) == (None, None)


def test_last_tap_signal_without_gate_freqs():
    sampler, kin = tap_sampler()
    del sampler.memos["tap_trigger_freq"]
    e = replay_eddy(sampler, kin)
    assert e.last_tap_signal(TapConfig(threshold=1e9, sos=e._tap_sos), 3.0) == (None, None)
    e._last_sampler = None
    assert e.last_tap_signal(TapConfig(threshold=1e9, sos=e._tap_sos), 3.0) == (None, None)


# --- AUTO_SETUP measuring the threshold ---


class FakeToolhead:
    def get_kinematics(self):
        return types.SimpleNamespace(max_z_accel=100.0)


def measuring_finder(e, peaks, before=5.0, triggers=False):
    f = object.__new__(ProbeEddyZeroFinder)
    f.eddy = e
    f._printer = e._printer
    f.gcmd = None
    f._th = FakeToolhead()
    f.fine_step = 0.2
    f.tap_speed = 5.0
    f.tap_start_z = 3.0
    f.tap_lift_speed = 5.0
    f.threshold_samples = len(peaks)
    taps = []
    remaining = list(peaks)

    def do_one_tap(**kwargs):
        taps.append(kwargs)
        if triggers:
            return TapResult(probe_z=kwargs["bed_z"])
        return TapResult(error=CommandError("No trigger"), missed=True)

    e.do_one_tap = do_one_tap
    e.last_tap_signal = lambda tapcfg, speed, above_z=None: (remaining.pop(0), before)
    return f, taps


def test_measure_threshold():
    e = make_eddy()
    f, taps = measuring_finder(e, [212.0, 219.0, 206.0])
    assert f._measure_threshold(3.02e6, 3.03e6) == 103.0

    assert len(taps) == 3
    for tap in taps:
        assert tap["expect_miss"] and tap["bed_z"] == f.Z_BASE
        assert tap["tapcfg"].threshold == 1e9
        assert (tap["tapcfg"].safe_freq, tap["tapcfg"].trigger_freq) == (3.02e6, 3.03e6)
        assert tap["start_z"] == f.Z_BASE + 3.0
        # a fine step, 80ms at 5mm/s, and stopping from 5mm/s at 100mm/s^2
        assert tap["target_z"] == pytest.approx(f.Z_BASE - (0.2 + 0.4 + 0.125))

    assert e._gcode.messages[1:4] == [
        "Test tap 1: signal peaked at 212",
        "Test tap 2: signal peaked at 219",
        "Test tap 3: signal peaked at 206",
    ]
    assert not errors(e)


def test_measure_threshold_too_close_to_noise():
    e = make_eddy()
    f, _ = measuring_finder(e, [60.0, 62.0, 58.0], before=12.0)
    with pytest.raises(CommandError, match="isn't clearly above the signal before contact"):
        f._measure_threshold(3.02e6, 3.03e6)


def test_measure_threshold_tap_triggered():
    e = make_eddy()
    f, _ = measuring_finder(e, [212.0], triggers=True)
    with pytest.raises(CommandError, match="measuring the tap signal failed"):
        f._measure_threshold(3.02e6, 3.03e6)


# --- taps that miss ---


def tapping_eddy(results):
    e = make_eddy()
    e._tap_profile.tap_drive_current = 16
    e._z_homed = lambda: True
    e._sensor = types.SimpleNamespace(set_drive_current=lambda dc: None)
    moves = []
    e._toolhead = types.SimpleNamespace(manual_move=lambda pos, speed: moves.append(pos))
    remaining = list(results)
    taps = []

    def do_one_tap(**kwargs):
        taps.append(kwargs)
        return remaining.pop(0)

    e.do_one_tap = do_one_tap
    return e, taps, moves


def missed(peak):
    return TapResult(error=CommandError("No trigger"), missed=True, signal_peak=peak, toolhead_z=4.4)


def test_missed_taps_report_signal_peak():
    e, taps, moves = tapping_eddy([missed(p) for p in (210.0, 190.0, 205.0, 220.0, 200.0)])
    with pytest.raises(CommandError, match="Tap failed"):
        e.run_taps(FakeGcmd(THRESHOLD=300), 16)
    assert len(taps) == 5
    assert "Tap 2: failed: no tap detected (signal peaked at 190, threshold 300)" in e._gcode.messages
    err = errors(e)[-1]
    assert "peaked at 190 to 220, below the threshold of 300" in err
    assert "try THRESHOLD=95" in err
    # lifted off the bed afterwards
    assert moves[-1][2] == e.params.tap_start_z


def test_stop_on_miss():
    e, taps, moves = tapping_eddy([missed(210.0), missed(200.0)])
    with pytest.raises(CommandError, match="Tap failed"):
        e.run_taps(FakeGcmd(), 16, z_base=5.0, gate_freqs=(3.02e6, 3.03e6), stop_on_miss=True)
    assert len(taps) == 1
    assert taps[0]["bed_z"] == 5.0
    assert taps[0]["start_z"] == e.params.tap_start_z + 5.0
    assert (taps[0]["tapcfg"].safe_freq, taps[0]["tapcfg"].trigger_freq) == (3.02e6, 3.03e6)
    assert "try THRESHOLD=105" in errors(e)[-1]
    assert moves[-1][2] == e.params.tap_start_z + 5.0
