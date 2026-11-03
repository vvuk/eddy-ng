# EDDY-ng
#
# Copyright (C) 2025  Vladimir Vukicevic <vladimir@pobox.com>
#
# Tap report: one HTML page summarizing all the taps of a TAP command, with
# the tap positions, their spread, all the taps overlaid and a graph of
# each tap. The page draws its graphs in the browser with plotly.js (loaded
# from plotly's CDN, like the tap plots), so the host only needs numpy.
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

import json
import logging
import math
import os
import time
import warnings
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import numpy as np

from .tap import TapResult, replay_tap_signal, tap_cluster

if TYPE_CHECKING:
    from .probe_eddy_ng import ProbeEddy

REPORT_PATH = "/tmp/tap-report.html"

# How much of each tap to keep, around the trigger (or the end of the move,
# for a tap that didn't trigger) and the start of the lift
WINDOW_BEFORE = 0.400
WINDOW_AFTER_LIFT = 0.300

# The grid the overlaid taps' medians are computed on, in ms from the trigger
COMPOSITE_STEP_MS = 2.0


# One tap's samples, taken right after the tap (the next tap replaces the
# sampler, and the toolhead's move history doesn't last). Times are in ms
# from the trigger.
@dataclass
class TapCapture:
    tap: TapResult
    times: List[float] = field(default_factory=list)
    sensor_z: List[Optional[float]] = field(default_factory=list)
    toolhead_z: List[Optional[float]] = field(default_factory=list)
    signal: List[Optional[float]] = field(default_factory=list)
    lift_start: Optional[float] = None
    tap_start: Optional[float] = None
    # the largest tap signal before the lift
    signal_peak: Optional[float] = None


def _num(v: Any, digits: int) -> Optional[float]:
    if v is None:
        return None
    v = float(v)
    if math.isnan(v) or math.isinf(v):
        return None
    return round(v, digits)


# Collect the last tap's samples from eddy's last sampler (none if there
# wasn't one)
def capture_tap(eddy: ProbeEddy, tap: TapResult) -> TapCapture:
    sampler = eddy._last_sampler
    if sampler is None or not sampler.times:
        return TapCapture(tap=tap)

    times = list(sampler.times)
    freqs = list(sampler.freqs)
    heights = list(sampler.heights) if sampler.heights is not None else None
    memos = sampler.memos

    lift_start_time = memos.get("lift_start_time")
    if tap.error is None and tap.tap_end_time:
        ref = tap.tap_end_time
    else:
        ref = memos.get("trigger_time") or lift_start_time or times[-1]
    end = (lift_start_time if lift_start_time is not None else ref) + WINDOW_AFTER_LIFT

    safe_freq = memos.get("tap_safe_freq")
    trigger_freq = memos.get("tap_trigger_freq")
    if safe_freq is not None and trigger_freq is not None:
        signal = replay_tap_signal(eddy._tap_sos, freqs, safe_freq, trigger_freq)
    else:
        signal = [None] * len(freqs)

    cap = TapCapture(tap=tap)
    peak = None
    for i, t in enumerate(times):
        if t < ref - WINDOW_BEFORE:
            continue
        if t > end:
            break
        s = signal[i]
        if s is not None and (lift_start_time is None or t < lift_start_time):
            peak = s if peak is None else max(peak, s)
        cap.times.append(round((t - ref) * 1000.0, 2))
        cap.sensor_z.append(_num(heights[i], 5) if heights is not None else None)
        cap.toolhead_z.append(_num(eddy._get_trapq_height(t), 5))
        cap.signal.append(_num(s, 1))
    cap.signal_peak = peak
    if lift_start_time is not None:
        cap.lift_start = round((lift_start_time - ref) * 1000.0, 2)
    tap_start_time = memos.get("tap_start_time")
    if tap_start_time is not None:
        cap.tap_start = round((tap_start_time - ref) * 1000.0, 2)
    return cap


def _stats(values: List[float]) -> Optional[Dict[str, float]]:
    if not values:
        return None
    a = np.array(values, dtype=float)
    return {
        "mean": float(a.mean()),
        "std": float(a.std()),
        "min": float(a.min()),
        "max": float(a.max()),
        "n": len(values),
    }


def _liftoff_z(tap: TapResult) -> Optional[float]:
    if tap.liftoff is None or tap.liftoff.error:
        return None
    return tap.liftoff.z


# The median of the taps' values on a common time grid, for the overlay
def _composite_median(caps: List[TapCapture], values) -> Optional[List[Optional[float]]]:
    grid = _composite_grid(caps)
    if grid is None:
        return None
    rows = []
    for cap in caps:
        ys = values(cap)
        pts = [(t, y) for t, y in zip(cap.times, ys) if y is not None]
        if len(pts) < 2:
            continue
        t = np.array([p[0] for p in pts])
        y = np.array([p[1] for p in pts])
        row = np.interp(grid, t, y)
        row[(grid < t[0]) | (grid > t[-1])] = np.nan
        rows.append(row)
    if not rows:
        return None
    with warnings.catch_warnings():
        # columns where no tap has a value
        warnings.simplefilter("ignore", category=RuntimeWarning)
        med = np.nanmedian(np.array(rows), axis=0)
    return [_num(v, 5) for v in med]


