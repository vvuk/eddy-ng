# EDDY-ng
#
# Copyright (C) 2025  Vladimir Vukicevic <vladimir@pobox.com>
#
# Experimental nozzle alignment using an upwards-facing eddy current
# sensor ("bigfoot"). See README-BIGFOOT.md.
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

import logging
import math
import numpy as np
from typing import cast, TYPE_CHECKING

from scipy.optimize import curve_fit

if TYPE_CHECKING:
    from .probe_eddy_ng import ProbeEddy

    try:
        from klippy.toolhead import ToolHead
    except ImportError:
        from toolhead import ToolHead


class BigfootProbe:
    def __init__(self, eddy: ProbeEddy):
        self.eddy = eddy
        self.params = self.eddy.params
        self.last_result = None
        self.last_offset = None
        self._printer = eddy._printer
        self._sensor = eddy._sensor
        self._reference = [0.0, 0.0, 0.0]

        gcode = self._printer.lookup_object("gcode")

        names = [self.eddy._name]
        if self.params.primary:
            names.append(None)

        for name in names:
            gcode.register_mux_command("EDDYNG_NOZZLE_POSITION_SCAN", "SENSOR", name, self.cmd_EDDYNG_NOZZLE_POSITION_SCAN)
            gcode.register_mux_command(
                "EDDYNG_NOZZLE_POSITION_SET_REFERENCE", "SENSOR", name, self.cmd_EDDYNG_NOZZLE_POSITION_SET_REFERENCE
            )
            gcode.register_mux_command("EDDYNG_SET_TOOL_OFFSET", "SENSOR", name, self.cmd_EDDYNG_SET_TOOL_OFFSET)

    def update_status(self, status):
        if self.last_result:
            status.update({"bigfoot_scan_result": self.last_result})
        if self.last_offset:
            status.update({"bigfoot_scan_offset": self.last_offset})

    def _last_probe_z_result(self):
        probe = self._printer.lookup_object("probe")
        ps = probe.get_status(self._printer.get_reactor().monotonic())
        return ps.get("last_z_result", None)

    def cmd_EDDYNG_SET_TOOL_OFFSET(self, gcmd):
        tool_name = gcmd.get("TOOL")
        x = gcmd.get_float("X", None)
        y = gcmd.get_float("Y", None)
        z = gcmd.get_float("Z", None)

        if (x is None or y is None or z is None) and not self.last_offset:
            raise self._printer.command_error("EDDYNG_SET_TOOL_OFFSET: need X Y Z if no last scan result")

        x = x if x is not None else self.last_offset[0]
        y = y if y is not None else self.last_offset[1]
        z = z if z is not None else self.last_offset[2]

        tool = self._printer.lookup_object(f"tool {tool_name}", None)
        if tool is None:
            raise self._printer.command_error(f"EDDYNG_SET_TOOL_OFFSET: Tool {tool_name} not found")

        tool.gcode_x_offset = x
        tool.gcode_y_offset = y
        tool.gcode_z_offset = z

        gcmd.respond_info(f"Tool {tool_name} offset set to {x:.3f} {y:.3f} {z:.3f}")

    def cmd_EDDYNG_NOZZLE_POSITION_SET_REFERENCE(self, gcmd):
        x = gcmd.get_float("X", None)
        y = gcmd.get_float("Y", None)
        z = gcmd.get_float("Z", None)
        z_use_probe = gcmd.get_int("Z_USE_PROBE", 0) == 1

        if z_use_probe and z is not None:
            raise self._printer.command_error("Can't specify both Z and Z_USE_PROBE")

        if z is None and not z_use_probe:
            raise self._printer.command_error("Must specify either Z or Z_USE_PROBE")

        if (x is None or y is None) and not self.last_result:
            raise self._printer.command_error("Need both X and Y if no last scan result")

        x = x if x is not None else self.last_result[0]
        y = y if y is not None else self.last_result[1]
        z = z or 0.0
        if z_use_probe:
            z = self._last_probe_z_result()
            if z is None:
                raise self._printer.command_error("Z_USE_PROBE specified, but no last probe result to use for Z")

        self._reference = [x, y, z]
        gcmd.respond_info(f"EDDYNG_NOZZLE_POSITION_SCAN reference set to {x:.3f} {y:.3f} {z:.3f}")

    def cmd_EDDYNG_NOZZLE_POSITION_SCAN(self, gcmd):
        try:
            self.cmd_EDDYNG_NOZZLE_POSITION_SCAN_actual(gcmd)
        except Exception as e:
            logging.exception("EDDYNG_NOZZLE_POSITION_SCAN error")
            self.eddy._log_error(f"EDDYNG_NOZZLE_POSITION_SCAN error: {e}")

    def cmd_EDDYNG_NOZZLE_POSITION_SCAN_actual(self, gcmd):
        use_last = gcmd.get_int("USE_LAST", 0) == 1
        if use_last:
            if not self.last_result:
                raise self._printer.command_error("No last scan result to use for USE_LAST")
            x, y = self.last_result
            z = self.eddy.params.bigfoot_scan_position[2] if self.eddy.params.bigfoot_scan_position else None
        elif self.eddy.params.bigfoot_scan_position:
            x, y, z = self.eddy.params.bigfoot_scan_position
        else:
            x, y, z = None, None, None
        x = gcmd.get_float("X", x)
        y = gcmd.get_float("Y", y)
        z = gcmd.get_float("Z", z)
        if x is None or y is None or z is None:
            raise self._printer.command_error("EDDYNG_NOZZLE_POSITION_SCAN requires X, Y, and Z; bigfoot_scan_position in the config; or USE_LAST")

        scan_z_use_probe = gcmd.get_int("SCAN_Z_USE_PROBE", 0) == 1

        radius = gcmd.get_float("RADIUS", 5.0)
        passes = gcmd.get_int("PASSES", 3)

        move_speed = gcmd.get_float("MOVE_SPEED", 100.0)
        speed = gcmd.get_float("SPEED", self.eddy.params.probe_speed)

        zval = 0.0
        if scan_z_use_probe:
            zval = self._last_probe_z_result()
            if zval is None:
                raise self._printer.command_error("SCAN_Z_USE_PROBE specified, but no last probe result to use for scan Z")

        th = cast("ToolHead", self._printer.lookup_object("toolhead"))

        th_z = th.get_position()[2]
        if th_z < z:
            th.manual_move([None, None, z], self.eddy.params.lift_speed)

        th.manual_move([x, y, None], move_speed)
        th.manual_move([None, None, z], self.eddy.params.lift_speed)
        th.wait_moves()

        def scan_between(start, end, passes):
            th.manual_move([start[0], start[1], z], move_speed)
            th.dwell(0.100)

            tstart = th.get_last_move_time()
            while passes > 0:
                th.manual_move([end[0], end[1], z], speed)
                passes -= 1
                if passes == 0:
                    break
                th.manual_move([start[0], start[1], z], speed)
                passes -= 1
            tend = th.get_last_move_time()

            th.dwell(0.100)
            th.wait_moves()
            return tstart, tend

        def write_debug(axis):
            mode = "w" if axis == "x" else "a"
            with open("/tmp/bigfoot.csv", mode) as datafile:
                if axis == "x":
                    datafile.write("axis,time,x,y,z,f,rf\n")
                for i in range(len(sampler.times)):
                    t = sampler.times[i]
                    f = sampler.freqs[i]
                    rf = sampler.raw_freqs[i]
                    pos, v = self.eddy._get_trapq_position(t)
                    x, y, z = pos if pos is not None else ("", "", "")
                    datafile.write(f"{axis},{t},{x},{y},{z},{f},{rf}\n")

        result = [-math.inf, -math.inf]

        for axindex, axis in enumerate(["x", "y"]):
            tstart_pos = [x, y]
            tend_pos = [x, y]

            tstart_pos[axindex] -= radius
            tend_pos[axindex] += radius

            with self.eddy.start_sampler(calculate_heights=False) as sampler:
                tstart, tend = scan_between(tstart_pos, tend_pos, passes)
                sampler.wait_for_sample_at_time(tend, max_wait_time=1.0)
                sampler.finish()
                sampler.trim_to_times(tstart, tend)

                np_times = np.asarray(sampler.times)
                np_rf = np.asarray(sampler.freqs)
                np_pos = np.asarray([self.eddy._get_trapq_position(t)[0][axindex] for t in np_times])

                write_debug(axis)

                def gaussian(x, a, x0, sigma, offset):
                    return a * np.exp(-((x - x0) ** 2) / (2 * sigma**2)) + offset

                p0 = [
                    np_rf.max() - np_rf.min(),  # a
                    np_pos[np.argmax(np_rf)],  # x0
                    (np_pos.max() - np_pos.min()) / 4,  # sigma
                    np_rf.min(),
                ]  # offset

                bounds = ([0, np_pos.min(), 1e-6, 0], [np.inf, np_pos.max(), (np_pos.max() - np_pos.min()) * 2, np.inf])

                popt, _ = curve_fit(gaussian, np_pos, np_rf, p0=p0, bounds=bounds, maxfev=10000)
                a, x0, sigma, offset = popt
                val = gaussian(x0, *popt)

                self.eddy._log_msg(
                    f"{axis} {x0:.3f} (fv {val:.6f} sigma {sigma:.6f} over {tend - tstart:.3f}s, {len(sampler.times)} samples)"
                )
                result[axindex] = float(x0)

        # we're moving at speed mm/sec. we're sampling at rcount_sec.
        rcount_sec = self.eddy._sensor.get_rcount_sec()
        # samples_per_sec = 1.0 / rcount_sec
        # TODO: not correct, need to also look at the quantization of the data.
        # would be handy to be able to log this.
        # best_precision = speed / samples_per_sec

        msg = f"Center: {result[0]:.3f} {result[1]:.3f} (samp_ms: {rcount_sec * 1000.0:.3f} speed: {speed:.3f})"

        offset = [result[0] - self._reference[0], result[1] - self._reference[1], zval - self._reference[2]]

        if self._reference[0] != 0.0 and self._reference[1] != 0.0 and self._reference[2] != 0.0:
            msg += f"\nOffset: {offset[0]:.3f} {offset[1]:.3f} {offset[2]:.3f} (samp_ms: {rcount_sec * 1000.0:.3f} speed: {speed:.3f})"

        self.eddy._log_msg(msg)

        self.last_result = result
        self.last_offset = offset
