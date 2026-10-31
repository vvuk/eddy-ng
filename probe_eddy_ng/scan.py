# EDDY-ng
#
# Copyright (C) 2025  Vladimir Vukicevic <vladimir@pobox.com>
#
# Probing without moving Z: the probe session used by bed mesh, QGL etc.,
# and the experimental direct bed mesh scan
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

import logging
import math
from typing import Any, final, TYPE_CHECKING

import numpy as np

from .compat import GCodeCommand, bed_mesh, manual_probe, HAS_PROBE_RESULT_TYPE

if TYPE_CHECKING:
    from .probe_eddy_ng import ProbeEddy
    from .sampler import ProbeEddySampler


# Probe interface that does only scanning, no up/down movement.
# It scans at whatever height the probe is, but returns values
# as if the probing happened (i.e. relative to
# z_offset/home_trigger_height).
@final
class ProbeEddyScanningProbe:
    def __init__(self, eddy: ProbeEddy, gcmd: GCodeCommand):
        self.eddy = eddy
        self._printer = eddy._printer
        self._toolhead = self._printer.lookup_object("toolhead")

        # we're going to scan at this height; pull_probed_results
        # also expects to return values based on this height
        self._scan_z = eddy.params.home_trigger_height

        # sensor thinks is _home_trigger_height vs. what it actually is.
        # For example, if we do a tap, adjust, and then we move the toolhead up
        # to 2.0 but the sensor says 1.950, then this would be +0.050.
        self._tap_offset = eddy._tap_offset

        # how much to dwell at each sample position in addition to sample_time
        self._sample_time_delay = self.eddy.params.scan_sample_time_delay
        self._sample_time: float = gcmd.get_float("SAMPLE_TIME", self.eddy.params.scan_sample_time, above=0.0)
        self._is_rapid = gcmd.get("METHOD", "automatic").lower() == "rapid_scan"

        self._sampler: ProbeEddySampler = None

        self._notes = []

    def get_probe_params(self, gcmd):
        # this seems to be all that external users of get_probe_params
        # use (bed_mesh, axis_twist_compensation)
        return {
            "lift_speed": self.eddy.params.lift_speed,
            "probe_speed": self.eddy.params.probe_speed,
        }

    def _start_session(self):
        if not self.eddy._z_homed():
            raise self._printer.command_error("Z axis must be homed before probing")

        self.eddy.probe_to_start_position()
        self._sampler = self.eddy.start_sampler()

    def end_probe_session(self):
        self._sampler.finish()
        self._sampler = None

    def _rapid_lookahead_cb(self, time, th_pos):
        # The time passed here is the time when the move finishes;
        # but this is super obnoxious because we don't get any info
        # here about _where_ the move is to. So we explicitly pass
        # in the last position in run_probe
        start_time = time - self._sample_time / 2.0
        self._notes.append((start_time, th_pos))

    def run_probe(self, gcmd, *args: Any, **kwargs: Any):
        th = self._toolhead
        th_pos = th.get_position()

        if self._is_rapid:
            # this callback is attached to the last move in the queue, so that
            # we can grab the toolhead position when the toolhead actually hits it

            self._toolhead.register_lookahead_callback(lambda time: self._rapid_lookahead_cb(time, th_pos))
            return

        th.dwell(self._sample_time_delay)
        start_time = th.get_last_move_time()
        self._toolhead.dwell(self._sample_time + self._sample_time_delay)
        self._notes.append((start_time, th_pos))

    def pull_probed_results(self):
        if self._is_rapid:
            # Flush lookahead (so all lookahead callbacks are invoked)
            self._toolhead.get_last_move_time()

        # make sure we get the sample for the final move
        self._sampler.wait_for_sample_at_time(self._notes[-1][0] + self._sample_time)

        # note: we can't call finish() here! this session can continue to be used
        # to probe additional points and pull them, because that's what QGL does.

        results = []

        logging.info(f"ProbeEddyScanningProbe: pulling {len(self._notes)} results")
        for start_time, th_pos in self._notes:
            end_time = start_time + self._sample_time
            height = self._sampler.find_height_at_time(start_time, end_time)

            if not math.isclose(th_pos[2], self._scan_z, rel_tol=1e-3):
                logging.info(
                    f"ProbeEddyScanningProbe warning: toolhead not at home_trigger_height ({self._scan_z:.3f}) during probes (saw {th_pos[2]:.3f})"
                )

            # adjust the sensor height value based on the fine-tuned tap offset amount
            height += self._tap_offset

            # the delta between where the toolhead thinks it should be (since it
            # should be homed), and the actual physical offset (height)
            z_deviation = th_pos[2] - height

            # what callers want to know is "what Z would the toolhead be at, if it was at the height
            # the probe would 'trigger'", because this is all done in terms of klicky-type probes
            z = float(self._scan_z + z_deviation)

            if HAS_PROBE_RESULT_TYPE:
                bed_x = th_pos[0] + self.eddy.params.x_offset
                bed_y = th_pos[1] + self.eddy.params.y_offset
                res = manual_probe.ProbeResult(bed_x, bed_y, z_deviation,
                                               th_pos[0], th_pos[1], th_pos[2])
                result_wrapper = [res]
                self._printer.send_event("probe:update_results", result_wrapper)
                res = result_wrapper[0]
            else:
                res = [th_pos[0], th_pos[1], z]
                self._printer.send_event("probe:update_results", res)

            results.append(res)

        # reset notes so that this session can continue to be used
        self._notes = []

        return results


