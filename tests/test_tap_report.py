# The tap report: capturing each tap's samples and building the page's data
# from simulated taps

import json
import re

import numpy as np
import pytest

from probe_eddy_ng import tap_report
from probe_eddy_ng.liftoff import LiftoffFit
from probe_eddy_ng.params import ProbeEddyParams
from probe_eddy_ng.tap import TapResult, compute_tap_z

RATE = 250
SENSOR_OFFSET = 0.7
SPEED = 3.0
LIFT_SPEED = 5.0
SOS = [
    [0.046131802093312926, 0.09226360418662585, 0.046131802093312926, 1.0, -1.3297767184682712, 0.5693902189294331],
    [1.0, -2.0, 1.0, 1.0, -1.845000600983779, 0.8637525213328747],
]


class FakeSampler:
    def __init__(self, times, freqs, heights, memos):
        self.times, self.freqs, self.heights, self.memos = times, freqs, heights, memos


class FakeEddy:
    def __init__(self):
        self._tap_sos = SOS
        self._last_sampler = None
        self._kin = None

    def _get_trapq_height(self, t):
        return self._kin(t)


def freq_at(z):
    return 3.0e6 + 4000.0 / (z + 0.5)


# A tap at contact_z: down at SPEED from 2mm, triggering 20ms after contact,
# stopping at the trigger, lifting 100ms later
def simulate_tap(eddy, contact_z, rng, missed=False):
    t0 = 100.0
    t_contact = t0 + (2.0 - contact_z) / SPEED
    t_trigger = t_contact + 0.020
    z_stop = contact_z - 0.020 * SPEED
    t_lift = t_trigger + 0.100
    if missed:
        t_lift = t_trigger = t_contact + 0.030

    def kin(t):
        if t < t0:
            return None
        if t < t_trigger:
            return 2.0 - (t - t0) * SPEED
        if t < t_lift:
            return z_stop
        return z_stop + (t - t_lift) * LIFT_SPEED

    times = list(np.arange(t0, t_lift + 0.4, 1 / RATE))
    heights = [max(kin(t), contact_z) + rng.normal(0, 0.001) for t in times]
    freqs = [freq_at(h) + rng.normal(0, 1) for h in heights]
    # the sensor's heights are offset from the toolhead's Z
    heights = [h + SENSOR_OFFSET for h in heights]
    memos = {"tap_safe_freq": freq_at(1.0), "tap_trigger_freq": freq_at(0.5), "lift_start_time": t_lift}
    if not missed:
        memos.update(trigger_time=t_trigger, tap_start_time=t_contact)
    eddy._kin = kin
    eddy._last_sampler = FakeSampler(times, freqs, heights, memos)
    if missed:
        return TapResult(error=Exception("No trigger"), missed=True, toolhead_z=z_stop, signal_peak=120.0)
    liftoff = LiftoffFit(z=contact_z + 0.03, depress=0.05, contact_slope=-1, free_slope=-9000, rms=3.0, samples=50, contact_samples=12)
    return TapResult(
        probe_z=contact_z,
        descent_z=contact_z,
        toolhead_z=z_stop,
        overshoot=contact_z - z_stop,
        tap_start_time=t_contact,
        tap_end_time=t_trigger,
        tap_time=t_contact,
        liftoff=liftoff,
    )


SETTINGS = dict(
    position="descent", profile="default", threshold=250.0, speed=SPEED, lift_speed=LIFT_SPEED, samples=3, max_samples=6,
    samples_stddev=0.01, use_median=True, drive_current=16, adjust_z=-0.02, start_z=3.0, target_z=-0.25,
)


def run(contacts):
    eddy = FakeEddy()
    rng = np.random.default_rng(3)
    caps, results = [], []
    for z in contacts:
        tap = simulate_tap(eddy, 0.0 if z is None else z, rng, missed=z is None)
        caps.append(tap_report.capture_tap(eddy, tap))
        if tap.error is None:
            results.append(tap)
    return caps, results


def test_capture_tap_window_and_signal():
    caps, _ = run([0.01])
    cap = caps[0]
    # ms from the trigger, from 400ms before it to 300ms after the lift
    assert cap.times[0] == pytest.approx(-400, abs=1000 / RATE)
    assert cap.lift_start == pytest.approx(100, abs=0.01)
    assert cap.times[-1] == pytest.approx(400, abs=1000 / RATE)
    assert cap.tap_start == pytest.approx(-20, abs=0.01)
    assert len(cap.times) == len(cap.sensor_z) == len(cap.toolhead_z) == len(cap.signal)
    assert cap.toolhead_z[0] == pytest.approx(0.01 + 0.38 * SPEED, abs=0.02)
    assert cap.signal_peak is not None and cap.signal_peak > 0
    # the sensor at the tap (at contact): the contact Z plus its offset
    assert cap.sensor_at_tap == pytest.approx(0.01 + SENSOR_OFFSET, abs=0.003)


