# EDDY-ng
#
# Copyright (C) 2025  Vladimir Vukicevic <vladimir@pobox.com>
#
# Based on original probe_eddy_current code by:
# Copyright (C) 2020-2024  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, final

import numpy as np

if __package__ in (None, "", "extras"):
    # Loaded as a single file from klippy/extras: an install from before
    # eddy-ng became a package
    raise ImportError("eddy-ng's install layout has changed; please re-run eddy-ng's install.sh")

from .compat import (
    ConfigWrapper,
    GCodeCommand,
    ToolHead,
    probe,
    manual_probe,
    HomingMove,
    set_position_z_homed,
    clear_z_homed,
)
from .sampler import ProbeEddyProbeResult
from .fmap import ProbeEddyFrequencyMap
from .tap import TapResult, TapConfig, compute_tap_z, tap_filter_sos
from .endstop import ProbeEddyEndstopWrapper
from .scan import ProbeEddyScanningProbe, BedMeshScanHelper
from . import plots
from .base import EddyNgSensor


# In this file, a couple of conventions are used (for sanity).
# Variables are named according to:
# - "height" is always a physical height as detected by the probe in mm
# - "z" is always a z axis position (which may or may not match height)
# - "freq" is always a frequency value (float)
# - "freqval" is always an encoded frequency value, as communicated to/from the sensor (int)

# There are three distinct operations/phases. Homing Z via the virtual
# endstop is the only operation that can happen while Z is not homed:
#
# 1. Homing Z using a virtual probe endstop. This is largely handled by
#    ProbeEddyEndstopWrapper. It sets up the sensor to trigger when a certain
#    frequency is crossed, and then lets a HomingMove continue that moves the
#    toolhead down. When that frequency is hit, it triggers, and Klipper stops
#    the toolhead from moving down. The time point when it triggers is set as
#    the z=trigger_height (which is home_trigger_height in the configurable
#    params). Z should be accurate enough at this point. This operation can be run
#    when the bed/toolhead are cold or hot.
#
# Once Z is homed, two additional operations become available:
#
# 2. Probing at either a single point or multiple points. This is used for
#    Quad Gantry Leveling, Bed Mesh, and other similar operations. This is
#    largely handled by the ProbeEddyScannigProbe class -- one is returned
#    from the ProbeEddy `probe` object when `start_probe_session` is called.
#    For Eddy probes, there is no reason to move the toolhead up and down at
#    each probe point: the measured distance between the sensor and the build
#    plate can be read directly. This class starts gathering sample data when
#    the session starts and records the times when there's a sample that we
#    care about, along with the toolhead position, whenever a caller calls
#    `run_probe`. If this is a `rapid_scan` scan, then a callback is attached
#    to the current motion so that we can save the movement's time and position
#    without actually waiting for it. If it's in normal mode, then the toolhead
#    will pause at each position. In both cases, the results are obtained by
#    calling `pull_probed_results`, which returns an array of results at each
#    point that `run_probe` was called for, in order.
#
#    PROBE_STATIC HOME_Z=1 can be used to set the toolhead's Z position
#    based on the current height reading from the probe while the toolhead is
#    static, leading to a more accurate result than a regular homing operation
#    (which involves movement).
#
# 3. A "tap" to fine-tune the Z offset. This should be run with the bed at print
#    temperature and soaked for a bit. The nozzle should also be warm but not so
#    hot that filament risks oozing out. The nozzle also must be clean. 150C
#    is a good temperature to both clean and tap at.
#
#    This operation will identify the exact position of the Z axis
#    when the nozzle touches the bed, which means that a precise Z offset
#    can be set.
#
# The eddy current response and readings depend on temperature of both the target
# (bed) and the sensor (coil). EddyNG does not do any temperature compensation. Instead
# it relies on the "tap" operation to get an accurate reference point for z=0 regardless
# of temperatures. Empirically, small offsets from a reference point can still be read
# accurately from the sensor, even if the absolute value is incorrect at temperature.
# For example, taking sensor readings at Z=2 when perfectly homed via tap may read as
# 1.9 due to temperatures, which is not correct. However, raising the toolhead to Z=2.1
# will raise the sensor reading to 2.0; likewise, lowering the toolhead to Z=1.9 will
# lower the sensor reading to 1.8.
#
# Care in macros should be taken to not invalidate the Z offset set after a tap
# by relying on absolute sensor readings.
#