def _composite_grid(caps: List[TapCapture]) -> Optional[np.ndarray]:
    starts = [c.times[0] for c in caps if c.times]
    ends = [c.times[-1] for c in caps if c.times]
    if not starts:
        return None
    return np.arange(math.floor(min(starts)), math.ceil(max(ends)) + COMPOSITE_STEP_MS, COMPOSITE_STEP_MS)


# Everything the page shows, as plain JSON-able values
def build_report_data(
    caps: List[TapCapture],
    results: List[TapResult],
    settings: Dict[str, Any],
    tap_z: Optional[float],
    tap_stddev: Optional[float],
    header: Dict[str, Any],
) -> Dict[str, Any]:
    samples = settings["samples"]
    good = [c.tap for c in caps if c.tap.error is None]
    # results only has the good taps, but the report may have been asked for
    # for a run that stopped early; fall back to what was captured
    pool = results or good
    cluster: List[TapResult] = []
    cluster_std = None
    if len(pool) >= samples:
        cluster, cluster_std = tap_cluster(pool, samples)
    cluster_ids = set(id(t) for t in cluster)
    ok = tap_z is not None

    # the summary is over the taps that made up the tap Z (or the closest
    # cluster, if there wasn't one), else all the good taps
    stat_taps = cluster or good
    descents = [t.descent_z for t in stat_taps]
    liftoffs = [_liftoff_z(t) for t in stat_taps if _liftoff_z(t) is not None]
    deltas = [_liftoff_z(t) - t.descent_z for t in stat_taps if _liftoff_z(t) is not None]
    peaks = [c.signal_peak for c in caps if c.tap.error is None and id(c.tap) in cluster_ids and c.signal_peak is not None]

    taps = []
    for n, cap in enumerate(caps, start=1):
        t = cap.tap
        lz = _liftoff_z(t)
        lo = t.liftoff
        entry = {
            "n": n,
            "ok": t.error is None,
            "error": str(t.error) if t.error is not None else None,
            "missed": t.missed,
            "used": id(t) in cluster_ids,
            # a tap that failed only for its lift-off still has a descent
            "descent": _num(t.descent_z, 5) if t.error is None or t.liftoff is not None else None,
            "liftoff": _num(lz, 5),
            "liftoff_error": lo.error if lo is not None and lo.error else (None if lo is not None else "no lift-off fit"),
            "delta": _num(lz - t.descent_z, 5) if lz is not None else None,
            "toolhead": _num(t.toolhead_z, 5),
            "overshoot": _num(t.overshoot, 5) if t.error is None else None,
            "depress": _num(lo.depress, 5) if lz is not None else None,
            "contact_samples": lo.contact_samples if lz is not None else None,
            "fit_samples": lo.samples if lz is not None else None,
            "slope_ratio": _num(lo.slope_ratio, 3) if lz is not None else None,
            "rms": _num(lo.rms, 2) if lz is not None else None,
            "signal_peak": _num(t.signal_peak if t.signal_peak is not None else cap.signal_peak, 1),
            "lift_start": cap.lift_start,
            "tap_start": cap.tap_start,
            "t": cap.times,
            "sensor_z": cap.sensor_z,
            "toolhead_z": cap.toolhead_z,
            "signal": cap.signal,
        }
        taps.append(entry)

    good_caps = [c for c in caps if c.tap.error is None]
    composite = None
    grid = _composite_grid(good_caps)
    if grid is not None:
        composite = {
            "t": [round(float(v), 2) for v in grid],
            "signal": _composite_median(good_caps, lambda c: c.signal),
            "relative_z": _composite_median(
                good_caps,
                lambda c: [None if z is None else z - c.tap.descent_z for z in c.sensor_z],
            ),
        }

    return {
        "header": header,
        "settings": settings,
        "result": {
            "ok": ok,
            "tap_z": _num(tap_z, 5),
            "stddev": _num(tap_stddev, 5),
            "cluster_stddev": _num(cluster_std, 5),
            "adjusted_z": _num(tap_z + settings["adjust_z"], 5) if ok else None,
        },
        "summary": {
            "basis": "used" if cluster else "good",
            "descent": _stats(descents),
            "liftoff": _stats(liftoffs),
            "delta": _stats(deltas),
            "overshoot": _stats([t.overshoot for t in stat_taps]),
            "signal_peak": _stats(peaks),
            "n_taps": len(caps),
            "n_failed": len(caps) - len(good),
            "n_used": len(cluster),
        },
        "composite": composite,
        "taps": taps,
    }


def render_report(data: Dict[str, Any]) -> str:
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    return _TEMPLATE.replace("__TAP_REPORT_DATA__", payload)


