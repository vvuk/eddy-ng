# EDDY-ng
#
# Copyright (C) 2025  Vladimir Vukicevic <vladimir@pobox.com>
#
# Finding where the nozzle leaves the bed as the toolhead lifts after a tap
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

# How long after the lift starts to look at samples, and how far above the
# lowest point. The nozzle leaves the bed within a few tenths of a mm; the
# samples above that pin down the free-air curve.
LIFTOFF_WINDOW_TIME = 0.300
LIFTOFF_WINDOW_HEIGHT = 1.0
# The lift must cover at least this much of the window for a fit
LIFTOFF_MIN_LIFT = 0.350
# Where the lift-off point is searched for, above the lowest point
LIFTOFF_MAX_DEPRESS = 0.300
# While the nozzle is pressed into the bed, the frequency barely changes as
# the toolhead lifts; after it leaves, it changes like it does in free air.
# The pressed slope must be at most this fraction of the free-air slope.
LIFTOFF_MAX_SLOPE_RATIO = 0.5


# The result of fitting the lift after a tap.
#
# While the toolhead lifts with the nozzle still pressed into the bed, the
# frame unflexes but the sensor stays at the same distance from the bed, so
# the frequency stays nearly constant. Once the nozzle leaves the bed, the
# sensor rises with the toolhead and the frequency follows the free-air
# curve. The toolhead Z at the kink between the two is where the contact
# force reached zero: the bed, in toolhead coordinates, independent of how
# far the tap overshot or how late it was detected.
#
# This fits, as a function of toolhead z, with zc the kink:
#   freq = f0 + contact_slope * (z - zc)                         for z <= zc
#   freq = f0 + free_slope * (z - zc) + free_curve * (z - zc)^2  for z >= zc
# (the same model as Klipper's probe_eddy_current tap).
@dataclass
class LiftoffFit:
    error: Optional[str] = None
    # toolhead z where the nozzle left the bed
    z: float = 0.0
    # how far above the lowest toolhead z that was
    depress: float = 0.0
    freq: float = 0.0
    contact_slope: float = 0.0
    free_slope: float = 0.0
    free_curve: float = 0.0
    # RMS fit error, in Hz
    rms: float = 0.0
    samples: int = 0
    # samples taken while the toolhead was lifting with the nozzle still
    # on the bed (not counting any from before the lift started)
    contact_samples: int = 0

    @property
    def slope_ratio(self) -> float:
        return abs(self.contact_slope / self.free_slope) if self.free_slope else float("inf")

    def __str__(self):
        if self.error:
            return f"lift-off fit failed: {self.error}"
        return (
            f"lift-off z={self.z:.3f} (depress {self.depress:.3f}, {self.contact_samples}/{self.samples} samples in contact, "
            f"slope ratio {self.slope_ratio:.2f}, rms {self.rms:.1f}Hz)"
        )


def _fit_at(z: np.ndarray, f: np.ndarray, zc: float):
    d = z - zc
    below = np.minimum(d, 0.0)
    above = np.maximum(d, 0.0)
    a = np.column_stack([np.ones_like(d), below, above, above * above])
    coeffs, _, _, _ = np.linalg.lstsq(a, f, rcond=None)
    resid = f - a @ coeffs
    return float(resid @ resid), coeffs


# Fit the lift-off model to samples taken while the toolhead lifted (and
# just before), given each sample's toolhead z and frequency. Error samples
# (nan frequencies) are ignored.
def fit_liftoff(zs: Sequence[float], freqs: Sequence[float]) -> LiftoffFit:
    z = np.asarray(zs, dtype=float)
    f = np.asarray(freqs, dtype=float)
    valid = np.isfinite(z) & np.isfinite(f)
    z, f = z[valid], f[valid]

    if len(z) < 10:
        return LiftoffFit(error=f"not enough samples ({len(z)})", samples=len(z))

    z_min = float(z.min())
    lift = float(z.max()) - z_min
    if lift < LIFTOFF_MIN_LIFT:
        return LiftoffFit(error=f"insufficient lift ({lift:.3f} < {LIFTOFF_MIN_LIFT:.3f})", samples=len(z))

    # work around the mean for numerical sanity; freqs are ~MHz
    f_base = float(np.mean(f))
    f = f - f_base

    # coarse search for the kink, then refine around the best
    hi = z_min + min(LIFTOFF_MAX_DEPRESS, lift - 0.1)
    candidates = np.arange(z_min, hi, 0.002)
    best = min(candidates, key=lambda c: _fit_at(z, f, c)[0])
    fine = np.arange(best - 0.002, best + 0.002, 0.0001)
    fine = fine[(fine >= z_min) & (fine <= hi)]
    zc = float(min(fine, key=lambda c: _fit_at(z, f, c)[0])) if len(fine) else float(best)
    sse, (f0, contact_slope, free_slope, free_curve) = _fit_at(z, f, zc)

    fit = LiftoffFit(
        z=zc,
        depress=zc - z_min,
        freq=float(f0) + f_base,
        contact_slope=float(contact_slope),
        free_slope=float(free_slope),
        free_curve=float(free_curve),
        rms=float(np.sqrt(sse / len(z))),
        samples=len(z),
        contact_samples=int(np.count_nonzero((z > z_min + 1e-4) & (z <= zc))),
    )

    if fit.free_slope == 0.0 or fit.slope_ratio > LIFTOFF_MAX_SLOPE_RATIO:
        fit.error = f"no contact found (slope ratio {fit.slope_ratio:.2f} > {LIFTOFF_MAX_SLOPE_RATIO:.2f})"
    elif fit.contact_samples < 2:
        fit.error = f"too few samples in contact ({fit.contact_samples})"
    elif zc >= hi - 0.005:
        fit.error = f"lift-off point at the edge of the search range ({fit.depress:.3f} above the lowest point)"
    return fit
