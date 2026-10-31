# EDDY-ng
#
# Copyright (C) 2025  Vladimir Vukicevic <vladimir@pobox.com>
#
# Collecting sensor samples and turning them into heights
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple, final, TYPE_CHECKING

import numpy as np

from . import ldc1612_ng

if TYPE_CHECKING:
    from .probe_eddy_ng import ProbeEddy


@dataclass
class ProbeEddyProbeResult:
    samples: List[float]
    mean: float = 0.0
    median: float = 0.0
    min_value: float = 0.0
    max_value: float = 0.0
    tstart: float = 0.0
    tend: float = 0.0
    errors: int = 0


    @property
    def value(self):
        return self.median

    @property
    def stddev(self):
        stddev_sum = np.sum([(s - self.value) ** 2.0 for s in self.samples])
        return float((stddev_sum / len(self.samples)) ** 0.5)

    @classmethod
    def make(cls, times: List[float], heights: List[float], errors: int = 0) -> ProbeEddyProbeResult:
        h = np.array(heights)
        return ProbeEddyProbeResult(
            samples=h.tolist(),
            mean=float(np.mean(h)),
            median=float(np.median(h)),
            min_value=float(np.min(h)),
            max_value=float(np.max(h)),
            tstart=float(times[0]),
            tend=float(times[-1]),
            errors=errors,
        )

    def __format__(self, spec):
        if spec == "v":
            return f"{self.value:.3f}"
        return f"{self.median:.3f} (avg={self.mean:.3f}, {self.min_value:.3f} to {self.max_value:.3f}, [{self.stddev:.3f}])"


