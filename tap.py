# EDDY-ng
#
# Copyright (C) 2025  Vladimir Vukicevic <vladimir@pobox.com>
#
# Tap results, the tap filter, and combining multiple taps
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .params import ProbeEddyParams

try:
    import scipy  # noqa
except ImportError:
    scipy = None


@dataclass
class TapResult:
    error: Optional[Exception]
    probe_z: float
    toolhead_z: float
    overshoot: float
    tap_time: float
    tap_start_time: float
    tap_end_time: float


@dataclass
class TapConfig:
    threshold: float
    sos: List[List[float]]


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

