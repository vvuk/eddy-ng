# EDDY-ng
#
# Copyright (C) 2025  Vladimir Vukicevic <vladimir@pobox.com>
#
# Debug plots of taps and calibrations (written when plotly is available)
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .fmap import ProbeEddyFrequencyMap
    from .probe_eddy_ng import ProbeEddy
    from .sampler import ProbeEddySampler
    from .tap import TapResult

try:
    import plotly  # noqa
except ImportError:
    plotly = None

try:
    import scipy  # noqa
except ImportError:
    scipy = None


def compute_butter_tap(eddy: ProbeEddy, sampler: ProbeEddySampler):
    if not scipy:
        return None, None, None

    # the frequency the firmware starts filtering at: the tap's first gate
    trigger_freq = sampler.memos.get("tap_safe_freq")
    if trigger_freq is None:
        trigger_freq = eddy.height_to_freq(eddy.params.home_trigger_height)

    s_t = np.asarray(sampler.times)
    s_f = np.asarray(sampler.freqs)
    first_one = np.argmax(s_f >= trigger_freq)
    # the firmware skips error samples, so the filter here does too
    valid = ~np.isnan(s_f[first_one:])
    s_t = s_t[first_one:][valid]
    s_f = s_f[first_one:][valid]

    lowcut = eddy.params.tap_butter_lowcut
    highcut = eddy.params.tap_butter_highcut
    order = eddy.params.tap_butter_order

    sos = scipy.signal.butter(
        order,
        [lowcut, highcut],
        btype="bandpass",
        fs=eddy._sensor.data_rate,
        output="sos",
    )
    filtered = scipy.signal.sosfilt(sos, s_f - s_f[0])

    return s_t, filtered, first_one


