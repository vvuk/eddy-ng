# EDDY-ng
#
# Copyright (C) 2025  Vladimir Vukicevic <vladimir@pobox.com>
#
# Finding Z=0 without a calibration or a paper test, for AUTO_SETUP
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import List, Optional, final, TYPE_CHECKING

import numpy as np

from .compat import GCodeCommand

if TYPE_CHECKING:
    from .probe_eddy_ng import ProbeEddy

# Bit 3 of a sample's error nibble: amplitude error. Far from the bed this is
# usually the amplitude being too high, which is why homing ignores it too.
SAMPLE_ERR_AMPLITUDE = 1 << 3


@dataclass
class FreqReading:
    # median frequency, and its standard error
    freq: float
    stderr: float


# Without a calibration the sensor can't tell us heights, but the raw
# frequency still goes up as the sensor gets closer to the bed, and stops
# going up when the nozzle touches the bed and the toolhead can't get any
# closer. That's what tap detects. This finds Z=0 in two steps:
#
# 1. Rough contact: step the toolhead down, reading the frequency while it
#    stands still after each step. Far from the bed the steps grow (up to
#    COARSE_STEP) while the frequency barely changes; once the frequency
#    starts rising they're FINE_STEP. Closer to the bed every step raises the
#    frequency more than the one before, so when a step raises it by much
#    less than the biggest rise so far, the nozzle is touching the bed. The
#    nozzle is pushed into the bed by at most about one fine step (and the
#    flex of the printer absorbs some of that).
#
# 2. Taps: with the rough contact as a temporary Z=0, read the frequencies at
#    home_trigger_height and tap_trigger_safe_start_height (the ones a tap
#    gets from the calibration), and tap as usual from tap_start_z. The tap
#    gives the accurate Z=0.
#
# Z is homed afterwards, with Z=0 where the nozzle touches the bed plus
# tap_adjust_z, the same as after PROBE_EDDY_NG_TAP.
@final
class ProbeEddyZeroFinder:
    # The rough contact is placed at this Z while tapping, so that tap moves
    # below it stay within the Z axis limits
    Z_BASE = 5.0

    def __init__(self, eddy: ProbeEddy, gcmd: GCodeCommand, drive_current: int):
        self.eddy = eddy
        self.gcmd = gcmd
        self.drive_current = drive_current
        self._printer = eddy._printer
        self._th = self._printer.lookup_object("toolhead")
        params = eddy.params

        # raise the toolhead by this much before starting, so that it doesn't
        # start out touching the bed
        self.lift: float = gcmd.get_float("LIFT", 5.0, minval=0.0)
        self.fine_step: float = gcmd.get_float("FINE_STEP", 0.2, minval=0.05, maxval=0.5)
        self.coarse_step: float = gcmd.get_float("COARSE_STEP", 2.0, minval=self.fine_step, maxval=5.0)
        # contact: a step raised the frequency by less than this fraction of
        # the largest rise
        self.contact_ratio: float = gcmd.get_float("CONTACT_RATIO", 0.5, above=0.0, below=1.0)
        # how far to go down, at most (defaults to the length of the Z axis)
        self.max_travel: Optional[float] = gcmd.get_float("MAX_TRAVEL", None, above=0.0)
        # how far to go down at most once the frequency is rising
        self.max_fine_travel: float = gcmd.get_float("MAX_FINE_TRAVEL", 15.0, above=0.0)
        # how long to read the sensor for at each step
        self.duration: float = gcmd.get_float("DURATION", 0.100, above=0.0)
        self.speed: float = gcmd.get_float("PROBE_SPEED", params.probe_speed, above=0.0)
        self.lift_speed: float = gcmd.get_float("LIFT_SPEED", params.lift_speed, above=0.0)
        self.adjust_z: float = gcmd.get_float("ADJUST_Z", eddy._tap_adjust_z)

    def _log_debug(self, msg):
        self.eddy._log_debug(f"auto setup: {msg}")

    def _move_z(self, z: float, speed: float):
        self._th.manual_move([None, None, z], speed)

    # Read the frequency with the toolhead standing still. Returns None if
    # every sample had an amplitude error, which far from the bed means it's
    # out of the sensor's range; raises on any other errors.
    def _read(self) -> Optional[FreqReading]:
        eddy = self.eddy
        th = self._th
        # let the toolhead settle after the move
        th.dwell(0.100)
        th.wait_moves()

        with eddy.start_sampler(calculate_heights=False) as sampler:
            start_time = eddy._print_time_now() + eddy._sensor.settle_time
            sampler.wait_for_sample_at_time(start_time + self.duration)

        times = np.asarray(sampler.times)
        in_window = times >= start_time
        freqs = np.asarray(sampler.freqs)[in_window]
        raw = np.asarray(sampler.raw_freqs, dtype=np.int64)[in_window]
        valid = freqs[~np.isnan(freqs)]

        if len(freqs) == 0:
            raise self._printer.command_error("Auto setup: no samples from the sensor")
        if len(valid) >= max(3, len(freqs) // 2):
            return FreqReading(float(np.median(valid)), float(np.std(valid) / math.sqrt(len(valid))))

        err_bits = 0
        for r in raw[(raw >> 28) != 0]:
            err_bits |= int(r) >> 28
        if len(valid) == 0 and err_bits == SAMPLE_ERR_AMPLITUDE:
            return None
        raise self._printer.command_error(
            f"Auto setup: sensor errors {eddy._sensor.data_error_to_str(err_bits << 28)} "
            f"in {len(freqs) - len(valid)} of {len(freqs)} samples. Try a different DRIVE_CURRENT."
        )

    # The smallest rise between two readings that isn't noise
    @staticmethod
    def _rise_noise(a: FreqReading, b: FreqReading) -> float:
        return max(3.0 * math.hypot(a.stderr, b.stderr), 2e-6 * b.freq)

    # Leaves Z homed, with Z=0 where the nozzle touches the bed plus
    # ADJUST_Z (tap_adjust_z). On errors, Z is left homed relative to
    # wherever this started, with the toolhead lifted off the bed.
    def find_zero(self):
        try:
            self._find_zero()
        except Exception:
            if not self._printer.is_shutdown():
                try:
                    # in case it's pushing into the bed
                    th = self._th
                    z_max = th.get_kinematics().rails[2].get_range()[1]
                    self._move_z(min(th.get_position()[2] + 2.0, z_max), self.lift_speed)
                    th.wait_moves()
                except Exception:
                    logging.exception("EDDYng auto setup: lifting the toolhead after an error failed")
            raise

    def _find_zero(self):
        eddy = self.eddy
        th = self._th
        th.wait_moves()

        # We don't know where Z is; give it the top of the axis, so that
        # there's room to go down
        z_min, z_max = th.get_kinematics().rails[2].get_range()
        pos = th.get_position()
        pos[2] = z_max - self.lift - 1.0
        eddy._set_toolhead_position(pos)
        floor_z = max(z_min, pos[2] - self.max_travel) if self.max_travel else z_min

        if self.lift > 0.0:
            self._move_z(pos[2] + self.lift, self.lift_speed)

        rough_zero = self._find_rough_contact(floor_z)

        # Lift off the bed, and make the rough contact Z_BASE
        self._move_z(rough_zero + eddy.params.tap_start_z, self.lift_speed)
        th.wait_moves()
        pos = th.get_position()
        pos[2] += self.Z_BASE - rough_zero
        eddy._set_toolhead_position(pos)

        tap_z = self._tap(rough_zero)

        # Z=0 is the tap, plus tap_adjust_z, like PROBE_EDDY_NG_TAP HOME_Z=1
        adjust_z = self.adjust_z
        th.wait_moves()
        pos = th.get_position()
        pos[2] -= self.Z_BASE + tap_z + adjust_z
        eddy._set_toolhead_position(pos)
        eddy._last_tap_z = float(-adjust_z)

    def _find_rough_contact(self, floor_z: float) -> float:
        eddy = self.eddy
        th = self._th

        z = th.get_position()[2]
        prev = self._read()
        step = self.fine_step
        in_range = False
        fine_start_z = None
        # rises of each fine step, and the Z after the biggest one
        rises: List[float] = []
        max_rise = 0.0
        max_rise_z = None

        while True:
            if fine_start_z is not None and fine_start_z - (z - step) > self.max_fine_travel:
                raise self._printer.command_error(
                    f"Auto setup: no bed contact detected within {self.max_fine_travel:.1f}mm of the sensor seeing the bed. "
                    "Check that the nozzle is over the bed and the sensor is over metal."
                )
            if z - step < floor_z:
                raise self._printer.command_error(
                    "Auto setup: reached the bottom of the Z axis without finding the bed. "
                    "Check that the sensor is over the bed, or try a different DRIVE_CURRENT."
                )

            z -= step
            self._move_z(z, self.speed)
            cur = self._read()

            if cur is None:
                if prev is not None:
                    raise self._printer.command_error(
                        f"Auto setup: the sensor stopped reading while moving down (at Z={z:.3f}). Try a different DRIVE_CURRENT."
                    )
                # still out of range
                self._log_debug(f"z={z:.3f}: out of range")
                step = min(step * 2.0, self.coarse_step)
                continue
            if prev is None:
                self._log_debug(f"z={z:.3f}: f={cur.freq:.1f} (first reading)")
                prev = cur
                step = self.fine_step
                continue

            noise = self._rise_noise(prev, cur)
            rise = cur.freq - prev.freq
            self._log_debug(f"z={z:.3f}: f={cur.freq:.1f} rise={rise:.1f} (step {step:.2f}, noise {noise:.1f})")

            if not in_range:
                # in range once the rise, scaled down to a fine step, isn't noise
                if rise * (self.fine_step / step) > 2.0 * noise:
                    in_range = True
                    fine_start_z = z
                    eddy._log_msg(f"Auto setup: sensor sees the bed, at f={cur.freq:.1f}. Looking for contact...")
                    step = self.fine_step
                else:
                    step = min(step * 2.0, self.coarse_step)
                prev = cur
                continue

            rises.append(rise)
            if rise > max_rise:
                max_rise = rise
                max_rise_z = z

            if self._is_contact(rises, noise):
                # don't push into the bed any further over noise; read again
                # without moving to make sure
                again = self._read()
                if again is None:
                    raise self._printer.command_error(f"Auto setup: the sensor stopped reading at Z={z:.3f}")
                rises[-1] = again.freq - prev.freq
                if self._is_contact(rises, noise):
                    break
                self._log_debug(f"z={z:.3f}: re-read f={again.freq:.1f}, not contact")
                cur = again
                if rises[-1] > max_rise:
                    max_rise = rises[-1]
                    max_rise_z = z

            prev = cur

        # The nozzle touched the bed after the step with the biggest rise:
        # contact is between that Z and one fine step below it
        self._move_z(max_rise_z + 1.0, self.lift_speed)
        eddy._log_msg(
            f"Auto setup: rough bed contact at Z={max_rise_z:.3f} "
            f"(largest rise {max_rise:.1f}, last rises {', '.join(f'{r:.1f}' for r in rises[-3:])})"
        )
        return max_rise_z

    def _is_contact(self, rises: List[float], noise: float) -> bool:
        if len(rises) < 3:
            return False
        prev, rise = rises[:-1], rises[-1]

        # A step that raised the frequency by much less than the biggest rise
        # so far. The biggest rise is taken over pairs of steps, so that a
        # single noisy reading doesn't set it.
        ref = max((a + b) / 2.0 for a, b in zip(prev, prev[1:]))
        if ref > 4.0 * noise and rise < self.contact_ratio * ref:
            return True

        # Before contact every step raises the frequency a bit more than the
        # one before, so a step that clearly falls short of the rise the last
        # few steps predict is contact. This catches contact on printers where
        # the sensor still gets closer to the bed for a while as the nozzle
        # pushes into it.
        if len(prev) >= 3:
            r1, r2, r3 = prev[-3:]
            predicted = r3 + (r3 - r1) / 2.0
            if predicted > 10.0 * noise and rise < predicted - max(0.1 * predicted, 3.0 * noise):
                return True
        return False

    def _tap(self, rough_zero: float) -> float:
        eddy = self.eddy
        params = eddy.params
        base = self.Z_BASE

        # The frequencies a tap must pass through, from above
        gate_freqs = []
        for height in (params.home_trigger_height, params.tap_trigger_safe_start_height):
            self._move_z(base + height, self.speed)
            reading = self._read()
            if reading is None:
                raise self._printer.command_error(f"Auto setup: no sensor reading at {height:.3f}mm")
            gate_freqs.append(reading)
        safe, trigger = gate_freqs
        if trigger.freq - safe.freq <= self._rise_noise(safe, trigger):
            raise self._printer.command_error(
                f"Auto setup: the frequency barely changes between {params.home_trigger_height:.1f}mm and "
                f"{params.tap_trigger_safe_start_height:.1f}mm ({safe.freq:.1f} to {trigger.freq:.1f}). "
                "The sensor may be mounted too high."
            )
        self._log_debug(
            f"gate freqs: {safe.freq:.1f} at {params.home_trigger_height:.3f}, {trigger.freq:.1f} at {params.tap_trigger_safe_start_height:.3f}"
        )

        # The real contact can be up to a fine step below the rough one
        tap_z, tap_stddev, _ = eddy.run_taps(
            self.gcmd,
            self.drive_current,
            drive_current=self.drive_current,
            target_z=params.tap_target_z - self.fine_step,
            z_base=base,
            gate_freqs=(safe.freq, trigger.freq),
        )
        tap_z -= base

        if not (-self.fine_step - 0.200 <= tap_z <= 1.0):
            raise self._printer.command_error(
                f"Auto setup: the tap (at {tap_z:.3f}) is too far from the contact found by stepping down. "
                "Make sure the nozzle is clean, and try again."
            )
        eddy._log_msg(f"Auto setup: tap at {tap_z:.3f} from the rough contact (stddev {tap_stddev:.3f})")
        return tap_z