# Helper to gather samples and convert them to probe positions
@final
class ProbeEddySampler:
    def __init__(
        self,
        eddy: ProbeEddy,
        calculate_heights: bool = True,
    ):
        self.eddy = eddy
        self._sensor = eddy._sensor
        self._printer = self.eddy._printer
        self._reactor = self._printer.get_reactor()
        self._mcu = self._sensor.get_mcu()
        self._stopped = False
        self._started = False
        self._errors = 0
        self._error_counts = [0, 0, 0, 0]
        self._fmap = eddy.map_for_drive_current() if calculate_heights else None

        self.times = []
        self.raw_freqs = []
        self.accum_vals = []
        # error samples are kept, with nan freqs and heights
        self.freqs = []
        self.heights = [] if self._fmap is not None else None

        self.memos = dict()

    @property
    def raw_count(self):
        return len(self.times)

    # this is just a handy way to communicate values between different parts of the system,
    # specifically to record things like trigger times for plotting
    def memo(self, name, value):
        self.memos[name] = value

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.finish()

    def active(self):
        return self._started and not self._stopped

    # bulk sample callback for when new data arrives
    # from the probe
    def _add_hw_data(self, data: ldc1612_ng.LDC1612_ng_sample_data):
        self.times.extend(data.times)
        self.raw_freqs.extend(data.raw_freqs)
        self.accum_vals.extend(data.accum_vals)

        for rf in data.raw_freqs:
            err = rf >> 28
            if err != 0:
                self._errors += 1
                for i in range(4):
                    if err & (1 << i) != 0:
                        self._error_counts[i] += 1

    def start(self):
        if self._stopped:
            raise self._printer.command_error("ProbeEddySampler.start() called after finish()")
        if not self._started:
            self._sensor.start_streaming(self._add_hw_data, send_filter_values=self.eddy.wants_filter_values())
            self._started = True

    def finish(self):
        if self._stopped:
            return
        if not self._started:
            raise self._printer.command_error("ProbeEddySampler.finish() called without start()")
        self._sensor.stop_streaming()
        self._update_samples()
        self.eddy._sampler_finished(self)
        self._stopped = True

    def trim_to_times(self, tstart: float | None = None, tend: float | None = None):
        if not self._stopped:
            raise self._printer.command_error("ProbeEddySampler.trim_to_times while sampler was active")

        istart = bisect.bisect_left(self.times, tstart) if tstart else 0
        iend = bisect.bisect_right(self.times, tend) if tend else len(self.times)

        self.times = self.times[istart:iend]
        self.freqs = self.freqs[istart:iend]
        self.raw_freqs = self.raw_freqs[istart:iend]
        self.accum_vals = self.accum_vals[istart:iend]
        self.heights = self.heights[istart:iend] if self.heights else None

    def _update_samples(self):
        if len(self.freqs) == len(self.raw_freqs):
            return

        conv_add, conv_mul = self._sensor.freqval_conversion_values()

        start_idx = len(self.freqs)
        raw = np.asarray(self.raw_freqs[start_idx:], dtype=np.int64)
        freqs_np = (raw + conv_add) * conv_mul
        freqs_np[(raw >> 28) != 0] = np.nan
        self.freqs.extend(freqs_np.tolist())

        if self._fmap is not None:
            heights_np = self._fmap.freqs_to_heights_np(freqs_np)
            self.heights.extend(heights_np.tolist())

    @property
    def error_count(self):
        return self._errors

    @property
    def error_counts(self):
        return list(self._error_counts)

    # wait for a valid sample from the current time on and get its height,
    # or None if there isn't one within max_wait_time
    def get_height_now(self, max_wait_time=1.000) -> Optional[float]:
        if self.heights is None:
            raise self._printer.command_error("ProbeEddySampler: no height mapping")
        wait_start = sample_time = self.eddy._print_time_now()
        while True:
            remaining = max_wait_time - (self.eddy._print_time_now() - wait_start)
            if remaining <= 0.0 or not self.wait_for_sample_at_time(sample_time, max_wait_time=remaining, raise_error=False):
                return None
            self._update_samples()
            height = self.heights[-1]
            if not math.isnan(height):
                return height
            # an error sample; wait for the next one
            sample_time = math.nextafter(self.times[-1], math.inf)

    # Wait until a sample for the given time arrives
    def wait_for_sample_at_time(self, sample_print_time, max_wait_time=0.250, raise_error=True) -> bool:
        self.eddy._log_debug(f"EDDYng waiting for sample at {sample_print_time:.3f} (now: max_wait_time: {max_wait_time:.3f})")

        def report_no_samples(waited_for):
            if raise_error:
                lsf = f", last sample {self.times[-1]}" if self.times else ", no samples at all"
                raise self._printer.command_error(f"No samples received for time {sample_print_time:.3f} (waited for {waited_for:.3f}{lsf})")
            return False

        if self._stopped:
            # if we're not getting any more samples, we can check directly
            if len(self.times) == 0:
                return report_no_samples(0.0)
            self.eddy._log_debug(f"stopped, last sample time: {self.times[-1]}")
            return self.times[-1] >= sample_print_time

        # quick check
        if self.times and self.times[-1] >= sample_print_time:
            return True

        wait_start_time = self.eddy._print_time_now()

        # if sample_print_time is in the future, make sure to wait max_wait_time
        # past the expected time
        if sample_print_time > wait_start_time:
            self.eddy._log_debug(
                f"{sample_print_time} > {wait_start_time}; max_wait_time = {max_wait_time} + ({sample_print_time} - {wait_start_time} [= {sample_print_time - wait_start_time}])"
            )
            max_wait_time = max_wait_time + (sample_print_time - wait_start_time)

        # this is just a sanity check, there shouldn't be any reason to ever wait this long
        if max_wait_time > 30.0:
            msg = f"ProbeEddyFrequencySampler: max_wait_time {max_wait_time:.3f} is too far into the future"
            raise self._printer.command_error(msg)

        self.eddy._log_debug(
            f"EDDYng waiting for sample at {sample_print_time:.3f} (now: {wait_start_time:.3f}, max_wait_time: {max_wait_time:.3f})"
        )
        now = self.eddy._print_time_now()
        while len(self.times) == 0 or self.times[-1] < sample_print_time:
            # Process any pending data
            self._sensor.process_data()

            now = self.eddy._print_time_now()
            if now - wait_start_time > max_wait_time:
                return report_no_samples(now - wait_start_time)
            self._reactor.pause(self._reactor.monotonic() + 0.010)

        if now - wait_start_time > 1.0:
            self.eddy._log_info(f"note: waited {now - wait_start_time:.3f}s for sample")

        return True


    # The times and heights of the valid (non-error) samples in [start_time, end_time)
    def heights_between(self, start_time: float, end_time: float) -> Tuple[np.ndarray, np.ndarray]:
        if self.heights is None:
            raise self._printer.command_error("ProbeEddySampler: no height mapping")
        self._update_samples()
        istart = bisect.bisect_left(self.times, start_time)
        iend = bisect.bisect_left(self.times, end_time, lo=istart)
        times = np.asarray(self.times[istart:iend])
        heights = np.asarray(self.heights[istart:iend])
        valid = ~np.isnan(heights)
        return times[valid], heights[valid]

    # The median height in each (start, end) interval, nan for intervals without valid samples
    def find_heights_at_times(self, intervals) -> List[float]:
        interval_heights = []
        for iv_start, iv_end in intervals:
            _, heights = self.heights_between(iv_start, iv_end)
            if len(heights) == 0:
                self.eddy._log_msg(f"Warning: no valid samples in time range {iv_start}-{iv_end}")
                interval_heights.append(math.nan)
            else:
                interval_heights.append(float(np.median(heights)))
        return interval_heights

    def find_height_at_time(self, start_time, end_time) -> float:
        height = self.find_heights_at_times([(start_time, end_time)])[0]
        if math.isnan(height):
            raise self._printer.command_error(f"no valid samples between time {start_time:.3f} and {end_time:.3f}!")
        return height