# Write a tap plot. This also has logic to compute the averages
# and the filter mostly-exactly how it's done on the probe MCU itself
# (vs using numpy or similar) to make these graphs more reprensetative
def write_tap_plot(eddy: ProbeEddy, tap: TapResult, tapnum: int = -1):
    if not plotly:
        return

    if tapnum == -1:
        filename_base = "tap"
    else:
        filename_base = f"tap-{tapnum + 1}"
    tapplot_path_png = f"/tmp/{filename_base}.png"
    tapplot_path_html = f"/tmp/{filename_base}.html"

    # delete any old plots to avoid confusion
    if tapplot_path_html and os.path.exists(tapplot_path_html):
        os.remove(tapplot_path_html)
    if tapplot_path_png and os.path.exists(tapplot_path_png):
        os.remove(tapplot_path_png)

    if not eddy._last_sampler or not eddy._last_sampler.times:
        return

    s_t = np.asarray(eddy._last_sampler.times)
    s_f = np.asarray(eddy._last_sampler.freqs)
    # AUTO_SETUP taps before there's a calibration, so there are no heights
    if eddy._last_sampler.heights is not None:
        s_z = np.asarray(eddy._last_sampler.heights)
    else:
        s_z = np.full(len(s_t), np.nan)
    s_accval = np.asarray(eddy._last_sampler.accum_vals)
    s_kinz = np.vectorize(lambda t: eddy._get_trapq_height(t) or -10)(s_t)

    # Any values below 0.0 are suspect because they were not calibrated,
    # and so are just extrapolated from the fit. Show them differently.
    s_lowz = np.ma.masked_where(s_z >= 0, s_z)
    s_z = np.ma.masked_where(s_z < 0, s_z)

    time_start = s_t.min()

    # normalize times to start at 0
    s_t = s_t - time_start
    tap_start_time = eddy._last_sampler.memos.get("tap_start_time", time_start) - time_start
    tap_end_time = eddy._last_sampler.memos.get("trigger_time", time_start) - time_start
    trigger_time = eddy.tap_time(tap_start_time, tap_end_time)
    tap_threshold = eddy._last_sampler.memos.get("tap_threshold", 0)

    time_len = s_t.max()

    # compute the butterworth filter, if we have scipy
    if tap is not None and scipy:
        butter_s_t, butter_s_v, butter_index_start = compute_butter_tap(eddy, eddy._last_sampler)
        butter_s_t = butter_s_t - time_start
    else:
        butter_s_t = butter_s_v = None
        butter_index_start = 0

    # Do this roughly how the C code does it, to keep the values identical
    # The C code computes: diff = tap_start_value - val
    # where tap_start_value is the most recent peak.
    # It does NOT accumulate drops, it measures distance from peak.
    butter_accum = None
    if butter_s_v is not None:
        butter_accum = np.zeros(len(butter_s_v))
        peak_value = butter_s_v[0]  # tap_start_value in C code
        last_value = butter_s_v[0]
        for bi, bv in enumerate(butter_s_v):
            if bv > last_value:
                # Update peak (matches C: sos_tap->tap_start_value = val)
                peak_value = bv
            # Compute drop from peak (matches C: diff = tap_start_value - val)
            butter_accum[bi] = peak_value - bv
            last_value = bv

    import plotly.graph_objects as go

    (c_red, c_lt_red) = ("#9e4058", "#C2697F")
    (c_orange, c_lt_orange) = ("#d0641e", "#E68E54")
    (c_yellow, c_lt_yellow) = ("#f9ab0e", '"#FBC559')
    (c_green, c_lt_green) = ("#589e40", "#7FC269")
    (c_blue, c_lt_blue) = ("#2c3778", "#4151B0")
    (c_purple, c_lt_purple) = ("#513965", "#785596")

    fig = go.Figure()

    # fmt: off
    if tap_start_time > 0:
        fig.add_shape(type="line", x0=tap_start_time, x1=tap_start_time, y0=0, y1=1,
                      xref="x", yref="paper", line=dict(color=c_purple, width=2),)
    if trigger_time > 0:
        fig.add_shape(type="line", x0=trigger_time, x1=trigger_time, y0=0, y1=1,
                      xref="x", yref="paper", line=dict(color=c_lt_orange, width=2),)
    if tap_end_time > 0:
        fig.add_shape(type="line", x0=tap_end_time, x1=tap_end_time, y0=0, y1=1,
                      xref="x", yref="paper", line=dict(color=c_purple, width=2),)
    if tap_threshold > 0:
        fig.add_shape(type="line", x0=0, x1=1, y0=tap_threshold, y1=tap_threshold,
                      xref="paper", yref="y3", line=dict(color="gray", width=1, dash="dash"),)

    fig.add_shape(type="line", x0=0, x1=1, y0=tap.probe_z, y1=tap.probe_z,
                  xref="paper", yref="y", line=dict(color=c_lt_orange, width=1),)

    # Computed Z, Toolhead Z, Sensor F
    fig.add_trace(go.Scatter(x=s_t, y=s_z, mode="lines", name="Z", line=dict(color=c_blue)))
    fig.add_trace(go.Scatter(x=s_t, y=s_lowz, mode="lines", name="Z (low)", line=dict(color=c_lt_blue, dash="dash")))
    fig.add_trace(go.Scatter(x=s_t, y=s_kinz, mode="lines", name="KinZ", line=dict(color=c_lt_red)))
    fig.add_trace(go.Scatter(x=s_t, y=s_f, mode="lines", name="Freq", yaxis="y2", line=dict(color=c_orange)))

    # the butter tap if we have the data
    if butter_s_t is not None:
        fig.add_trace(go.Scatter(x=butter_s_t, y=butter_s_v, mode="lines", name="signal", yaxis="y4", line=dict(color=c_green)))
        fig.add_trace(go.Scatter(x=butter_s_t, y=butter_accum, mode="lines", name="accum_py", yaxis="y3", line=dict(color="#626b73")))
        fig.add_trace(go.Scatter(x=s_t[butter_index_start:], y=s_accval[butter_index_start:], mode="lines", name="accum_mcu", yaxis="y3", line=dict(color="#c26ba3")))

    # The samples continue through the lift after the tap; show the end of
    # the tap move and the start of the lift
    lift_start_time = eddy._last_sampler.memos.get("lift_start_time")
    view_end = time_len
    if lift_start_time is not None:
        lift_start_rel = lift_start_time - time_start
        view_end = min(time_len, lift_start_rel + 0.30)
        fig.add_shape(type="line", x0=lift_start_rel, x1=lift_start_rel, y0=0, y1=1,
                      xref="x", yref="paper", line=dict(color="gray", width=1, dash="dot"),)
    liftoff = eddy._last_sampler.memos.get("liftoff")
    if liftoff is not None and not liftoff.error:
        fig.add_shape(type="line", x0=0, x1=1, y0=liftoff.z, y1=liftoff.z,
                      xref="paper", yref="y", line=dict(color=c_green, width=1, dash="dash"),)
    fig.update_xaxes(range=[max(0.0, view_end - 0.90), view_end], autorange=False)

    fig.update_layout(
        hovermode="x unified",
        title=dict(text=f"Tap {tapnum+1}: {tap.probe_z:.3f}"),
        yaxis=dict(title="Z", side="right"),  # Z axis
        yaxis2=dict(overlaying="y", title="Freq", tickformat="d", side="left"),  # Freq + WMA
        yaxis3=dict(overlaying="y", side="left", tickformat="d", position=0.2),  # derivatives, tap accum
        yaxis4=dict(overlaying="y", side="right", showticklabels=False),  # filter
        height=800,
    )
    # fmt: on

    timg = 0.0
    thtml = 0.0
    if tapplot_path_png:
        t0 = time.time()
        try:
            fig.write_image(tapplot_path_png)
        except:
            tapplot_path_png = None
        timg = time.time() - t0
    if tapplot_path_html:
        t0 = time.time()
        fig.write_html(tapplot_path_html, include_plotlyjs="cdn")
        thtml = time.time() - t0
    eddy._log_info(f"Wrote tap plot to {tapplot_path_png or ''} {tapplot_path_html or ''}  [took {timg:.1f}, {thtml:.1f}]")