# Write the report; returns the path written, or None if there was nothing
# to report
def write_tap_report(
    eddy: ProbeEddy,
    caps: List[TapCapture],
    results: List[TapResult],
    settings: Dict[str, Any],
    tap_z: Optional[float],
    tap_stddev: Optional[float],
    header: Dict[str, Any],
    path: str = REPORT_PATH,
) -> Optional[str]:
    if os.path.exists(path):
        os.remove(path)
    if not caps:
        return None
    t0 = time.time()
    header = dict(header)
    header.setdefault("generated", time.strftime("%Y-%m-%d %H:%M:%S"))
    header.setdefault("path", path)
    data = build_report_data(caps, results, settings, tap_z, tap_stddev, header)
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_report(data))
    logging.info(f"EDDYng: wrote tap report to {path} in {time.time() - t0:.2f}s")
    return path


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>eddy-ng Tap Report</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Semi+Condensed:wght@500;600;700&family=Source+Sans+3:wght@400;600&family=JetBrains+Mono:wght@400;500&display=swap">
<style>
:root {
  --bg: #f6f7f9; --panel: #ffffff; --fg: #14181f; --fg-2: #4b5361; --fg-3: #7b8494;
  --rule: #dfe3e9; --grid: #e9ecf1; --descent: #2a78d6; --liftoff: #eb6834;
  --band-d: rgba(42,120,214,.12); --band-l: rgba(235,104,52,.12); --neutral-line: #9aa3b2;
  --ok: #1d7f45; --bad: #c43b3b;
  --font-display: "Barlow Semi Condensed", "Arial Narrow", system-ui, sans-serif;
  --font-body: "Source Sans 3", system-ui, -apple-system, "Segoe UI", sans-serif;
  --font-mono: "JetBrains Mono", ui-monospace, "SFMono-Regular", Menlo, monospace;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #111418; --panel: #191d23; --fg: #eef1f5; --fg-2: #b4bcc8; --fg-3: #858e9c;
    --rule: #2a3039; --grid: #242a32; --descent: #3987e5; --liftoff: #d95926;
    --band-d: rgba(57,135,229,.18); --band-l: rgba(217,89,38,.18); --neutral-line: #5d6675;
    --ok: #4cbf7a; --bad: #e66767; color-scheme: dark;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg); font-family: var(--font-body); font-size: 15px; line-height: 1.5; }
.wrap { max-width: 1180px; margin: 0 auto; padding: 28px 16px 56px; display: grid; gap: 28px; }
header { display: grid; gap: 10px; }
.eyebrow { font-family: var(--font-mono); font-size: 12px; letter-spacing: .08em; text-transform: uppercase; color: var(--fg-3); }
h1 { font-family: var(--font-display); font-weight: 700; font-size: clamp(30px, 4.5vw, 44px); line-height: 1.05; margin: 0; }
h1 .z { color: var(--descent); font-variant-numeric: tabular-nums; }
h1 .z.bad { color: var(--bad); }
.verdict { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; color: var(--fg-2); }
.pill { display: inline-flex; align-items: center; gap: 6px; font-family: var(--font-mono); font-size: 12px; padding: 3px 9px; border-radius: 999px; border: 1px solid currentColor; }
.pill.ok { color: var(--ok); } .pill.bad { color: var(--bad); }
.params { display: flex; flex-wrap: wrap; gap: 6px; margin: 4px 0 0; padding: 0; list-style: none; }
.params li { font-family: var(--font-mono); font-size: 12px; color: var(--fg-2); background: var(--panel); border: 1px solid var(--rule); border-radius: 4px; padding: 3px 8px; }
.params b { color: var(--fg); font-weight: 500; }
.readouts { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 1px; background: var(--rule); border: 1px solid var(--rule); border-radius: 6px; overflow: hidden; }
.readout { background: var(--panel); padding: 14px 16px; display: grid; gap: 2px; align-content: start; }
.readout .k { font-size: 12px; letter-spacing: .06em; text-transform: uppercase; color: var(--fg-3); display: flex; align-items: center; gap: 6px; }
.readout .v { font-family: var(--font-mono); font-size: 22px; font-weight: 500; font-variant-numeric: tabular-nums; }
.readout .v small { font-size: 13px; color: var(--fg-3); font-weight: 400; }
.readout .s { font-size: 13px; color: var(--fg-2); font-variant-numeric: tabular-nums; }
.swatch { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
section { display: grid; gap: 10px; min-width: 0; }
.sec-head { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 4px 16px; border-bottom: 1px solid var(--rule); padding-bottom: 6px; }
h2 { font-family: var(--font-display); font-weight: 600; font-size: 22px; margin: 0; }
.sec-head p { margin: 0; color: var(--fg-2); font-size: 14px; max-width: 70ch; }
.chart { background: var(--panel); border: 1px solid var(--rule); border-radius: 6px; min-width: 0; }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 760px) { .two { grid-template-columns: 1fr; } }
.taps { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px; }
@media (max-width: 400px) { .taps { grid-template-columns: 1fr; } }
.tapcard { background: var(--panel); border: 1px solid var(--rule); border-radius: 6px; display: grid; min-width: 0; }
.tapcard.failed { border-style: dashed; }
.tc-head { display: flex; justify-content: space-between; align-items: baseline; gap: 4px 8px; padding: 10px 12px 0; flex-wrap: wrap; }
.tc-head .n { font-family: var(--font-display); font-weight: 600; font-size: 17px; }
.tc-head .vals { font-family: var(--font-mono); font-size: 12px; color: var(--fg-2); font-variant-numeric: tabular-nums; }
.tc-head .vals .d { color: var(--descent); } .tc-head .vals .l { color: var(--liftoff); }
.tc-tag { font-family: var(--font-mono); font-size: 11px; color: var(--fg-3); }
.tc-tag.bad { color: var(--bad); }
.tc-err { padding: 2px 12px 0; font-size: 12px; color: var(--bad); }
.tc-plot { height: 240px; }
.legend-row { display: flex; flex-wrap: wrap; gap: 14px; font-size: 13px; color: var(--fg-2); }
.legend-row span { display: inline-flex; align-items: center; gap: 6px; }
.legend-row i { display: inline-block; width: 16px; height: 2px; background: currentColor; }
.legend-row i.dash { background: none; border-top: 2px dashed currentColor; height: 0; }
.table-wrap { overflow-x: auto; background: var(--panel); border: 1px solid var(--rule); border-radius: 6px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; font-variant-numeric: tabular-nums; }
th, td { padding: 7px 12px; text-align: right; white-space: nowrap; border-bottom: 1px solid var(--grid); }
th { font-weight: 600; color: var(--fg-3); font-size: 11px; letter-spacing: .06em; text-transform: uppercase; }
td { font-family: var(--font-mono); }
th:first-child, td:first-child, th:last-child, td:last-child { text-align: left; }
td:last-child { white-space: normal; min-width: 200px; }
tr.failed td { color: var(--fg-3); }
tr.used td:first-child::before { content: "\25CF"; color: var(--descent); margin-right: 6px; }
tr:not(.used) td:first-child::before { content: "\25CB"; color: var(--fg-3); margin-right: 6px; }
td .bad { color: var(--bad); font-family: var(--font-body); }
.nochart { padding: 24px; color: var(--fg-3); font-size: 14px; }
footer { color: var(--fg-3); font-size: 12px; font-family: var(--font-mono); }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="eyebrow" id="eyebrow"></div>
    <h1 id="headline"></h1>
    <div class="verdict" id="verdict"></div>
    <ul class="params" id="params"></ul>
  </header>

  <div class="readouts" id="readouts"></div>

  <section>
    <div class="sec-head">
      <h2>Tap positions</h2>
      <p id="pos-note">Where each tap was detected on the way down and where the nozzle left the bed on the way up. Bands show mean &plusmn; 1&sigma; of the taps used. Hollow markers were left out of the tap Z.</p>
    </div>
    <div class="two">
      <div class="chart" id="c-positions" style="height:360px"></div>
      <div class="chart" id="c-dist" style="height:360px"></div>
    </div>
  </section>

  <section id="sec-delta">
    <div class="sec-head">
      <h2>Lift-off &minus; descent</h2>
      <p>The gap between the two positions for each tap. A steady gap means the descent detection is consistent; a drifting one points at the threshold or the filter.</p>
    </div>
    <div class="chart" id="c-delta" style="height:240px"></div>
  </section>

  <section>
    <div class="sec-head">
      <h2>All taps, overlaid</h2>
      <p>Every tap that triggered, aligned at the moment the threshold was crossed (t = 0). The dark line is the median across taps.</p>
    </div>
    <div class="two">
      <div class="chart" id="c-signal" style="height:320px"></div>
      <div class="chart" id="c-height" style="height:320px"></div>
    </div>
  </section>

  <section>
    <div class="sec-head">
      <h2>Individual taps</h2>
      <div class="legend-row" id="tap-legend"></div>
    </div>
    <div class="taps" id="taps"></div>
  </section>

  <section>
    <div class="sec-head"><h2>Tap data</h2></div>
    <div class="table-wrap"><table id="tbl"></table></div>
  </section>

  <footer id="footer"></footer>
</div>

<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<script>
const DATA = __TAP_REPORT_DATA__;
const S = DATA.settings, R = DATA.result, SUM = DATA.summary, TAPS = DATA.taps, COMP = DATA.composite;

const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fx = (x, d) => x == null ? "—" : (x < 0 ? "−" : "") + Math.abs(x).toFixed(d);
const f3 = x => fx(x, 3), f4 = x => fx(x, 4);
const sgn3 = x => x == null ? "—" : (x < 0 ? "−" : "+") + Math.abs(x).toFixed(3);
const okTaps = TAPS.filter(t => t.ok);
const failedTaps = TAPS.filter(t => !t.ok);
const hasLiftoff = okTaps.some(t => t.liftoff != null);
const hasSensorZ = TAPS.some(t => t.sensor_z.some(z => z != null));
const posName = S.position === "liftoff" ? "lift-off" : "descent";

// ---------- header ----------
const H = DATA.header;
document.getElementById("eyebrow").textContent =
  ["eddy-ng", H.command, H.xy ? `X ${H.xy[0].toFixed(1)} Y ${H.xy[1].toFixed(1)}` : null, H.generated].filter(Boolean).join(" · ");
document.getElementById("headline").innerHTML = R.ok
  ? `Tap Z <span class="z">${f3(R.tap_z)}</span> mm`
  : `Tap <span class="z bad">failed</span>`;
let why;
if (R.ok) {
  why = `<span class="pill ok">✓ ${SUM.n_used} taps within ${S.samples_stddev.toFixed(3)} mm</span>`;
} else if (R.cluster_stddev != null) {
  why = `<span class="pill bad">✗ best ${S.samples} taps: σ ${R.cluster_stddev.toFixed(4)} &gt; ${S.samples_stddev.toFixed(3)} mm</span>`;
} else {
  why = `<span class="pill bad">✗ ${okTaps.length} good taps of ${S.samples} needed</span>`;
}
const basis = R.ok ? `${S.use_median ? "median" : "mean"} of ${posName} positions` : "";
document.getElementById("verdict").innerHTML = why +
  `<span>${TAPS.length} taps run · ${failedTaps.length} failed${R.ok ? ` · σ ${f4(R.stddev)} mm · ${basis}` : ""}</span>`;
document.getElementById("params").innerHTML = [
  ["position", S.position], S.profile ? ["profile", S.profile] : null, ["threshold", Math.round(S.threshold)],
  ["speed", S.speed + " mm/s"], ["lift", S.lift_speed + " mm/s"],
  ["samples", `${S.samples} / max ${S.max_samples}`], ["stddev ≤", S.samples_stddev.toFixed(3)],
  ["drive current", S.drive_current], ["adjust_z", f3(S.adjust_z)], ["start_z", f3(S.start_z)], ["target_z", f3(S.target_z)],
].filter(Boolean).map(([k, v]) => `<li>${esc(k)} <b>${esc(v)}</b></li>`).join("");
document.getElementById("footer").textContent = `Written by eddy-ng · ${H.path}`;

const statLine = st => st ? `σ ${st.std.toFixed(4)} · range ${(st.max - st.min).toFixed(4)}` : "no data";
const ofWhich = SUM.basis === "used" ? (R.ok ? `${SUM.n_used} taps used` : `closest ${SUM.n_used} taps`) : "all good taps";
const readouts = [];
if (R.ok) readouts.push({ k: "Tap Z + adjust_z", v: f3(R.adjusted_z), u: "mm", s: `${f3(R.tap_z)} ${S.adjust_z < 0 ? "−" : "+"} ${Math.abs(S.adjust_z).toFixed(3)}` });
readouts.push({ k: "Descent", sw: "--descent", v: f3(SUM.descent && SUM.descent.mean), u: "mm", s: statLine(SUM.descent) });
readouts.push({ k: "Lift-off", sw: "--liftoff", v: f3(SUM.liftoff && SUM.liftoff.mean), u: "mm", s: statLine(SUM.liftoff) });
readouts.push({ k: "Lift-off − descent", v: sgn3(SUM.delta && SUM.delta.mean), u: "mm", s: SUM.delta ? `σ ${SUM.delta.std.toFixed(4)}` : "no lift-off fits" });
readouts.push({ k: "Overshoot", v: f3(SUM.overshoot && SUM.overshoot.mean), u: "mm", s: "toolhead past the tap" });
if (SUM.signal_peak) readouts.push({ k: "Tap signal peak", v: Math.round(SUM.signal_peak.mean), u: "", s: `${(SUM.signal_peak.mean / S.threshold).toFixed(1)}× threshold ${Math.round(S.threshold)}` });
document.getElementById("readouts").innerHTML = readouts.map(r => `<div class="readout"><div class="k">${r.sw ? `<span class="swatch" style="background:var(${r.sw})"></span>` : ""}${r.k}</div>
  <div class="v">${r.v} <small>${r.u}</small></div><div class="s">${r.s}</div></div>`).join("") ;
document.getElementById("pos-note").insertAdjacentHTML("beforeend", ` Summary figures are over the ${ofWhich}.`);
if (!hasLiftoff) document.getElementById("sec-delta").hidden = true;

document.getElementById("tap-legend").innerHTML =
  (hasSensorZ ? `<span style="color:var(--descent)"><i></i>sensor Z</span>` : "") +
  `<span style="color:var(--neutral-line)"><i class="dash"></i>toolhead Z</span><span style="color:var(--liftoff)"><i></i>tap signal</span>`;

// ---------- table ----------
document.getElementById("tbl").innerHTML =
  `<thead><tr><th>Tap</th><th>Descent Z</th><th>Lift-off Z</th><th>Δ</th><th>Toolhead Z</th><th>Overshoot</th><th>Depress</th><th>Contact / fit</th><th>Slope ratio</th><th>Fit RMS</th><th>Signal peak</th><th>Status</th></tr></thead><tbody>` +
  TAPS.map(t => {
    const status = !t.ok ? `<span class="bad">${esc(t.error)}</span>`
      : (t.used ? (R.ok ? "used" : "closest cluster") : "outside cluster") + (t.liftoff == null && t.liftoff_error ? ` · lift-off: ${esc(t.liftoff_error)}` : "");
    return `<tr class="${t.ok ? "" : "failed"} ${t.used ? "used" : ""}"><td>${t.n}</td><td>${f4(t.descent)}</td><td>${f4(t.liftoff)}</td><td>${sgn3(t.delta)}</td>
      <td>${f4(t.toolhead)}</td><td>${f3(t.overshoot)}</td><td>${f3(t.depress)}</td>
      <td>${t.contact_samples == null ? "—" : `${t.contact_samples} / ${t.fit_samples}`}</td><td>${t.slope_ratio == null ? "—" : t.slope_ratio.toFixed(2)}</td>
      <td>${t.rms == null ? "—" : t.rms.toFixed(1) + " Hz"}</td><td>${t.signal_peak == null ? "—" : Math.round(t.signal_peak)}</td><td>${status}</td></tr>`;
  }).join("") + "</tbody>";

// ---------- tap cards ----------
document.getElementById("taps").innerHTML = TAPS.map(t => {
  const head = t.ok
    ? `<span class="vals"><span class="d">↓ ${f3(t.descent)}</span> · <span class="l">↑ ${f3(t.liftoff)}</span>${t.delta != null ? ` · Δ ${sgn3(t.delta)}` : ""}</span>
       <span class="tc-tag">${t.used ? (R.ok ? "used" : "closest cluster") : "not used"}</span>`
    : `<span class="tc-tag bad">failed</span>`;
  return `<div class="tapcard${t.ok ? "" : " failed"}"><div class="tc-head"><span class="n">Tap ${t.n}</span>${head}</div>
    ${t.ok ? "" : `<div class="tc-err">${esc(t.error)}</div>`}<div class="tc-plot" id="tp-${t.n}"></div></div>`;
}).join("");

// ---------- charts ----------
function axis(extra) {
  return Object.assign({ gridcolor: css("--grid"), zeroline: false, linecolor: css("--rule"),
    tickfont: { family: "JetBrains Mono, monospace", size: 11 } }, extra || {});
}
function baseLayout(extra) {
  return Object.assign({
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    font: { family: "Source Sans 3, system-ui, sans-serif", color: css("--fg-2"), size: 12 },
    margin: { l: 60, r: 16, t: 34, b: 40 }, xaxis: axis(), yaxis: axis(),
    hoverlabel: { bgcolor: css("--panel"), bordercolor: css("--rule"), font: { color: css("--fg"), family: "JetBrains Mono, monospace", size: 12 } },
    legend: { orientation: "h", x: 0, y: 1.12, font: { size: 12 } }, showlegend: true,
  }, extra || {});
}
const cfg = { displaylogo: false, responsive: true, modeBarButtonsToRemove: ["lasso2d", "select2d"] };
const title = t => ({ text: t, x: 0.01, xanchor: "left", font: { family: "Barlow Semi Condensed, sans-serif", size: 15, color: css("--fg") } });
const atitle = t => ({ text: t, font: { size: 12 } });
const median = a => { const s = a.filter(v => v != null).sort((x, y) => x - y); if (!s.length) return null; const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const noChart = (id, msg) => { document.getElementById(id).innerHTML = `<div class="nochart">${msg}</div>`; };

function render() {
  if (typeof Plotly === "undefined") {
    ["c-positions", "c-dist", "c-delta", "c-signal", "c-height"].forEach(id => noChart(id, "The graphs need plotly.js, loaded from cdn.plot.ly; check the internet connection and reload."));
    return;
  }
  const D = css("--descent"), L = css("--liftoff"), N = css("--neutral-line"), BAD = css("--bad"), FG = css("--fg"), PANEL = css("--panel");
  const xs = okTaps.map(t => t.n);
  const band = (st, fill) => st ? [{ type: "rect", xref: "paper", x0: 0, x1: 1, y0: st.mean - st.std, y1: st.mean + st.std, fillcolor: fill, line: { width: 0 }, layer: "below" }] : [];
  const hline = (y, color, dash, yref) => ({ type: "line", xref: "paper", yref: yref || "y", x0: 0, x1: 1, y0: y, y1: y, line: { color, width: 1, dash: dash || "dot" } });
  const vline = (x, color, dash) => ({ type: "line", x0: x, x1: x, yref: "paper", y0: 0, y1: 1, line: { color, width: 1, dash: dash || "solid" } });
  const markerFor = (taps, color, size) => ({ size: size || 10, color: taps.map(t => t.used ? color : PANEL), line: { color, width: 2 } });
  const lo = okTaps.filter(t => t.liftoff != null);
  const xrange = [0.4, TAPS.length + 0.6];

  // positions per tap
  const allZ = okTaps.map(t => t.descent).concat(lo.map(t => t.liftoff));
  const failY = allZ.length ? Math.min(...allZ) - 0.008 : 0;
  const posShapes = [...band(SUM.descent, css("--band-d")), ...band(SUM.liftoff, css("--band-l"))];
  if (SUM.descent) posShapes.push(hline(SUM.descent.mean, D));
  if (SUM.liftoff) posShapes.push(hline(SUM.liftoff.mean, L));
  if (R.ok) posShapes.push(hline(R.tap_z, FG, "solid"));
  Plotly.react("c-positions", [
    { x: xs, y: okTaps.map(t => t.descent), mode: "markers", name: "descent", marker: markerFor(okTaps, D), hovertemplate: "tap %{x}<br>descent %{y:.4f} mm<extra></extra>" },
    { x: lo.map(t => t.n), y: lo.map(t => t.liftoff), mode: "markers", name: "lift-off", marker: markerFor(lo, L), hovertemplate: "tap %{x}<br>lift-off %{y:.4f} mm<extra></extra>" },
    { x: failedTaps.map(t => t.n), y: failedTaps.map(() => failY), mode: "markers", name: "failed",
      marker: { symbol: "x-thin", size: 11, line: { color: BAD, width: 2 } }, hovertext: failedTaps.map(t => `tap ${t.n}: ${t.error}`), hoverinfo: "text" },
  ], baseLayout({
    title: title("Z per tap"),
    xaxis: axis({ title: atitle("tap"), dtick: 1, range: xrange }),
    yaxis: axis({ title: atitle("Z (mm)"), tickformat: ".3f" }),
    shapes: posShapes,
    annotations: R.ok ? [{ xref: "paper", x: 1, y: R.tap_z, xanchor: "right", yanchor: "bottom", text: `tap Z ${f3(R.tap_z)}`, showarrow: false,
      font: { family: "JetBrains Mono, monospace", size: 11, color: FG } }] : [],
  }), cfg);

  // spread: strip + mean +- sigma
  const jit = i => ((i * 37) % 11 - 5) / 40;
  const means = [], meanX = [], meanC = [], meanE = [];
  if (SUM.descent) { meanX.push(-0.32); means.push(SUM.descent.mean); meanC.push(D); meanE.push(SUM.descent.std); }
  if (SUM.liftoff) { meanX.push(0.68); means.push(SUM.liftoff.mean); meanC.push(L); meanE.push(SUM.liftoff.std); }
  Plotly.react("c-dist", [
    { x: okTaps.map((t, i) => jit(i)), y: okTaps.map(t => t.descent), mode: "markers", name: "descent", showlegend: false,
      marker: markerFor(okTaps, D, 8), text: okTaps.map(t => `tap ${t.n}`), hovertemplate: "%{text}<br>descent %{y:.4f} mm<extra></extra>" },
    { x: lo.map((t, i) => 1 + jit(i)), y: lo.map(t => t.liftoff), mode: "markers", name: "lift-off", showlegend: false,
      marker: markerFor(lo, L, 8), text: lo.map(t => `tap ${t.n}`), hovertemplate: "%{text}<br>lift-off %{y:.4f} mm<extra></extra>" },
    { x: meanX, y: means, mode: "markers", name: "mean ± 1σ",
      marker: { symbol: "line-ew", size: 18, line: { width: 3, color: meanC } },
      error_y: { type: "data", array: meanE, color: FG, thickness: 1.5, width: 8 },
      hovertemplate: "mean %{y:.4f} mm<extra></extra>" },
  ], baseLayout({
    title: title("Spread"),
    xaxis: axis({ tickvals: [0, 1], ticktext: ["descent", "lift-off"], range: [-0.7, 1.5], tickfont: { size: 12 } }),
    yaxis: axis({ title: atitle("Z (mm)"), tickformat: ".3f" }),
  }), cfg);

  // lift-off - descent
  if (hasLiftoff) {
    Plotly.react("c-delta", [
      { x: lo.map(t => t.n), y: lo.map(t => t.delta), type: "bar", name: "lift-off − descent", width: 0.5,
        marker: { color: lo.map(t => t.used ? N : "rgba(0,0,0,0)"), line: { color: N, width: 1.5 } },
        hovertemplate: "tap %{x}<br>Δ %{y:+.4f} mm<extra></extra>" },
    ], baseLayout({
      showlegend: false, margin: { l: 60, r: 16, t: 14, b: 40 },
      xaxis: axis({ title: atitle("tap"), dtick: 1, range: xrange }),
      yaxis: axis({ title: atitle("Δ (mm)"), tickformat: ".3f", rangemode: "tozero" }),
      shapes: SUM.delta ? [hline(SUM.delta.mean, FG, "dash")] : [],
      annotations: SUM.delta ? [{ xref: "paper", x: 1, y: SUM.delta.mean, xanchor: "right", yanchor: "bottom", showarrow: false,
        text: `mean ${sgn3(SUM.delta.mean)} · σ ${SUM.delta.std.toFixed(4)}`, font: { family: "JetBrains Mono, monospace", size: 11, color: FG } }] : [],
    }), cfg);
  }

  // all taps overlaid
  const faint = (t, y, color, fmt) => ({ x: t.t, y, mode: "lines", name: `tap ${t.n}`, showlegend: false, connectgaps: false,
    line: { color, width: 1 }, opacity: t.used ? 0.5 : 0.22, hovertemplate: `tap ${t.n}<br>%{x:.0f} ms: %{y:${fmt}}<extra></extra>` });
  const compX = [-250, 350];
  if (okTaps.length) {
    Plotly.react("c-signal", [
      ...okTaps.map(t => faint(t, t.signal, L, ".0f")),
      ...(COMP && COMP.signal ? [{ x: COMP.t, y: COMP.signal, mode: "lines", name: "median", line: { color: FG, width: 2 }, hovertemplate: "median %{y:.0f}<extra></extra>" }] : []),
    ], baseLayout({
      title: title("Tap signal"), showlegend: false,
      xaxis: axis({ title: atitle("ms from trigger"), range: compX }),
      yaxis: axis({ title: atitle("drop from peak (Hz)"), rangemode: "tozero" }),
      shapes: [hline(S.threshold, BAD, "dash"), vline(0, N)],
      annotations: [{ xref: "paper", x: 0.01, y: S.threshold, xanchor: "left", yanchor: "bottom", showarrow: false, text: `threshold ${Math.round(S.threshold)}`,
        font: { family: "JetBrains Mono, monospace", size: 11, color: BAD } }],
    }), cfg);
  } else noChart("c-signal", "No tap triggered.");
  if (okTaps.length && hasSensorZ) {
    Plotly.react("c-height", [
      ...okTaps.map(t => faint(t, t.sensor_z.map(z => z == null ? null : z - t.descent), D, ".3f")),
      ...(COMP && COMP.relative_z ? [{ x: COMP.t, y: COMP.relative_z, mode: "lines", name: "median", line: { color: FG, width: 2 }, hovertemplate: "median %{y:.3f} mm<extra></extra>" }] : []),
    ], baseLayout({
      title: title("Sensor Z relative to each tap's descent Z"), showlegend: false,
      xaxis: axis({ title: atitle("ms from trigger"), range: compX }),
      yaxis: axis({ title: atitle("mm"), range: [-0.05, 0.25], tickformat: ".2f" }),
      shapes: [hline(0, D), vline(0, N)],
    }), cfg);
  } else noChart("c-height", hasSensorZ ? "No tap triggered." : "No sensor heights: there was no calibration for this drive current.");

  // each tap
  TAPS.forEach(t => {
    const kin = t.toolhead_z.filter(z => z != null);
    const lowest = kin.length ? Math.min(...kin) : 0;
    const top = (t.liftoff != null ? t.liftoff : t.descent != null ? t.descent : lowest) + 0.15;
    const sigs = t.signal.filter(v => v != null);
    const sigTop = Math.max(S.threshold * 1.3, (sigs.length ? Math.max(...sigs) : 0) * 1.1);
    const xEnd = Math.max(t.lift_start != null ? t.lift_start : 0, 0) + 200;
    const shapes = [vline(0, N), { type: "line", xref: "paper", yref: "y2", x0: 0, x1: 1, y0: S.threshold, y1: S.threshold, line: { color: BAD, width: 1, dash: "dash" } }];
    if (t.descent != null) shapes.push(hline(t.descent, D, "dot"));
    if (t.liftoff != null) shapes.push(hline(t.liftoff, L, "dot"));
    if (t.lift_start != null) shapes.push(vline(t.lift_start, N, "dot"));
    Plotly.react(`tp-${t.n}`, [
      { x: t.t, y: t.sensor_z, mode: "lines", name: "sensor Z", line: { color: D, width: 1.5 }, hovertemplate: "sensor %{y:.3f}<extra></extra>" },
      { x: t.t, y: t.toolhead_z, mode: "lines", name: "toolhead Z", line: { color: N, width: 1.5, dash: "dash" }, hovertemplate: "toolhead %{y:.3f}<extra></extra>" },
      { x: t.t, y: t.signal, mode: "lines", name: "tap signal", yaxis: "y2", line: { color: L, width: 1.2 }, hovertemplate: "signal %{y:.0f}<extra></extra>" },
    ], baseLayout({
      showlegend: false, margin: { l: 50, r: 10, t: 8, b: 30 }, hovermode: "x unified",
      xaxis: axis({ range: [-250, xEnd], anchor: "y2", hoverformat: ".0f", ticksuffix: " ms", tickfont: { size: 10, family: "JetBrains Mono, monospace" } }),
      yaxis: axis({ domain: [0.38, 1], range: [lowest - 0.02, top], tickformat: ".2f", tickfont: { size: 10, family: "JetBrains Mono, monospace" } }),
      yaxis2: axis({ domain: [0, 0.3], range: [0, sigTop], nticks: 3, tickfont: { size: 10, family: "JetBrains Mono, monospace" } }),
      shapes,
    }), Object.assign({}, cfg, { displayModeBar: false }));
  });
}

render();
try { matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => requestAnimationFrame(render)); } catch (e) {}
</script>
</body>
</html>
"""