def test_capture_tap_toolhead_stops_at_the_halt_position():
    eddy = FakeEddy()
    tap = simulate_tap(eddy, 0.01, np.random.default_rng(0))
    planned = eddy._kin
    t_lift = eddy._last_sampler.memos["lift_start_time"]

    # the move history continues down to target_z after the trigger, until
    # homing sets the halt position
    def kin(t):
        if tap.tap_end_time <= t < t_lift:
            return max(-1.0, tap.toolhead_z - (t - tap.tap_end_time) * SPEED)
        return planned(t)

    eddy._kin = kin
    cap = tap_report.capture_tap(eddy, tap)
    zs = [z for t, z in zip(cap.times, cap.toolhead_z) if t < cap.lift_start]
    assert min(zs) == pytest.approx(tap.toolhead_z)
    # before the halt and after the lift, the move history is used as is
    assert cap.toolhead_z[0] == pytest.approx(planned(tap.tap_end_time - 0.4), abs=0.02)
    assert cap.toolhead_z[-1] > tap.toolhead_z + 0.5


def test_capture_tap_without_sampler():
    eddy = FakeEddy()
    tap = TapResult(error=Exception("Sensor error"))
    cap = tap_report.capture_tap(eddy, tap)
    assert cap.tap is tap and cap.times == []


def test_report_data_marks_used_taps():
    contacts = [0.010, 0.012, None, 0.050, 0.011]
    caps, results = run(contacts)
    tap_z, stddev, _ = compute_tap_z(results, 3, 0.01, True)
    data = tap_report.build_report_data(caps, results, SETTINGS, tap_z, stddev, {"command": "PROBE_EDDY_NG_TAP"})

    assert data["result"]["ok"]
    assert data["result"]["tap_z"] == pytest.approx(0.011)
    assert data["result"]["adjusted_z"] == pytest.approx(0.011 - 0.02)
    assert [t["used"] for t in data["taps"]] == [True, True, False, False, True]
    assert [t["ok"] for t in data["taps"]] == [True, True, False, True, True]
    assert data["taps"][2]["descent"] is None and data["taps"][2]["missed"]

    s = data["summary"]
    assert s["n_taps"] == 5 and s["n_failed"] == 1 and s["n_used"] == 3
    assert s["descent"]["mean"] == pytest.approx(0.011)
    assert s["delta"]["mean"] == pytest.approx(0.03)
    assert s["liftoff"]["n"] == 3

    comp = data["composite"]
    assert len(comp["t"]) == len(comp["signal"]) == len(comp["relative_z"])
    # before contact, the sensor is above each tap's descent Z; after, at it
    i_before = comp["t"].index(-100.0)
    i_after = comp["t"].index(50.0)
    assert comp["relative_z"][i_before] == pytest.approx(0.08 * SPEED, abs=0.01)
    assert comp["relative_z"][i_after] == pytest.approx(0.0, abs=0.005)


def test_report_data_failed_run():
    caps, results = run([0.0, 0.1, None])
    data = tap_report.build_report_data(caps, results, SETTINGS, None, None, {})
    assert not data["result"]["ok"]
    assert data["result"]["adjusted_z"] is None
    # not enough taps for a cluster: the summary is over the good taps
    assert data["summary"]["basis"] == "good"
    assert data["summary"]["descent"]["n"] == 2
    assert not any(t["used"] for t in data["taps"])


def test_write_tap_report(tmp_path):
    caps, results = run([0.010, 0.012, 0.011])
    tap_z, stddev, _ = compute_tap_z(results, 3, 0.01, True)
    path = str(tmp_path / "tap-report.html")
    assert tap_report.write_tap_report(FakeEddy(), caps, results, SETTINGS, tap_z, stddev, {"command": "TAP"}, path=path) == path
    html = open(path).read()
    m = re.search(r"const DATA = (.*?);\n", html)
    data = json.loads(m.group(1))
    assert data["header"]["path"] == path
    assert len(data["taps"]) == 3
    # nothing to report removes an old report
    assert tap_report.write_tap_report(FakeEddy(), [], [], SETTINGS, None, None, {}, path=path) is None
    assert not (tmp_path / "tap-report.html").exists()


def test_report_escapes_script_end():
    caps, results = run([0.01])
    caps[0].tap.error = Exception("</script><b>")
    html = tap_report.render_report(tap_report.build_report_data(caps, [], SETTINGS, None, None, {}))
    assert "</script><b>" not in html


def test_write_tap_report_config():
    from fakes import FakeConfig

    params = ProbeEddyParams()
    assert params.write_tap_report is False
    config = FakeConfig({"x_offset": 0, "y_offset": 20, "write_tap_report": "true"})
    params.load_from_config(config)
    assert params.write_tap_report is True
