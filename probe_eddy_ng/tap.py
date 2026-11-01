# EDDY-ng
#
# Copyright (C) 2025  Vladimir Vukicevic <vladimir@pobox.com>
#
# Tap results, the tap filter, and combining multiple taps
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Tuple, final, TYPE_CHECKING

import numpy as np

from .compat import GCodeCommand, manual_probe, HAS_PROBE_RESULT_TYPE
from .liftoff import LiftoffFit

if TYPE_CHECKING:
    from .params import ProbeEddyParams
    from .probe_eddy_ng import ProbeEddy

try:
    import scipy  # noqa
except ImportError:
    scipy = None


@dataclass
class TapResult:
    error: Optional[Exception] = None
    probe_z: float = 0.0
    toolhead_z: float = 0.0
    overshoot: float = 0.0
    tap_time: float = 0.0
    tap_start_time: float = 0.0
    tap_end_time: float = 0.0
    # The move ended without a tap being detected
    missed: bool = False
    # The largest tap signal (drop from a peak, in Hz, the value compared to
    # the threshold) while the toolhead moved at the tap speed, if known
    signal_peak: Optional[float] = None
    # Where the tap was detected on the way down; probe_z, unless the tap
    # position is the lift-off point
    descent_z: float = 0.0
    # Where the nozzle left the bed as the toolhead lifted afterwards
    liftoff: Optional[LiftoffFit] = None


@dataclass
class TapConfig:
    threshold: float
    sos: List[List[float]]
    # The frequencies to pass through before a tap is looked for, when
    # they don't come from the calibration (see run_taps)
    safe_freq: Optional[float] = None
    trigger_freq: Optional[float] = None


# Compute the average tap_z from a set of tap results, taking the cluster of
# `samples` taps with the lowest standard deviation
def compute_tap_z(taps: List[TapResult], samples: int, req_stddev: float, use_median: bool) -> Tuple[float, float, float]:
    if len(taps) < samples:
        return None, None, None

    # The lowest-stddev subset of a given size is always a run of
    # consecutive values once they're sorted, so only those need checking.
    taps = sorted(taps, key=lambda t: t.probe_z)
    tap_zs = np.array([t.probe_z for t in taps])
    start = min(range(len(taps) - samples + 1), key=lambda i: np.std(tap_zs[i : i + samples]))
    cluster = taps[start : start + samples]
    std_min = float(np.std(tap_zs[start : start + samples]))

    if std_min > req_stddev:
        return None, std_min, None
    if use_median:
        # the cluster is sorted; use the overshoot of the same tap
        mid = cluster[samples // 2]
        return float(mid.probe_z), std_min, float(mid.overshoot)
    return float(np.mean([t.probe_z for t in cluster])), std_min, float(np.mean([t.overshoot for t in cluster]))


# The tap filter's second-order sections. The default filter at the common
# data rates is precomputed so scipy isn't needed.
def tap_filter_sos(params: ProbeEddyParams, data_rate: int, config_error) -> List[List[float]]:
    # fmt: off
    if params.is_default_butter_config() and data_rate == 250:
        return [
            [ 0.046131802093312926, 0.09226360418662585, 0.046131802093312926, 1.0, -1.3297767184682712, 0.5693902189294331, ],
            [ 1.0, -2.0, 1.0, 1.0, -1.845000600983779, 0.8637525213328747, ],
        ]
    if params.is_default_butter_config() and data_rate == 500:
        return [
            [ 0.013359200027856505, 0.02671840005571301, 0.013359200027856505, 1.0, -1.686278256753083, 0.753714473246724, ],
            [ 1.0, -2.0, 1.0, 1.0, -1.9250515947328444, 0.9299234737648037, ],
        ]
    # fmt: on
    if not scipy:
        raise config_error(
            "ProbeEddy: a custom tap filter (tap_butter_* settings) or a samples_per_second other than 250 or 500 "
            "requires scipy, which is not available; please install scipy or use the defaults"
        )
    return scipy.signal.butter(
        params.tap_butter_order,
        [params.tap_butter_lowcut, params.tap_butter_highcut],
        btype="bandpass",
        fs=data_rate,
        output="sos",
    ).tolist()


# The sensor firmware's tap detection (check_sos_tap), replayed on samples:
# returns, for each sample, the tap signal the firmware compares to the
# threshold (how far the filtered value has dropped from its last peak), or
# None while the tap isn't armed yet or for error (nan) samples, which the
# firmware skips. The filter starts at the first sample at or above
# safe_freq, relative to that sample, and the signal is tracked from the
# first sample at or above trigger_freq.
#
# The firmware filters raw sample values in float32; this filters the same
# differences in Hz, which only scales them (the threshold is scaled the same
# way), so the results match up to rounding.
def replay_tap_signal(sos: List[List[float]], freqs: List[float], safe_freq: float, trigger_freq: float) -> List[Optional[float]]:
    signal: List[Optional[float]] = [None] * len(freqs)
    state = [[0.0, 0.0] for _ in sos]
    offset = None
    armed = False
    start_value = last_value = 0.0

    for i, freq in enumerate(freqs):
        if freq != freq:  # nan
            continue
        if offset is None:
            if freq >= safe_freq:
                offset = freq
            continue

        # direct form II transposed, section by section
        val = freq - offset
        for (b0, b1, b2, _, a1, a2), z in zip(sos, state):
            out = b0 * val + z[0]
            z[0] = b1 * val - a1 * out + z[1]
            z[1] = b2 * val - a2 * out
            val = out

        if not armed:
            if freq < trigger_freq:
                continue
            armed = True

        if val > last_value:
            start_value = val
        signal[i] = start_value - val
        last_value = val

    return signal


# Klipper's probe commands (PROBE, QUAD_GANTRY_LEVEL etc.) select a tap
# with METHOD=tap
def is_tap_request(gcmd: Optional[GCodeCommand]) -> bool:
    return gcmd is not None and gcmd.get("METHOD", "automatic").lower() == "tap"


# Probe session for METHOD=tap: taps at the current XY position (taking the
# same parameters as PROBE_EDDY_NG_TAP) and reports where the nozzle touched
# the bed, plus tap_adjust_z. Unlike PROBE_EDDY_NG_TAP, it doesn't change the
# Z position, the gcode offset or the sensor tap offset.
@final
class ProbeEddyTapSession:
    def __init__(self, eddy: ProbeEddy):
        self.eddy = eddy
        self._toolhead = eddy._printer.lookup_object("toolhead")
        self._results = []

    def run_probe(self, gcmd: GCodeCommand, *args: Any, **kwargs: Any):
        th_pos = self._toolhead.get_position()
        tap_z, _, _ = self.eddy.run_taps(gcmd, self.eddy._sensor.get_drive_current())
        contact_z = float(tap_z + gcmd.get_float("ADJUST_Z", self.eddy._tap_adjust_z))
        self.eddy._log_msg(f"Tap contact at z={contact_z:.3f} (tap at z={tap_z:.3f})")

        # The nozzle is the probe, so the bed position is the toolhead position
        if HAS_PROBE_RESULT_TYPE:
            res = manual_probe.ProbeResult(th_pos[0], th_pos[1], contact_z, th_pos[0], th_pos[1], float(tap_z))
        else:
            res = [th_pos[0], th_pos[1], contact_z]
        self._results.append(res)

    def pull_probed_results(self):
        res = self._results
        self._results = []
        return res

    def end_probe_session(self):
        self._results = []