@final
class ProbeEddy(EddyNgSensor):
    def __init__(self, config: ConfigWrapper):
        logging.info("Hello from ProbeEddyNG")
        super().__init__(config)

        self._dc_to_fmap: Dict[int, ProbeEddyFrequencyMap] = {}

        # The last tap Z value, in absolute axis terms. Used for status.
        self._last_tap_z = 0.0
        # The last gcode offset applied after tap, either the tap
        # value, or 0.0 if HOME_Z=1
        self._last_tap_gcode_adjustment = 0.0

        # figure out if either of these comes from the autosave section
        # so we can sort out what we want to write out later on
        asfc = self._printer.lookup_object("configfile").autosave.fileconfig
        self._saved_reg_drive_current = asfc.getint(self._full_name, "reg_drive_current", fallback=None)
        self._saved_tap_drive_current = asfc.getint(self._full_name, "tap_drive_current", fallback=None)

        # drive current saved by older versions
        old_saved_tap_drive_current = config.getint("saved_tap_drive_current", 0, minval=0, maxval=31)

        self._reg_drive_current = self._sensor.get_drive_current()
        self._tap_drive_current = self.params.tap_drive_current or old_saved_tap_drive_current or self._reg_drive_current

        # at what minimum physical height to start homing. It must be above the safe start position,
        # because we need to move from the start through the safe start position
        self._home_start_height = self.params.home_trigger_height + self.params.home_trigger_safe_start_offset + 1.0

        # physical offsets between probe and nozzle
        self.offset = {
            "x": self.params.x_offset,
            "y": self.params.y_offset,
        }

        version = config.getint("calibration_version", default=-1)
        calibration_bad = False
        if version == -1:
            if config.get("calibrated_drive_currents", None) is not None:
                calibration_bad = True
        elif version not in ProbeEddyFrequencyMap.loadable_calibration_versions:
            calibration_bad = True

        calibrated_drive_currents = config.getintlist("calibrated_drive_currents", [])

        if not calibration_bad:
            for dc in calibrated_drive_currents:
                fmap = ProbeEddyFrequencyMap(self)
                if fmap.load_from_config(config, dc, version):
                    self._dc_to_fmap[dc] = fmap
        else:
            for dc in calibrated_drive_currents:
                # read so that there are no warnings about unknown fields
                _ = config.get(f"calibration_{dc}")
            self.params._warning_msgs.append("EDDYng calibration: calibration data invalid, please recalibrate")

        # Our virtual endstop wrapper -- used for homing.
        self._endstop_wrapper = ProbeEddyEndstopWrapper(self)

        # This class emulates "PrinterProbe". We use some existing helpers to implement
        # functionality like start_session
        self._printer.add_object("probe", self)

        self._bed_mesh_helper = BedMeshScanHelper(self, config) if config.has_section("bed_mesh") else None

        if hasattr(probe, "ProbeCommandHelper"):
            self._cmd_helper = probe.ProbeCommandHelper(config, self, self._endstop_wrapper.query_endstop)
        else:
            self._cmd_helper = None

        # when doing a scan, what's the offset between probe readings at the bed
        # scan height and the accurate bed height, based on the last tap.
        self._tap_offset = 0.0
        self._last_probe_result = 0.0

        # runtime configurable
        self._tap_adjust_z = self.params.tap_adjust_z

        self._tap_sos = tap_filter_sos(self.params, self._sensor.data_rate, self._printer.config_error)

        self._register_commands(
            [
                ["PROBE_STATIC", "PEPS"],
                "SETUP",
                "CALIBRATE",
                "CALIBRATION_STATUS",
                "CLEAR_CALIBRATION",
                "PROBE_ACCURACY",
                "SET_TAP_OFFSET",
                "SET_TAP_ADJUST_Z",
                "TEST_DRIVE_CURRENT",
                ["PROBE", "PEP"],
                ["TAP", "PETAP"],
                "BED_MESH_EXPERIMENTAL",
            ]
        )

        # Replace Klipper's Z_OFFSET_APPLY_PROBE, which would apply the offset
        # to a z_offset we don't use
        if self.params.primary:
            self._gcode.register_command("Z_OFFSET_APPLY_PROBE", None)
        for name in [self._name] + ([None] if self.params.primary else []):
            self._gcode.register_mux_command(
                "Z_OFFSET_APPLY_PROBE",
                "SENSOR",
                name,
                self.cmd_Z_OFFSET_APPLY_PROBE,
                "Apply the current G-Code Z offset to tap_adjust_z",
            )

    def reset_drive_current(self):
        if self._reg_drive_current == 0:
            raise self._printer.command_error("Unknown homing drive current")
        self._sensor.set_drive_current(self._reg_drive_current)

    def map_for_drive_current(self, dc: Optional[int] = None) -> ProbeEddyFrequencyMap:
        if dc is None:
            dc = self.current_drive_current()
        if dc not in self._dc_to_fmap:
            raise self._printer.command_error(f"Drive current {dc} not calibrated")
        return self._dc_to_fmap[dc]

    # helpers to forward to the map
    def height_to_freq(self, height: float, drive_current: Optional[int] = None) -> float:
        if drive_current is None:
            drive_current = self.current_drive_current()
        return self.map_for_drive_current(drive_current).height_to_freq(height)

    def freq_to_height(self, freq: float, drive_current: Optional[int] = None) -> float:
        if drive_current is None:
            drive_current = self.current_drive_current()
        return self.map_for_drive_current(drive_current).freq_to_height(freq)

    def calibrated(self, drive_current: Optional[int] = None) -> bool:
        if drive_current is None:
            drive_current = self.current_drive_current()
        return drive_current in self._dc_to_fmap and self._dc_to_fmap[drive_current].calibrated()

    def _z_homed(self):
        curtime = self._reactor.monotonic()
        kin_status = self._printer.lookup_object("toolhead").get_kinematics().get_status(curtime)
        return "z" in kin_status["homed_axes"]

    def _xy_homed(self):
        curtime = self._reactor.monotonic()
        kin_status = self._printer.lookup_object("toolhead").get_kinematics().get_status(curtime)
        return "x" in kin_status["homed_axes"] and "y" in kin_status["homed_axes"]

    def _z_hop(self, by=5.0):
        if by < 0.0:
            raise self._printer.command_error("Z hop must be positive")
        toolhead: ToolHead = self._printer.lookup_object("toolhead")
        curpos = toolhead.get_position()
        curpos[2] = curpos[2] + by
        toolhead.manual_move(curpos, self.params.probe_speed)

    def _set_toolhead_position(self, pos):
        set_position_z_homed(self._toolhead, pos)

    def _z_not_homed(self):
        clear_z_homed(self._toolhead, self._printer.command_error)

    def save_config(self):
        configfile = self._printer.lookup_object("configfile")
        configfile.remove_section(self._full_name)

        configfile.set(
            self._full_name,
            "calibrated_drive_currents",
            str.join(", ", [str(dc) for dc in self._dc_to_fmap.keys()]),
        )
        configfile.set(
            self._full_name,
            "calibration_version",
            str(ProbeEddyFrequencyMap.calibration_version),
        )

        if self.params.reg_drive_current != self._reg_drive_current or self.params.reg_drive_current == self._saved_reg_drive_current:
            configfile.set(self._full_name, "reg_drive_current", str(self._reg_drive_current))

        if self.params.tap_drive_current != self._tap_drive_current or self.params.tap_drive_current == self._saved_tap_drive_current:
            configfile.set(self._full_name, "tap_drive_current", str(self._tap_drive_current))

        for _, fmap in self._dc_to_fmap.items():
            fmap.save_calibration()

        self._log_msg("Calibration saved. Issue a SAVE_CONFIG to write the values to your config file and restart Klipper.")

    def cmd_BED_MESH(self, gcmd: GCodeCommand):
        if self._bed_mesh_helper is None:
            raise self._printer.command_error("No [bed_mesh] section configured")
        self._bed_mesh_helper.scan()


    cmd_PROBE_ACCURACY_help = "Probe accuracy"

    def cmd_PROBE_ACCURACY(self, gcmd: GCodeCommand):
        if not self._z_homed():
            raise self._printer.command_error("Must home Z before PROBE_ACCURACY")

        # How long to read at each sample time
        duration: float = gcmd.get_float("DURATION", 0.100, above=0.0)
        # whether to check +/- 1mm positions for accuracy
        start_z: float = gcmd.get_float("Z", 5.0)
        offsets: str = gcmd.get("OFFSETS", None)

        probe_speed = gcmd.get_float("SPEED", self.params.probe_speed, above=0.0)
        lift_speed = gcmd.get_float("LIFT_SPEED", self.params.lift_speed, above=0.0)

        probe_zs = [start_z]

        if offsets is not None:
            probe_zs.extend([float(v) + start_z for v in offsets.split(",")])
        else:
            probe_zs.extend(np.arange(0.5, start_z, 0.5).tolist())

        probe_zs.sort()
        probe_zs.reverse()

        # drive current to use
        old_drive_current = self.current_drive_current()
        drive_current: int = gcmd.get_int("DRIVE_CURRENT", old_drive_current, minval=0, maxval=31)

        if not self.calibrated(drive_current):
            raise self._printer.command_error(f"Drive current {drive_current} not calibrated")

        th = self._toolhead
        try:
            self._sensor.set_drive_current(drive_current)

            th.manual_move(
                [None, None, probe_zs[0] + 1.0],
                lift_speed,
            )
            th.wait_moves()

            results = []
            ranges = []
            from_zs = []
            stddev_sums = []
            stddev_count = 0

            for pz in probe_zs:
                th.manual_move([None, None, pz], probe_speed)
                th.dwell(0.050)
                th.wait_moves()

                result = self.probe_static_height(duration=duration)
                rangev = result.max_value - result.min_value
                from_z = result.value - pz
                stddev_sum = np.sum([(s - result.value) ** 2.0 for s in result.samples])

                self._log_msg(f"Probe at z={pz:.3f} is {result}")

                stddev_sums.append(stddev_sum)
                stddev_count += len(result.samples)
                results.append(result)
                ranges.append(rangev)
                from_zs.append(from_z)

            if len(results) > 1:
                avg_range = np.mean(ranges)
                avg_from_z = np.mean(from_zs)
                stddev = (np.sum(stddev_sums) / stddev_count) ** 0.5
                gcmd.respond_info(f"Probe spread: {avg_range:.3f}, z deviation: {avg_from_z:.3f}, stddev: {stddev:.3f}")

        finally:
            self._sensor.set_drive_current(old_drive_current)
            th.manual_move(
                [None, None, start_z],
                lift_speed,
            )

    cmd_CLEAR_CALIBRATION_help = "Clear calibration for all drive currents"

    def cmd_CLEAR_CALIBRATION(self, gcmd: GCodeCommand):
        drive_current: int = gcmd.get_int("DRIVE_CURRENT", -1)
        if drive_current == -1:
            self._dc_to_fmap = {}
            gcmd.respond_info("Cleared calibration for all drive currents")
        else:
            if drive_current not in self._dc_to_fmap:
                raise self._printer.command_error(f"Drive current {drive_current} not calibrated")
            del self._dc_to_fmap[drive_current]
            gcmd.respond_info(f"Cleared calibration for drive current {drive_current}")
        self.save_config()

    cmd_CALIBRATION_STATUS_help = "Display information about EDDYng calibration"

    def cmd_CALIBRATION_STATUS(self, gcmd: GCodeCommand):
        for dc in self._dc_to_fmap:
            m = self._dc_to_fmap[dc]
            hmin, hmax = m.height_range
            fmin, fmax = m.freq_range
            fspread = m.freq_spread()
            self._log_msg(
                f"Drive current {dc}: {hmin:.3f} to {hmax:.3f} ({fmin:.1f} to {fmax:.1f}, {fspread:.2f}%; ftoh_high: {m._ftoh_high is not None})"
            )

    cmd_SET_TAP_OFFSET_help = "Set or clear the tap offset for the bed mesh scan and other probe operations"

    # A new value from VALUE= (replace) and/or ADJUST= (add)
    @staticmethod
    def _value_or_adjust(gcmd: GCodeCommand, current: float) -> float:
        value = gcmd.get_float("VALUE", current)
        return value + gcmd.get_float("ADJUST", 0.0)

    def cmd_SET_TAP_OFFSET(self, gcmd: GCodeCommand):
        tap_offset = self._tap_offset = self._value_or_adjust(gcmd, self._tap_offset)
        gcmd.respond_info(f"Set tap offset: {tap_offset:.3f}")

    cmd_SET_TAP_ADJUST_Z_help = "Set the tap adjustment value"

    def cmd_SET_TAP_ADJUST_Z(self, gcmd: GCodeCommand):
        tap_adjust_z = self._tap_adjust_z = self._value_or_adjust(gcmd, self._tap_adjust_z)

        if self.params.tap_adjust_z != self._tap_adjust_z:
            configfile = self._printer.lookup_object("configfile")
            configfile.set(self._full_name, "tap_adjust_z", str(float(self._tap_adjust_z)))

        gcmd.respond_info(f"Set tap_adjust_z: {tap_adjust_z:.3f} (SAVE_CONFIG to make it permanent)")

    def cmd_Z_OFFSET_APPLY_PROBE(self, gcmd: GCodeCommand):
        gcode_move = self._printer.lookup_object("gcode_move")
        offset = gcode_move.get_status()["homing_origin"].z
        offset += self._tap_adjust_z
        offset -= self._last_tap_gcode_adjustment
        configfile = self._printer.lookup_object("configfile")
        configfile.set(self._full_name, "tap_adjust_z", f"{offset:.3f}")
        self._log_msg(
            f"new tap_adjust_z: {offset:.3f}\n"
            "The SAVE_CONFIG command will update the printer config file\n"
            "with the above and restart the printer."
        )

    def probe_static_height(self, duration: float = 0.100) -> ProbeEddyProbeResult:
        with self.start_sampler() as sampler:
            start_time = self._print_time_now() + self._sensor.settle_time
            sampler.wait_for_sample_at_time(start_time + duration)

        times, heights = sampler.heights_between(start_time, math.inf)

        if len(heights) == 0:
            raise self._printer.command_error(f"No valid samples in time range (errors: {sampler.error_counts})")
        return ProbeEddyProbeResult.make(times, heights, errors=sampler.error_count)

    cmd_PROBE_help = "Probe the height using the eddy current sensor, moving the toolhead to the home trigger height, or Z if specified."

    def cmd_PROBE(self, gcmd: GCodeCommand):
        if not self._z_homed():
            raise self._printer.command_error("Must home Z before PROBE")

        z: float = gcmd.get_float("Z", self.params.home_trigger_height)

        th = self._printer.lookup_object("toolhead")
        th_pos = th.get_position()
        if th_pos[2] < z:
            th.manual_move([None, None, z + 3.0], self.params.lift_speed)
        th.manual_move([None, None, z], self.params.probe_speed)
        th.dwell(0.100)
        th.wait_moves()

        self.cmd_PROBE_STATIC(gcmd)

    cmd_PROBE_STATIC_help = "Probe the current height using the eddy current sensor without moving the toolhead."

    def cmd_PROBE_STATIC(self, gcmd: GCodeCommand):
        old_drive_current = self.current_drive_current()
        drive_current: int = gcmd.get_int("DRIVE_CURRENT", old_drive_current, minval=0, maxval=31)
        duration: float = gcmd.get_float("DURATION", 0.100, above=0.0)
        save: bool = gcmd.get_int("SAVE", 0) == 1
        home_z: bool = gcmd.get_int("HOME_Z", 0) == 1

        if not self.calibrated(drive_current):
            raise self._printer.command_error(f"Drive current {drive_current} not calibrated")

        try:
            self._sensor.set_drive_current(drive_current)

            if save:
                self.save_samples_path = "/tmp/eddy-probe-static.csv"

            r = self.probe_static_height(duration)

            if self._cmd_helper is not None:
                self._cmd_helper.last_z_result = float(r.value)

            self._last_probe_result = float(r.value)

            if home_z:
                th = self._printer.lookup_object("toolhead")
                th_pos = th.get_position()
                th_pos[2] = r.value
                self._set_toolhead_position(th_pos)
                self._log_debug(f"Homed Z to {r}")
            else:
                self._log_msg(f"Probed {r}")

        finally:
            self._sensor.set_drive_current(old_drive_current)

    cmd_SETUP_help = "Setup"

    def cmd_SETUP(self, gcmd: GCodeCommand):
        if not self._xy_homed():
            raise self._printer.command_error("X and Y must be homed before setup")

        trust_z_axis = gcmd.get_int("TRUST_Z", 0) == 1

        th = self._printer.lookup_object("toolhead")
        th_pos = th.get_position()

        if trust_z_axis:
            self.cmd_SETUP_next(gcmd, th_pos[0:3])
        else:
            if self._z_homed():
                # z-hop so that manual probe helper doesn't complain if we're already
                # at the right place
                self._z_hop()

            # Now reset the axis so that we have a full range to calibrate with
            # XXX This is proably not correct for some printers?
            zrange = th.get_kinematics().rails[2].get_range()
            th_pos[2] = zrange[1] - 20.0
            self._set_toolhead_position(th_pos)

            manual_probe.ManualProbeHelper(
                self._printer,
                gcmd,
                lambda kin_pos: self.cmd_SETUP_next(gcmd, kin_pos),
            )

    def cmd_SETUP_next(self, gcmd: GCodeCommand, kin_pos: Optional[List[float]]):
        if kin_pos is None:
            # User cancelled ManualProbeHelper
            self._z_not_homed()
            return

        debug = 1 if self.params.debug else 0
        debug = gcmd.get_int("DEBUG", debug) == 1
        trust_z_axis = gcmd.get_int("TRUST_Z", 0) == 1

        # We just did a ManualProbeHelper, so we're going to zero the z-axis
        # to make the following code easier, so it can assume z=0 is actually real zero.
        th = self._printer.lookup_object("toolhead")
        th_pos = th.get_position()
        if not trust_z_axis:
            th_pos[2] = 0.0
            self._set_toolhead_position(th_pos)

        orig_drive_current = self.current_drive_current()
        start_dc = self._sensor.drive_current_range[1]
        end_dc = self._sensor.drive_current_range[0]

        start_dc: int = gcmd.get_int("START_DRIVE_CURRENT", start_dc, minval=0, maxval=31)
        end_dc: int = gcmd.get_int("END_DRIVE_CURRENT", end_dc, minval=0, maxval=31)

        if end_dc > start_dc:
            raise self._printer.command_error("end can't be greater than start")

        # lift up above cal_z_max, and then move over so the probe
        # is over the nozzle position
        th.manual_move(
            [None, None, self.params.calibration_z_max + 3.0],
            self.params.lift_speed,
        )
        th.manual_move(
            [
                th_pos[0] - self.offset["x"],
                th_pos[1] - self.offset["y"],
                None,
            ],
            self.params.move_speed,
        )

        HOMING_REQ = (0.5, 5.0)
        TAP_REQ = (0.025, 3.0)

        def in_range(m, req):
            if m is None:
                return False
            return m.height_range[0] <= req[0] and m.height_range[1] >= req[1]

        def best_mapping(old_m, new_m, req):
            if not in_range(new_m, req):
                return old_m
            if old_m is None:
                return new_m
            # prefer ranges that expand the valid range at the bottom end,
            # as long as they're still within req
            if old_m.height_range[0] > new_m.height_range[0]:
                return new_m
            return old_m

        self._log_msg("Setup: calibrating...")
        best_homing = None
        best_tap = None
        saw_success = False
        for cur_dc in range(start_dc, end_dc - 1, -1):
            mapping = self._create_mapping(
                self.params.calibration_z_max,
                0.0,  # z_target
                self.params.probe_speed,
                self.params.lift_speed,
                cur_dc,
                report_errors=debug,
                write_debug_files=debug,
            )

            # ignore anything with max height range < 3.0
            if mapping is not None and (mapping.height_range[1] < 3.0 or mapping.height_range[0] > 1.0):
                mapping = None

            if mapping is not None and mapping.freq_spread() < 0.30:
                self._log_warning(
                    f"DC {cur_dc}: frequency spread {mapping.freq_spread():.2f}% is very low. (The sensor is probably mounted too high; the height includes any case thickness.)"
                )
                mapping = None

            if mapping is not None and mapping._ftoh_rms > 0.025:
                self._log_msg(f"DC {cur_dc}: calibration error rate is too high ({mapping._ftoh_rms:.3f})")
                mapping = None

            if mapping is None:
                if saw_success:
                    # don't test really low drive currents unnecessarily
                    self._log_msg(f"DC {cur_dc} not usable, not checking further")
                    break
                self._log_msg(f"DC {cur_dc} not usable...")
                continue

            saw_success = True
            self._log_msg(f"{mapping}")

            best_homing = best_mapping(best_homing, mapping, HOMING_REQ)
            best_tap = best_mapping(best_tap, mapping, TAP_REQ)

        if best_homing is None:
            self._log_error(
                "Unable to find valid homing drive current. Verify sensor height and consult documentation for troubleshooting."
            )
            self._sensor.set_drive_current(orig_drive_current)
            self._z_not_homed()
            return

        self._dc_to_fmap[best_homing.drive_current] = best_homing
        self._reg_drive_current = best_homing.drive_current
        self._log_msg(f"Using for homing: {best_homing}")

        if best_tap is not None:
            self._dc_to_fmap[best_tap.drive_current] = best_tap
            self._tap_drive_current = best_tap.drive_current
            self._log_msg(f"Using for tap: {best_tap}")
        else:
            self._log_warning(
                "Unable to find valid tap drive current, but homing should be functional. (Verify sensor height and consult documentation for troubleshooting.)"
            )

        self._log_msg("Setup complete. Verify Z homing with G28 Z, and tap with EDDYNG_TAP if tap calibration was successful.")

        self.reset_drive_current()
        self.save_config()
        self._z_not_homed()

    cmd_CALIBRATE_help = (
        "Calibrate the eddy current sensor. Specify DRIVE_CURRENT to calibrate for a different drive current "
        + "than the default. Specify START_Z to set a different calibration start point."
    )

    def cmd_CALIBRATE(self, gcmd: GCodeCommand):
        if not self._xy_homed():
            raise self._printer.command_error("X and Y must be homed before calibrating")

        if self._z_homed():
            # z-hop so that manual probe helper doesn't complain if we're already
            # at the right place
            self._z_hop()

        # Now reset the axis so that we have a full range to calibrate with
        th = self._printer.lookup_object("toolhead")
        th_pos = th.get_position()
        # XXX This is proably not correct for some printers?
        zrange = th.get_kinematics().rails[2].get_range()
        th_pos[2] = zrange[1] - 20.0
        self._set_toolhead_position(th_pos)

        manual_probe.ManualProbeHelper(
            self._printer,
            gcmd,
            lambda kin_pos: self.cmd_CALIBRATE_next(gcmd, kin_pos),
        )

    def cmd_CALIBRATE_next(self, gcmd: GCodeCommand, kin_pos: Optional[List[float]]):
        th = self._printer.lookup_object("toolhead")
        if kin_pos is None:
            # User cancelled ManualProbeHelper
            self._z_not_homed()
            return

        old_drive_current = self.current_drive_current()
        drive_current: int = gcmd.get_int("DRIVE_CURRENT", old_drive_current, minval=0, maxval=31)
        cal_z_max: float = gcmd.get_float("START_Z", self.params.calibration_z_max, above=2.0)
        z_target: float = gcmd.get_float("TARGET_Z", 0.0)

        probe_speed: float = gcmd.get_float("SPEED", self.params.probe_speed, above=0.0)
        lift_speed: float = gcmd.get_float("LIFT_SPEED", self.params.lift_speed, above=0.0)

        # We just did a ManualProbeHelper, so we're going to zero the z-axis
        # to make the following code easier, so it can assume z=0 is actually real zero.
        # The Eddy sensor calibration is done to nozzle height (not sensor or trigger height).
        th_pos = th.get_position()
        th_pos[2] = 0.0
        self._set_toolhead_position(th_pos)

        th.wait_moves()

        self._log_debug(f"calibrating from {kin_pos}, {th_pos}")

        # lift up above cal_z_max, and then move over so the probe
        # is over the nozzle position
        th.manual_move([None, None, cal_z_max + 3.0], lift_speed)
        th.manual_move(
            [
                th_pos[0] - self.offset["x"],
                th_pos[1] - self.offset["y"],
                None,
            ],
            self.params.move_speed,
        )

        mapping = self._create_mapping(
            cal_z_max,
            z_target,
            probe_speed,
            lift_speed,
            drive_current,
            report_errors=True,
            write_debug_files=True,
        )
        if mapping is None:
            self._log_error("Calibration failed")
            return

        self._dc_to_fmap[drive_current] = mapping
        self.save_config()

        # reset the Z homing state after alibration
        self._z_not_homed()

    def _create_mapping(
        self,
        z_start: float,
        z_target: float,
        probe_speed: float,
        lift_speed: float,
        drive_current: int,
        report_errors: bool,
        write_debug_files: bool,
    ) -> Optional[ProbeEddyFrequencyMap]:
        th = self._printer.lookup_object("toolhead")
        th_pos = th.get_position()

        # move to the start z of the mapping, going up first if we need to for backlash
        if th_pos[2] < z_start:
            th.manual_move([None, None, z_start + 3.0], lift_speed)
        th.manual_move([None, None, z_start], lift_speed)

        old_drive_current = self.current_drive_current()
        try:
            self._sensor.set_drive_current(drive_current)
            times, freqs, heights, vels = self._capture_samples_down_to(z_target, probe_speed)
            th.manual_move([None, None, z_start + 3.0], lift_speed)
        finally:
            self._sensor.set_drive_current(old_drive_current)

        if times is None:
            if report_errors:
                self._log_error(f"Drive current {drive_current}: No samples collected. This could be a hardware issue or an incorrect drive current.")
            else:
                self._log_warning(f"Drive current {drive_current}: Warning: no samples collected.")
            return None

        # and build a map
        mapping = ProbeEddyFrequencyMap(self)
        ok = mapping.calibrate_from_values(
            drive_current,
            times,
            freqs,
            heights,
            vels,
            report_errors,
            write_debug_files,
        )

        return mapping if ok else None

    def _capture_samples_down_to(self, z_target: float, probe_speed: float) -> tuple[List[float], List[float], List[float], List[float]]:
        th = self._printer.lookup_object("toolhead")
        th.dwell(0.500)  # give the sensor a bit to settle
        th.wait_moves()

        with self.start_sampler(calculate_heights=False) as sampler:
            first_sample_time = th.get_last_move_time()
            th.manual_move([None, None, z_target], probe_speed)
            last_sample_time = th.get_last_move_time()
            th.wait_moves()
            sampler.wait_for_sample_at_time(last_sample_time)

        if sampler.raw_count == 0:
            return None, None, None, None

        freqs = []
        heights = []
        times = []
        vels = []

        for i in range(sampler.raw_count):
            s_t = sampler.times[i]
            s_freq = sampler.freqs[i]
            if math.isnan(s_freq):
                continue
            s_pos, s_v = self._get_trapq_position(s_t)
            s_z = s_pos[2]
            if first_sample_time < s_t < last_sample_time and s_z >= z_target:
                times.append(s_t)
                freqs.append(s_freq)
                heights.append(s_z)
                vels.append(s_v)

        return times, freqs, heights, vels

    cmd_TEST_DRIVE_CURRENT_help = "Test drive current value when Z is already homed"

    def cmd_TEST_DRIVE_CURRENT(self, gcmd: GCodeCommand):
        drive_current: int = gcmd.get_int("DRIVE_CURRENT", self._reg_drive_current, minval=1, maxval=31)
        z_start: float = gcmd.get_float("START_Z", self.params.calibration_z_max, above=2.0)
        z_end: float = gcmd.get_float("TARGET_Z", 0.0)
        debug: bool = gcmd.get_int("DEBUG", 0) == 1
        self._log_msg(f"Testing Z={z_start:.3f} to Z={z_end:.3f}")

        mapping = self._create_mapping(
            z_start,
            z_end,
            self.params.probe_speed,
            self.params.lift_speed,
            drive_current,
            report_errors=False,
            write_debug_files=debug,
        )
        if mapping is None:
            self._log_error(f"Test failed: drive current {drive_current} is not usable.")

    #
    # PrinterProbe interface
    #

    def get_offsets(self, *args, **kwargs):
        # the z offset is the trigger height, because the probe will trigger
        # at z=trigger_height (not at z=0)
        return (
            self.offset["x"],
            self.offset["y"],
            self.params.home_trigger_height,
        )

    def get_probe_params(self, gcmd=None):
        return {
            "probe_speed": self.params.probe_speed,
            "lift_speed": self.params.lift_speed,
            "sample_retract_dist": 0.0,
        }

    def start_probe_session(self, gcmd):
        session = ProbeEddyScanningProbe(self, gcmd)
        session._start_session()
        return session

    def get_status(self, eventtime):
        if self._cmd_helper is not None:
            status = self._cmd_helper.get_status(eventtime)
        else:
            status = dict()
        status.update(
            {
                "name": self._full_name,
                "home_trigger_height": float(self.params.home_trigger_height),
                "tap_offset": float(self._tap_offset),
                "tap_adjust_z": float(self._tap_adjust_z),
                "last_probe_result": float(self._last_probe_result),
                "last_tap_z": float(self._last_tap_z),
            }
        )
        return status

    # Old Probe interface, for Kalico

    def get_lift_speed(self, gcmd=None):
        if gcmd is not None:
            return gcmd.get_float("LIFT_SPEED", self.params.lift_speed, above=0.0)
        return self.params.lift_speed

    def multi_probe_begin(self):
        pass

    def multi_probe_end(self):
        pass

    # This is a mishmash of cmd_PROBE and cmd_PROBE_STATIC. This run_probe
    # is the old one, different than the scanning session run_probe.
    def run_probe(self, gcmd=None, *args: Any, **kwargs: Any):
        z = self.params.home_trigger_height
        duration = 0.100

        if not self._z_homed():
            raise self._printer.command_error("Must home Z before PROBE")

        if not self.calibrated():
            raise self._printer.command_error("Eddy probe not calibrated!")

        th = self._printer.lookup_object("toolhead")
        th_pos = th.get_position()
        if th_pos[2] < z:
            th.manual_move([None, None, z + 3.0], self.params.lift_speed)
        th.manual_move([None, None, z], self.params.lift_speed)
        th.dwell(0.100)
        th.wait_moves()

        r = self.probe_static_height(duration)
        height = r.value
        height += self._tap_offset

        # At what Z position would the toolhead be at for the probe to read
        # _home_trigger_height? In other words, if the probe tells us
        # the height is 1.5 when the toolhead is at z=2.0, if the toolhead
        # was moved up to 2.5, then the probe should read 2.0.
        probe_z = z + (z - height)

        return [th_pos[0], th_pos[1], probe_z]

    #
    # Moving the sensor to the correct position
    #
    def _probe_to_start_position_unhomed(self):
        if not self._xy_homed():
            raise self._printer.command_error("xy must be homed")
        if not self.sampler_is_active():
            raise self._printer.command_error("probe_to_start_position_unhomed: no sampler active")
        if not self.calibrated():
            raise self._printer.command_error("EDDYng not calibrated!")

        th = self._printer.lookup_object("toolhead")
        th_pos = th.get_position()

        # debug logging
        th_kin = th.get_kinematics()
        zlim = th_kin.limits[2]
        rail_range = th_kin.rails[2].get_range()
        self._log_debug(
            f"probe to start unhomed: before movement: Z pos {th_pos[2]:.3f}, "
            f"Z limits {zlim[0]:.2f}-{zlim[1]:.2f}, "
            f"rail range {rail_range[0]:.2f}-{rail_range[1]:.2f}"
        )

        start_height_ok_factor = 0.100

        # This is where we want to get to
        start_height = self._home_start_height
        # This is where the probe thinks we are
        now_height = self._sampler.get_height_now()

        # If we can't get a value at all for right now, make sure the errors
        # are not "low amplitude"
        if now_height is None:
            ale_count = ahe_count = 0
            for i in range(5):
                status = self._sensor.latched_status_str()
                if "ALE" in status:
                    ale_count += 1
                elif "AHE" in status:
                    ahe_count += 1

            if ale_count == 0 and ahe_count == 0:
                raise self._printer.command_error(f"Got errors from sensor during homing, last status: {status}")

            if ale_count > 0 and ahe_count > 0:
                raise self._printer.command_error(f"Got mixed amplitude errors from sensor during homing, last status: {status}")

            if ahe_count > 0:
                self._log_info(f"probe_to_start_position_unhomed: AHE in status ({status}), assuming high and ignoring")
                return

            self._log_info(f"probe_to_start_position_unhomed: ALE in status ({status}), assuming too low")
            now_height = 0.0

        self._log_debug(f"probe_to_start_position_unhomed: now: {now_height} (start {start_height})")
        if abs(now_height - start_height) <= start_height_ok_factor:
            return

        th = self._printer.lookup_object("toolhead")
        th_pos = th.get_position()

        # If the sensor thinks we're too low we need to move back up before homing
        if now_height < start_height:
            move_up_by = min(start_height, start_height - now_height)
            # give ourselves some room to do so, homing typically doesn't move up,
            # and we should know that we have this room because the sensor tells us we're too low
            th_pos[2] = rail_range[1] - (move_up_by + 10.0)
            self._log_debug(f"probe_to_start_position_unhomed: resetting toolhead to z {th_pos[2]:.3f}")
            self._set_toolhead_position(th_pos)

            n_pos = th.get_position()

            zlim = th_kin.limits[2]
            rail_range = th_kin.rails[2].get_range()
            self._log_debug(
                f"after reset: Z pos {n_pos[2]:.3f}, Z limits {zlim[0]:.2f}-{zlim[1]:.2f}, rail range {rail_range[0]:.2f}-{rail_range[1]:.2f}"
            )

            th_pos[2] += move_up_by
            self._log_debug(f"probe_to_start_position_unhomed: moving toolhead up by {move_up_by:.3f} to {th_pos[2]:.3f}")
            th.manual_move([None, None, th_pos[2]], self.params.probe_speed)
            # TODO: this should just be th.wait_moves()
            self._sampler.wait_for_sample_at_time(th.get_last_move_time())

    def probe_to_start_position(self, z_pos=None):
        self._log_debug(f"probe_to_start_position (tt: {self.params.tap_threshold}, z-homed: {self._z_homed()})")

        # If we're not homed at all, rely on the sensor values to bring us to
        # a good place to start a diving probe from
        if not self._z_homed():
            if z_pos is not None:
                raise self._printer.command_error("Can't probe_to_start_position with an explicit Z without homed Z")
            self._probe_to_start_position_unhomed()
            return

        th = self._printer.lookup_object("toolhead")
        th.wait_moves()
        th_pos = th.get_position()

        # Note home_trigger_height and not home_start_height: if we're homed,
        # we don't need to do another dive and we just want to move to
        # the right position for probing.
        if z_pos is not None:
            start_z = z_pos
        else:
            start_z = self.params.home_trigger_height

        # If we're below, move up a bit beyond and the back down
        # to compensate for backlash
        if th_pos[2] < start_z:
            self._log_debug(f"probe_to_start_position: moving toolhead from {th_pos[2]:.3f} to {(start_z + 1.0):.3f}")
            th_pos[2] = start_z + 1.0
            th.manual_move(th_pos, self.params.lift_speed)

        self._log_debug(f"probe_to_start_position: moving toolhead from {th_pos[2]:.3f} to {start_z:.3f}")
        th_pos[2] = start_z
        th.manual_move(th_pos, self.params.probe_speed)

        th.wait_moves()

    #
    # Tap probe
    #
    cmd_TAP_help = "Calculate a z-offset by touching the build plate."

    def cmd_TAP(self, gcmd: GCodeCommand):
        drive_current = self._sensor.get_drive_current()
        try:
            self.cmd_TAP_next(gcmd, drive_current)
        finally:
            self._sensor.set_drive_current(drive_current)

    def do_one_tap(
        self,
        start_z: float,
        target_z: float,
        tap_speed: float,
        lift_speed: float,
        tapcfg: TapConfig,
    ) -> TapResult:
        self.probe_to_start_position(start_z)

        th = self._printer.lookup_object("toolhead")

        target_position = th.get_position()
        target_position[2] = target_z

        try:
            # configure the endstop for tap (gets reset at the end of a tap sequence,
            # also in finally just in case
            self._endstop_wrapper.tap_config = tapcfg

            endstops = [(self._endstop_wrapper, "probe")]
            hmove = HomingMove(self._printer, endstops)

            try:
                try:
                    probe_position = hmove.homing_move(target_position, tap_speed, probe_pos=True)
                finally:
                    # raise the toolhead as soon as the tap ends (or fails), so
                    # it isn't left pushing into the bed
                    finish_z = th.get_position()[2]
                    if finish_z < 1.0 and not self._printer.is_shutdown():
                        th.manual_move([None, None, start_z], lift_speed)

                if hmove.check_no_movement() is not None:
                    raise self._printer.command_error("Probe triggered prior to movement")

                probe_z = probe_position[2]

                self._log_debug(f"tap: probe_z: {probe_z:.3f} finish_z: {finish_z:.3f} moved up to {start_z:.3f}")

                if probe_z - target_z < 0.050:
                    # we detected a tap but it was too close to our target z
                    # to be trusted
                    # TODO: use velocity to determine this
                    return TapResult(error=Exception("Tap detected too close to target z"), toolhead_z=finish_z, probe_z=probe_z)

            except self._printer.command_error as err:
                if self._printer.is_shutdown():
                    raise self._printer.command_error("Probing failed due to printer shutdown")

                # If just sensor errors, let the caller handle it
                self._log_error(f"Tap failed with Z at {finish_z:.3f}: {err}")
                if any(m in str(err) for m in ("Sensor error", "Probe completed movement", "Probe triggered prior")):
                    return TapResult(error=err, toolhead_z=finish_z)
                else:
                    raise
        finally:
            self._endstop_wrapper.tap_config = None

        # The toolhead ended at finish_z, but probe_z is the actual zero.
        # finish_z should be below or equal to probe_z because there will always be
        # a bit of overshoot due to trigger delay, and because we actually
        # fire the trigger later than when the tap starts (and the tap start
        # time is what's used to compute probe_position)
        if finish_z > probe_z:
            raise self._printer.command_error(f"Unexpected: finish_z {finish_z:.3f} is above probe_z {probe_z:.3f} after tap")

        # How much the toolhead overshot the real z=0 position. This is the amount
        # the toolhead is pushing into the build plate.
        overshoot = probe_z - finish_z

        tap_start_time = self._endstop_wrapper.last_tap_start_time
        tap_end_time = self._endstop_wrapper.last_trigger_time
        tap_time = self.tap_time(tap_start_time, tap_end_time)

        return TapResult(
            error=None,
            probe_z=probe_z,
            toolhead_z=finish_z,
            overshoot=overshoot,
            tap_time=tap_time,
            tap_start_time=tap_start_time,
            tap_end_time=tap_end_time,
        )

    # Where the tap is placed between the start of tap detection and the
    # time the threshold was crossed
    def tap_time(self, tap_start_time: float, trigger_time: float) -> float:
        return tap_start_time + (trigger_time - tap_start_time) * self.params.tap_time_position

    def cmd_TAP_next(self, gcmd: GCodeCommand, orig_drive_current: int):
        self._log_debug("\nEDDYng Tap begin")

        tap_drive_current: int = gcmd.get_int(
            name="DRIVE_CURRENT",
            default=self._tap_drive_current,
            minval=1,
            maxval=31,
        )
        tap_speed: float = gcmd.get_float("SPEED", self.params.tap_speed, above=0.0)
        lift_speed: float = gcmd.get_float("RETRACT_SPEED", self.params.lift_speed, above=0.0)
        tap_start_z: float = gcmd.get_float("START_Z", self.params.tap_start_z, above=2.0)
        target_z: float = gcmd.get_float("TARGET_Z", self.params.tap_target_z)
        tap_threshold: float = gcmd.get_float("THRESHOLD", None)  # None so we have a sentinel value
        tap_threshold: float = gcmd.get_float("TT", tap_threshold)  # alias for THRESHOLD
        tap_adjust_z: float = gcmd.get_float("ADJUST_Z", self._tap_adjust_z)
        do_retract: bool = gcmd.get_int("RETRACT", 1) == 1
        samples: int = gcmd.get_int("SAMPLES", self.params.tap_samples, minval=1)
        max_samples: int = gcmd.get_int("MAX_SAMPLES", self.params.tap_max_samples, minval=samples)
        samples_stddev: float = gcmd.get_float("SAMPLES_STDDEV", self.params.tap_samples_stddev, above=0.0)
        use_median: bool = gcmd.get_int("USE_MEDIAN", 1 if self.params.tap_use_median else 0) == 1
        home_z: bool = gcmd.get_int("HOME_Z", 1) == 1
        write_plot_arg: int = gcmd.get_int("PLOT", None)

        if tap_threshold is None:
            tap_threshold = self.params.tap_threshold

        if not self._z_homed():
            raise self._printer.command_error("Z axis must be homed before tapping")

        write_tap_plot = self.params.write_tap_plot
        write_every_tap_plot = self.params.write_every_tap_plot and write_tap_plot
        if write_plot_arg is not None:
            write_tap_plot = write_plot_arg > 0
            write_every_tap_plot = write_plot_arg > 1

        tapcfg = TapConfig(threshold=tap_threshold, sos=self._tap_sos)

        results = []
        tap_z = None
        tap_stddev = None
        tap_overshoot = None
        sample_err_count = 0
        tap = None

        try:
            self._tap_plot_requested = write_tap_plot
            self._sensor.set_drive_current(tap_drive_current)

            sample_last_err = None

            for sample_i in range(max_samples):
                if self.params.debug:
                    self.save_samples_path = f"/tmp/tap-samples-{sample_i + 1}.csv"

                tap = self.do_one_tap(
                    start_z=tap_start_z,
                    target_z=target_z,
                    tap_speed=tap_speed,
                    lift_speed=lift_speed,
                    tapcfg=tapcfg,
                )

                if write_every_tap_plot:
                    try:
                        plots.write_tap_plot(self, tap, sample_i)
                    except Exception as e:
                        self._log_error(f"Failed to write tap plot: {e}")

                if tap.error:
                    if "too close to target z" in str(tap.error):
                        self._log_msg(f"Tap {sample_i+1}: failed: try lowering TARGET_Z by 0.100 (to {target_z - 0.100:.3f})")
                    else:
                        self._log_msg(f"Tap {sample_i+1}: failed ({tap.error})")
                    sample_err_count += 1
                    sample_last_err = tap
                    continue

                results.append(tap)

                self._log_msg(f"Tap {sample_i+1}: z={tap.probe_z:.3f}")
                self._log_debug(
                    f"tap[{sample_i+1}]: {tap.probe_z:.3f} toolhead at: {tap.toolhead_z:.3f} overshoot: {tap.overshoot:.3f} at {tap.tap_time:.4f}s"
                )

                if len(results) >= samples:
                    tap_z, tap_stddev, tap_overshoot = compute_tap_z(results, samples, samples_stddev, use_median)
                    if tap_z is not None:
                        break
        finally:
            self._tap_plot_requested = False
            self._sensor.set_drive_current(orig_drive_current)
            if write_tap_plot and not write_every_tap_plot and tap:
                try:
                    plots.write_tap_plot(self, tap)
                except Exception as e:
                    self._log_error(f"Failed to write tap plot: {e}")

        th = self._toolhead

        # If we didn't compute a tap_z report the error
        if tap_z is None:
            # raise toolhead on failed tap
            th.manual_move([None, None, tap_start_z], lift_speed)
            err_msg = "Tap failed:"
            if tap_stddev is not None:
                err_msg += f" stddev {tap_stddev:.3f} > {samples_stddev:.3f}."
                err_msg += " Consider adjusting tap_samples, tap_max_samples, or tap_samples_stddev."
            if sample_err_count > 0:
                err_msg += f" {sample_err_count} errors, last: {sample_last_err.error} at toolhead z={sample_last_err.toolhead_z:.3f}"
            self._log_error(err_msg)
            raise self._printer.command_error("Tap failed")

        # Adjust the computed tap_z by the user's tap_adjust_z, typically to raise
        # it to account for flex in the system (otherwise the Z would be too low)
        computed_tap_z = adjusted_tap_z = tap_z + tap_adjust_z
        self._last_tap_z = float(tap_z)

        homed_to_str = ""
        if home_z:
            th_pos = th.get_position()
            th_z = th_pos[2]
            true_z_zero = - computed_tap_z
            th_pos[2] = th_pos[2] + true_z_zero
            homed_to_str = f"homed z with true_z_zero={true_z_zero:.3f}, thz={th_z:.3f}, setz={th_pos[2]:.3f}, overshoot={tap_overshoot:.3f}, "
            self._set_toolhead_position(th_pos)
            adjusted_tap_z = 0.0

        gcode_move = self._printer.lookup_object("gcode_move")
        gcode_delta = adjusted_tap_z - gcode_move.homing_position[2]
        gcode_move.base_position[2] += gcode_delta
        gcode_move.homing_position[2] = adjusted_tap_z
        self._last_tap_gcode_adjustment = adjusted_tap_z

        #
        # Figure out the offset to apply to sensor readings at the home trigger height
        # for future probes.
        #
        # This is actually unrelated to tap, but is related to temperature compensation.
        # Bed mesh is going to read values relative to the probe's z_offset (home_trigger_height).
        # But we can't trust the probe's values directly, because of temperature effects.
        #
        # What we can do though is move the toolhead to that height, take a probe reading,
        # then save the delta there to apply as an offset for bed mesh in the future.
        # That makes this bed height effectively "0", which is fine, because this is
        # what we did tap at to get a height zero reading.
        #
        # Toolhead moves are absolute; they don't take into account the gcode offset.
        # Probes happen at absolute z=z_offset, so this doesn't take into account the
        # tap_z computed above. This does mean that the actual physical height probing happens at
        # is not likely to be exactly the same as the Z position, but all we care about is
        # variance from that position so this should be fine.
        th_now = th.get_position()
        th.manual_move([None, None, self.params.home_trigger_height + 1.0], lift_speed)
        th.manual_move([th_now[0] - self.params.x_offset, th_now[1] - self.params.y_offset, None], self.params.move_speed)
        th.manual_move([None, None, self.params.home_trigger_height], self.params.probe_speed)
        th.dwell(0.500)
        th.wait_moves()

        result = self.probe_static_height()
        self._tap_offset = float(self.params.home_trigger_height - result.value)

        self._log_msg(
            f"Probe computed tap at {computed_tap_z:.3f} (tap at z={tap_z:.3f}, "
            f"stddev {tap_stddev:.3f}) with {samples} samples, {homed_to_str}"
            f"sensor offset {self._tap_offset:.3f} at z={self.params.home_trigger_height:.3f}"
        )

        if do_retract:
            th.manual_move([None, None, self._home_start_height], lift_speed)
            th.wait_moves()
            th.flush_step_generation()

        self._log_debug("EDDYng Tap end\n")