def write_calibration_plot(
    fmap: ProbeEddyFrequencyMap,
    times,
    freqs,
    heights,
    rmse_fth,
    vels=None,
):
    if not plotly:
        return

    if fmap._ftoh is None:
        logging.warning("write_calibration_plot: null calibration?")
        return

    import plotly.graph_objects as go

    low_samples = heights <= fmap.low_z_threshold
    high_samples = heights >= fmap.low_z_threshold - 0.5

    f_to_z_low_err = heights[low_samples] - fmap._ftoh(1.0 / freqs[low_samples])

    if fmap._ftoh_high is not None:
        f_to_z_high_err = heights[high_samples] - fmap._ftoh_high(1.0 / freqs[high_samples])
    else:
        f_to_z_high_err = None

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=times, y=heights, mode="lines", name="Z"))

    fig.add_trace(
        go.Scatter(
            x=times[low_samples],
            y=fmap._ftoh(1.0 / freqs[low_samples]),
            mode="lines",
            name=f"Z {rmse_fth:.4f}",
        )
    )

    if fmap._ftoh_high is not None:
        fig.add_trace(
            go.Scatter(
                x=times[high_samples],
                y=fmap._ftoh_high(1.0 / freqs[high_samples]),
                mode="lines",
                name="Z (high)",
            )
        )

    fig.add_trace(go.Scatter(x=times, y=freqs, mode="lines", name="F", yaxis="y2"))

    fig.add_trace(
        go.Scatter(
            x=times[low_samples],
            y=1.0 / fmap._heights_to_invfreqs(heights[low_samples]),
            mode="lines",
            name="F (fit)",
            yaxis="y2",
        )
    )

    fig.add_trace(
        go.Scatter(
            x=times[low_samples],
            y=f_to_z_low_err,
            mode="lines",
            name="Err",
            yaxis="y3",
        )
    )
    if f_to_z_high_err is not None:
        fig.add_trace(
            go.Scatter(
                x=times[high_samples],
                y=f_to_z_high_err,
                mode="lines",
                name="Err (high)",
                yaxis="y3",
            )
        )

    if vels is not None:
        fig.add_trace(go.Scatter(x=times, y=vels, mode="lines", name="V", yaxis="y4"))

    fig.update_layout(
        hovermode="x unified",
        title=f"Calibration for drive current {fmap.drive_current}",
        yaxis2=dict(title="Freq", overlaying="y", tickformat="d", side="right"),
        yaxis3=dict(overlaying="y", side="right", position=0.1),
        yaxis4=dict(overlaying="y", side="right", position=0.2),
    )
    fig.write_html("/tmp/eddy-calibration.html")