@final
class BedMeshScanHelper:
    def __init__(self, eddy, config):
        self._eddy = eddy
        self._printer = eddy._printer

        bmc = config.getsection("bed_mesh")
        self._bed_mesh = eddy._printer.load_object(bmc, "bed_mesh")
        self._x_points, self._y_points = bmc.getintlist("probe_count", count=2, note_valid=False)
        self._x_min, self._y_min = bmc.getfloatlist("mesh_min", count=2, note_valid=False)
        self._x_max, self._y_max = bmc.getfloatlist("mesh_max", count=2, note_valid=False)
        self._speed = bmc.getfloat("speed", 100.0, above=0.0, note_valid=False)
        self._scan_z = bmc.getfloat("horizontal_move_z", self._eddy.params.home_trigger_height, above=0.0, note_valid=False)

        self._x_offset = self._eddy.params.x_offset
        self._y_offset = self._eddy.params.y_offset

        self._mesh_path = self._generate_path()

    # A serpentine path: rows from min_y to max_y, alternating direction
    def _generate_path(self):
        x_vals = np.linspace(self._x_min, self._x_max, self._x_points)
        y_vals = np.linspace(self._y_min, self._y_max, self._y_points)
        path = []
        for j, y in enumerate(y_vals):
            path.extend((x, y) for x in (x_vals[::-1] if j % 2 else x_vals))
        return path

    def _scan_path(self):
        th = self._eddy._toolhead
        times = []

        for pt in self._mesh_path:
            # TODO bounds
            th.manual_move([pt[0] - self._x_offset, pt[1] - self._y_offset, None], self._speed)
            th.register_lookahead_callback(lambda t: times.append(t))

        th.wait_moves()

        return times

    def _set_bed_mesh(self, heights):
        # heights is in _mesh_path order; undo the serpentine so rows go
        # min_y..max_y and each row min_x..max_x
        matrix = self._scan_z - np.asarray(heights, dtype=float).reshape(self._y_points, self._x_points)
        matrix[1::2] = matrix[1::2, ::-1]
        matrix = matrix.tolist()

        # heights may have NaNs in them if there were no samples during a point's
        # scan time; fill these in with the average of the valid adjacent points
        for j in range(self._y_points):
            for i in range(self._x_points):
                if not math.isnan(matrix[j][i]):
                    continue
                adjacent = [
                    matrix[nj][ni]
                    for nj, ni in ((j - 1, i), (j + 1, i), (j, i - 1), (j, i + 1))
                    if 0 <= nj < self._y_points and 0 <= ni < self._x_points and not math.isnan(matrix[nj][ni])
                ]
                if not adjacent:
                    raise self._printer.command_error(f"No sample for mesh point [{i},{j}] and all adjacent samples are missing")
                matrix[j][i] = sum(adjacent) / len(adjacent)

        params = self._bed_mesh.bmc.mesh_config.copy()
        params.update(
            {
                "min_x": self._x_min,
                "max_x": self._x_max,
                "min_y": self._y_min,
                "max_y": self._y_max,
                "x_count": self._x_points,
                "y_count": self._y_points,
            }
        )
        mesh = bed_mesh.ZMesh(params, None)
        try:
            mesh.build_mesh(matrix)
        except bed_mesh.BedMeshError as e:
            raise self._printer.command_error(str(e))
        self._bed_mesh.set_mesh(mesh)
        self._eddy._log_msg("Mesh scan complete")

    def scan(self):
        th = self._eddy._toolhead

        # move to the start point
        v = self._mesh_path[0]
        th.manual_move([None, None, 10.0], self._eddy.params.lift_speed)
        th.manual_move([v[0] - self._x_offset, v[1] - self._y_offset, None], self._speed)
        th.manual_move([None, None, self._scan_z], self._eddy.params.probe_speed)
        th.wait_moves()

        heights = []

        sample_time = self._eddy.params.scan_sample_time

        with self._eddy.start_sampler() as sampler:
            path_times = self._scan_path()
            sampler.wait_for_sample_at_time(path_times[-1] + sample_time * 2.0)

        heights = sampler.find_heights_at_times([(t - sample_time / 2.0, t + sample_time / 2.0) for t in path_times])

        # Note plus tap_offset here, vs -tap_offset when probing. These are actual
        # heights, the other is "offset from real"
        heights = [h + self._eddy._tap_offset for h in heights]

        with open("/tmp/mesh.csv", "w") as mfile:
            mfile.write("time,x,y,z\n")
            for i in range(len(self._mesh_path)):
                t = path_times[i]
                x = self._mesh_path[i][0]
                y = self._mesh_path[i][1]
                z = heights[i]
                mfile.write(f"{t},{x},{y},{z}\n")

        self._set_bed_mesh(heights)

