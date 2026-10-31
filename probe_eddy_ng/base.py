# EDDY-ng
#
# Copyright (C) 2025  Vladimir Vukicevic <vladimir@pobox.com>
#
# What every probe_eddy_ng section has, whether it's a probe or a bigfoot
# nozzle sensor: the sensor itself, sampling, logging, and the basic
# commands.
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

import logging
import math
from typing import List, Tuple, Union

from .compat import chelper, ConfigWrapper, GCodeCommand, Printer, ToolHead
from . import ldc1612_ng
from .params import ProbeEddyParams
from .sampler import ProbeEddySampler


class EddyNgSensor:
    def __init__(self, config: ConfigWrapper):
        self._printer: Printer = config.get_printer()
        self._reactor = self._printer.get_reactor()
        self._gcode = self._printer.lookup_object("gcode")
        self._full_name = config.get_name()
        self._name = self._full_name.split()[-1]

        self.params = ProbeEddyParams()
        self.params.load_from_config(config)

        # drive current saved by older versions
        old_saved_reg_drive_current = config.getint("saved_reg_drive_current", 0, minval=0, maxval=31)

        self._sensor = ldc1612_ng.LDC1612_ng(
            config,
            name=self._name,
            primary=self.params.primary,
            drive_current=self.params.reg_drive_current or old_saved_reg_drive_current,
            debug=self.params.debug,
        )
        self._mcu = self._sensor.get_mcu()
        self._toolhead: ToolHead = None  # filled in _handle_connect
        self._trapq = None

        # There can only be one active sampler at a time
        self._sampler: ProbeEddySampler = None
        self._last_sampler: ProbeEddySampler = None
        self.save_samples_path = None
        # Set during TAP when a tap plot will be written
        self._tap_plot_requested = False

        self._ffi_pull_move_1 = chelper.get_ffi()[0].new("struct pull_move[1]")

        self._printer.register_event_handler("gcode:command_error", self._handle_command_error)
        self._printer.register_event_handler("klippy:connect", self._handle_connect)

        self._register_commands(
            [
                ["STATUS", "PES"],
                "STREAM_START",
                "STREAM_STOP",
            ]
        )

    # Register PROBE_EDDY_NG_<cmd> and EDDYNG_<cmd> (plus any aliases) for
    # this sensor, and for no SENSOR= at all if this is the primary one. Each
    # entry is a command name or a [name, aliases...] list; the handler is
    # cmd_<name>.
    def _register_commands(self, commands: List[Union[str, List[str]]]):
        names = [self._name]
        if self.params.primary:
            names.append(None)

        prefixes = ["PROBE_EDDY_NG", "EDDYNG"]

        for name in names:
            for v in commands:
                if type(v) is list:
                    cmd_name = v[0]
                    aliases = v[1:]
                else:
                    cmd_name = v
                    aliases = []

                func_name = f"cmd_{cmd_name}"

                cmd_fn = getattr(self, func_name)
                cmd_help = getattr(self, f"{func_name}_help", None)
                for p in prefixes:
                    self._gcode.register_mux_command(f"{p}_{cmd_name}", "SENSOR", name, cmd_fn, cmd_help)
                for alias in aliases:
                    self._gcode.register_mux_command(alias, "SENSOR", name, cmd_fn, (cmd_help or "") + f" (alias for {cmd_name})")

    # Whether there's a frequency to height mapping for the drive current;
    # overridden by the probe
    def calibrated(self, drive_current=None) -> bool:
        return False

    def _log_error(self, msg):
        logging.error(f"{self._name}: {msg}")
        self._gcode.respond_raw(f"!! EDDYng: {msg}\n")

    def _log_warning(self, msg):
        logging.warning(f"{self._name}: {msg}")
        self._gcode.respond_raw(f"!! EDDYng: {msg}\n")

    def _log_msg(self, msg):
        logging.info(f"{self._name}: {msg}")
        self._gcode.respond_info(f"{msg}", log=False)

    def _log_info(self, msg):
        logging.info(f"{self._name}: {msg}")

    def _log_debug(self, msg):
        if self.params.debug:
            logging.info(f"{self._name}: {msg}")

    def _handle_command_error(self, gcmd=None):
        try:
            if self._sampler is not None:
                logging.info("COMMAND ERROR")
                self._sampler.finish()
        except:
            logging.exception("EDDYng handle_command_error: sampler.finish() failed")

    def _handle_connect(self):
        self._toolhead = self._printer.lookup_object("toolhead")
        self._trapq = self._toolhead.get_trapq()
        for msg in self.params._warning_msgs:
            self._log_warning(msg)

    def _get_trapq_position(self, print_time: float) -> Tuple[Tuple[float, float, float] | None, float | None]:
        ffi_main, ffi_lib = chelper.get_ffi()
        count = ffi_lib.trapq_extract_old(self._trapq, self._ffi_pull_move_1, 1, 0.0, print_time)
        if not count:
            return None, None
        move = self._ffi_pull_move_1[0]
        move_time = max(0.0, min(move.move_t, print_time - move.print_time))
        dist = (move.start_v + 0.5 * move.accel * move_time) * move_time
        pos = (
            move.start_x + move.x_r * dist,
            move.start_y + move.y_r * dist,
            move.start_z + move.z_r * dist,
        )
        velocity = move.start_v + move.accel * move_time
        return pos, velocity

    def _get_trapq_height(self, print_time: float) -> float:
        th_pos, _ = self._get_trapq_position(print_time)
        if th_pos is None:
            return None
        return th_pos[2]

    def current_drive_current(self) -> int:
        return self._sensor.get_drive_current()

    def _print_time_now(self):
        return self._mcu.estimated_print_time(self._reactor.monotonic())

    def start_sampler(self, *args, **kwargs) -> ProbeEddySampler:
        if self._sampler:
            raise self._printer.command_error("EDDYng: Already sampling! (This shouldn't happen; FIRMWARE_RESTART to fix)")
        self._sampler = ProbeEddySampler(self, *args, **kwargs)
        self._sampler.start()
        return self._sampler

    def wants_filter_values(self) -> bool:
        # The tap filter value streamed from the sensor is only used for sample
        # dumps and tap plots, so don't spend bandwidth on it otherwise
        return self.save_samples_path is not None or self._tap_plot_requested

    def sampler_is_active(self):
        return self._sampler is not None and self._sampler.active()

    # Called by samplers when they're finished
    def _sampler_finished(self, sampler: ProbeEddySampler):
        if self._sampler is not sampler:
            raise self._printer.command_error("EDDYng finishing sampler that's not active")

        self._last_sampler = sampler
        self._sampler = None

        if self.save_samples_path is not None:
            with open(self.save_samples_path, "w") as data_file:
                times = sampler.times
                raw_freqs = sampler.raw_freqs
                accum_vals = sampler.accum_vals
                freqs = sampler.freqs
                heights = sampler.heights

                data_file.write("time,frequency,z,kin_z,kin_v,raw_f,sos_val,trigger_time,tap_start_time\n")
                trigger_time = sampler.memos.get("trigger_time", "")
                tap_start_time = sampler.memos.get("tap_start_time", "")
                for i in range(len(times)):
                    past_pos, past_v = self._get_trapq_position(times[i])
                    past_k_z = past_pos[2] if past_pos is not None else ""
                    past_v = past_v if past_v is not None else ""
                    data_file.write(
                        f"{times[i]},{freqs[i]},{heights[i] if heights else ''},{past_k_z},{past_v},{raw_freqs[i]},{accum_vals[i]},{trigger_time},{tap_start_time}\n"
                    )
            logging.info(f"Wrote {len(times)} samples to {self.save_samples_path}")
            self.save_samples_path = None

    cmd_STATUS_help = "Query the last raw coil value and status"

    def cmd_STATUS(self, gcmd: GCodeCommand):
        result = self._sensor.read_one_value()

        status = result.status
        freqval = result.freqval
        freq = result.freq
        height = -math.inf

        err = ""
        if freqval > 0x0FFFFFFF:
            height = -math.inf
            freq = 0.0
            err = f"ERROR: {bin(freqval >> 28)} "
        elif freq <= 0.0:
            err += "(Zero frequency) "
        elif self.calibrated():
            height = self.freq_to_height(freq)
        else:
            err += "(Not calibrated) "

        gcmd.respond_info(
            f"Last coil value: {freq:.2f} ({height:.3f}mm) raw: {hex(freqval)} {err}status: {hex(status)} {self._sensor.status_to_str(status)}"
        )

    def cmd_STREAM_START(self, gcmd):
        self.save_samples_path = "/tmp/stream.csv"
        msg = f"Starting stream to {self.save_samples_path}"
        if not self.calibrated():
            msg += " (not calibrated)"
        gcmd.respond_info(msg)
        self.start_sampler(calculate_heights=self.calibrated())

    def cmd_STREAM_STOP(self, gcmd):
        self._log_info("Eddy sampling finished")
        sampler = self._sampler
        self._sampler.finish()
        gcmd.respond_info(f"Stopped stream ({len(sampler.times)} samples, of which {sampler.error_count} errors)")

