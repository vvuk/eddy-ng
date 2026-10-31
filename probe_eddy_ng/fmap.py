# EDDY-ng
#
# Copyright (C) 2025  Vladimir Vukicevic <vladimir@pobox.com>
#
# The calibrated mapping between sensor frequency and height
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

import base64
import json
import math
import pickle
from typing import List, Optional, Tuple, final, TYPE_CHECKING

import numpy as np
import numpy.polynomial as npp

from .compat import ConfigWrapper, configerror
from . import plots

if TYPE_CHECKING:
    from .probe_eddy_ng import ProbeEddy



@final
class ProbeEddyFrequencyMap:
    # Version 6 stores the calibration as JSON; version 5 (a pickle of numpy
    # objects) can still be loaded, and is rewritten as 6 on the next save.
    calibration_version = 6
    loadable_calibration_versions = (5, 6)
    low_z_threshold = 5.0

    def __init__(self, eddy: ProbeEddy):
        self._eddy = eddy
        self._sensor = eddy._sensor

        self.drive_current = 0
        self.height_range = (math.inf, -math.inf)
        self.freq_range = (math.inf, -math.inf)
        self._ftoh: Optional[npp.Polynomial] = None
        self._ftoh_high: Optional[npp.Polynomial] = None
        self._ftoh_rms = None

    def __format__(self, _spec):
        fit = f"fit {self._ftoh_rms:.3f}" if self._ftoh_rms is not None else "fit unknown"
        return (
            f"DC {self.drive_current}: valid range {self.height_range[0]:.3f} to {self.height_range[1]:.3f}, "
            + f"spread {self.freq_spread():.2f}% (freq {self.freq_range[0]:.1f} to {self.freq_range[1]:.1f}), {fit}"
        )


    def freq_spread(self) -> float:
        return ((self.freq_range[1] / self.freq_range[0]) - 1.0) * 100.0

    @staticmethod
    def _poly_to_json(p: Optional[npp.Polynomial]):
        if p is None:
            return None
        return {"coef": p.coef.tolist(), "domain": p.domain.tolist(), "window": p.window.tolist()}

    @staticmethod
    def _poly_from_json(d) -> Optional[npp.Polynomial]:
        if d is None:
            return None
        return npp.Polynomial(d["coef"], domain=d["domain"], window=d["window"])

    def load_from_config(self, config: ConfigWrapper, drive_current: int, version: int):
        calibstr = config.get(f"calibration_{drive_current}", None)
        if calibstr is None:
            return False

        if version == 5:
            data = pickle.loads(base64.b64decode(calibstr))
            if data.get("v", None) != 5:
                self._eddy._log_info(f"Calibration for dc {drive_current} is old ({data.get('v')}), needs recalibration")
                return False
            ftoh, ftoh_high = data.get("ftoh"), data.get("ftoh_high")
        else:
            data = json.loads(calibstr)
            ftoh, ftoh_high = self._poly_from_json(data["ftoh"]), self._poly_from_json(data.get("ftoh_high"))

        dc = data.get("dc", None)
        if dc != drive_current:
            raise configerror(f"ProbeEddyFrequencyMap: drive current mismatch: loaded {dc} != requested {drive_current}")

        self._ftoh = ftoh
        self._ftoh_high = ftoh_high
        self._ftoh_rms = data.get("rms", None)
        self.height_range = tuple(data["h_range"])
        self.freq_range = tuple(data["f_range"])
        self.drive_current = drive_current

        self._eddy._log_info(f"Loaded calibration for drive current {drive_current}")
        return True

    def save_calibration(self):
        if self._ftoh is None:
            return

        configfile = self._eddy._printer.lookup_object("configfile")
        # json writes floats with repr(), which round-trips exactly
        data = {
            "dc": self.drive_current,
            "ftoh": self._poly_to_json(self._ftoh),
            "ftoh_high": self._poly_to_json(self._ftoh_high),
            "h_range": [float(v) for v in self.height_range],
            "f_range": [float(v) for v in self.freq_range],
            "rms": None if self._ftoh_rms is None else float(self._ftoh_rms),
        }
        configfile.set(self._eddy._full_name, f"calibration_{self.drive_current}", json.dumps(data, separators=(",", ":")))

    def calibrate_from_values(
        self,
        drive_current: int,
        raw_times: List[float],
        raw_freqs_list: List[float],
        raw_heights_list: List[float],
        raw_vels_list: List[float],
        report_errors: bool,
        write_debug_files: bool,
    ) -> bool:
        if len(raw_freqs_list) != len(raw_heights_list):
            raise ValueError("freqs and heights must be the same length")

        if len(raw_freqs_list) == 0:
            if report_errors:
                self._eddy._log_error(f"Drive current {drive_current}: Calibration failed, no valid samples received.")
            return False

        # everything must be a np.array or things get confused below
        times = np.asarray(raw_times)
        freqs = np.asarray(raw_freqs_list)
        heights = np.asarray(raw_heights_list)
        vels = np.asarray(raw_vels_list) if raw_vels_list else None

        if write_debug_files:
            with open("/tmp/eddy-calibration.csv", "w") as data_file:
                data_file.write("time,frequency,z,v\n")
                for i in range(len(freqs)):
                    s_t = times[i]
                    s_f = freqs[i]
                    s_z = heights[i]
                    s_v = vels[i] if vels is not None else 0.0
                    data_file.write(f"{s_t},{s_f},{s_z},{s_v}\n")
                self._eddy._log_info(f"Wrote {len(freqs)} samples to /tmp/eddy-calibration.csv")

        max_height = float(heights.max())
        min_height = float(heights.min())
        min_freq = float(freqs.min())
        max_freq = float(freqs.max())
        freq_spread = ((max_freq / min_freq) - 1.0) * 100.0

        # Check if our calibration is good enough
        if report_errors:
            if max_height < 2.5:  # we really can't do anything with this
                self._eddy._log_error(
                    f"Drive current {drive_current} error: max height for valid samples is too low: {max_height:.3f} < 2.5. Possible causes: bad drive current, bad sensor mount height."
                )
                if not self._eddy.params.allow_unsafe:
                    return False

            if min_height > 0.65:  # this is a bit arbitrary; but if it's this far off we shouldn't trust it
                self._eddy._log_error(
                    f"Drive current {drive_current} error: min height for valid samples is too high: {min_height:.3f} > 0.65. Possible causes: bad drive current, bad sensor mount height."
                )
                if not self._eddy.params.allow_unsafe:
                    return False

            if min_height > 0.025:
                self._eddy._log_msg(
                    f"Drive current {drive_current} warning: min height is {min_height:.3f} (> 0.025) is too high for tap. This calibration will work fine for homing, but may not for tap."
                )

            # somewhat arbitrary spread
            if freq_spread < 0.30:
                extremely = "EXTREMELY " if freq_spread < 0.15 else ""
                self._eddy._log_warning(
                    f"Drive current {drive_current} warning: frequency spread is {extremely}low ({freq_spread:.2f}%, {min_freq:.1f}-{max_freq:.1f}), which will greatly impact accuracy. Your sensor may be too high."
                )

        low_samples = heights <= ProbeEddyFrequencyMap.low_z_threshold
        high_samples = heights >= ProbeEddyFrequencyMap.low_z_threshold - 0.5

        ftoh_low_fn = npp.Polynomial.fit(1.0 / freqs[low_samples], heights[low_samples], deg=9)

        if np.count_nonzero(high_samples) > 50:
            ftoh_high_fn = npp.Polynomial.fit(1.0 / freqs[high_samples], heights[high_samples], deg=9)
        else:
            self._eddy._log_debug("not computing ftoh_high, not enough high samples")
            ftoh_high_fn = None

        # Calculate rms, only for the low values (where error is most relevant)
        rmse_fth = np_rmse(
            ftoh_low_fn,
            1.0 / freqs[low_samples],
            heights[low_samples],
        )

        if report_errors:
            if rmse_fth > 0.050:
                self._eddy._log_error(
                    f"Drive current {drive_current} error: calibration error margin is too high ({rmse_fth:.3f}). Possible causes: bad drive current, bad sensor mount height."
                )
                if not self._eddy.params.allow_unsafe:
                    return False

        self._ftoh = ftoh_low_fn
        self._ftoh_high = ftoh_high_fn
        self._ftoh_rms = rmse_fth
        self.drive_current = drive_current
        self.height_range = [min_height, max_height]
        self.freq_range = [min_freq, max_freq]

        self._eddy._log_msg(f"{self}")

        if write_debug_files:
            plots.write_calibration_plot(
                self,
                times,
                freqs,
                heights,
                rmse_fth,
                vels=vels,
            )

        return True

    def freq_to_height(self, freq: float) -> float:
        return float(self.freqs_to_heights_np(np.asarray([freq]))[0])

    def freqs_to_heights_np(self, freqs: np.ndarray) -> np.ndarray:
        if self._ftoh is None:
            raise self._eddy._printer.command_error("Calling freqs_to_heights on uncalibrated map")
        invfreqs = 1.0 / np.asarray(freqs, dtype=float)
        heights = self._ftoh(invfreqs)
        if self._ftoh_high is not None:
            # higher heights mean lower frequencies, i.e. larger inverse frequencies
            high = invfreqs > self._ftoh.domain[1]
            heights[high] = self._ftoh_high(invfreqs[high])
        return heights

    # The freq -> height fit is monotonic over the calibrated range, so invert
    # it by evaluating it on a dense grid of inverse frequencies and
    # interpolating. Returns the grid as (heights, inverse frequencies).
    def _inverse_grid(self) -> Tuple[np.ndarray, np.ndarray]:
        if self._ftoh is None:
            raise self._eddy._printer.command_error("Calling height_to_freq on uncalibrated map")
        top = self._ftoh_high if self._ftoh_high is not None else self._ftoh
        invfreqs = np.linspace(self._ftoh.domain[0], top.domain[1], 4000)
        # guard against any small wiggles in the polynomial fits
        heights = np.maximum.accumulate(self.freqs_to_heights_np(1.0 / invfreqs))
        return heights, invfreqs

    # clamps to the calibrated range
    def _heights_to_invfreqs(self, heights: np.ndarray) -> np.ndarray:
        grid_heights, grid_invfreqs = self._inverse_grid()
        return np.interp(heights, grid_heights, grid_invfreqs)

    def height_to_freq(self, height: float) -> float:
        grid_heights, grid_invfreqs = self._inverse_grid()
        if not grid_heights[0] <= height <= grid_heights[-1]:
            raise self._eddy._printer.command_error(
                f"Height {height:.3f} is outside the calibrated range ({grid_heights[0]:.3f} to {grid_heights[-1]:.3f}) for drive current {self.drive_current}"
            )
        return 1.0 / float(np.interp(height, grid_heights, grid_invfreqs))

    def calibrated(self) -> bool:
        return self._ftoh is not None


def np_rmse(p, x, y):
    y_hat = p(x)
    return np.sqrt(np.mean((y - y_hat) ** 2))

