# EDDY-ng
#
# Copyright (C) 2025  Vladimir Vukicevic <vladimir@pobox.com>
#
# The virtual Z endstop, used for both homing and tapping
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

from typing import Optional, final, TYPE_CHECKING

from .compat import mcu, pins

if TYPE_CHECKING:
    from .probe_eddy_ng import ProbeEddy
    from .sampler import ProbeEddySampler
    from .tap import TapConfig


# This is a ProbeEndstopWrapper-compatible class,
# which also forwards the "mcu_probe" methods.
@final
class ProbeEddyEndstopWrapper:
    REASON_BASE = mcu.MCU_trsync.REASON_COMMS_TIMEOUT + 1
    REASON_ERROR_SENSOR = REASON_BASE + 0
    REASON_ERROR_PROBE_TOO_LOW = REASON_BASE + 1
    REASON_ERROR_TOO_EARLY = REASON_BASE + 2

    def __init__(self, eddy: ProbeEddy):
        self.eddy = eddy
        self._sensor = eddy._sensor
        self._printer = eddy._printer
        self._mcu = eddy._mcu
        self._reactor = eddy._reactor

        # filled in by the outside for a tap
        self.tap_config: Optional[TapConfig] = None

        self._dispatch = mcu.TriggerDispatch(self._mcu)

        # the times of the last successful endstop home_wait
        self.last_trigger_time = 0.0
        self.last_tap_start_time = 0.0

        self._sampler: ProbeEddySampler = None

        # Register z_virtual_endstop pin
        self._printer.lookup_object("pins").register_chip("probe", self)
        # Register event handlers
        self._printer.register_event_handler("klippy:mcu_identify", self._handle_mcu_identify)
        self._printer.register_event_handler("homing:homing_move_begin", self._handle_homing_move_begin)
        self._printer.register_event_handler("homing:homing_move_end", self._handle_homing_move_end)

        # copy some things in for convenience
        self._home_trigger_height = self.eddy.params.home_trigger_height
        self._home_trigger_safe_start_offset = self.eddy.params.home_trigger_safe_start_offset

    def _handle_mcu_identify(self):
        kin = self._printer.lookup_object("toolhead").get_kinematics()
        for stepper in kin.get_steppers():
            if stepper.is_active_axis("z"):
                self.add_stepper(stepper)


    def _handle_homing_move_begin(self, hmove):
        if self not in hmove.get_mcu_endstops():
            return
        self._sampler = self.eddy.start_sampler()
        # if we're doing a tap, we're already in the right position;
        # otherwise move there
        if self.tap_config is None:
            self.eddy._probe_to_start_position_unhomed()

    def _handle_homing_move_end(self, hmove):
        if self not in hmove.get_mcu_endstops():
            return
        self._sampler.finish()

    def setup_pin(self, pin_type, pin_params):
        if pin_type != "endstop" or pin_params["pin"] != "z_virtual_endstop":
            raise pins.error("Probe virtual endstop only useful as endstop pin")
        if pin_params["invert"] or pin_params["pullup"]:
            raise pins.error("Can not pullup/invert probe virtual endstop")
        return self

    # these are the "MCU Probe" methods
    def get_mcu(self):
        return self._mcu

    def add_stepper(self, stepper):
        self._dispatch.add_stepper(stepper)

    def get_steppers(self):
        return self._dispatch.get_steppers()

    def get_position_endstop(self):
        if self.tap_config is None:
            return self._home_trigger_height
        else:
            return 0.0

    def home_start(self, print_time, sample_time, sample_count, rest_time, triggered=True):
        if not self._sampler.active():
            raise self._printer.command_error("home_start called without a sampler active")

        self.last_trigger_time = 0.0
        self.last_tap_start_time = 0.0

        trigger_height = self._home_trigger_height
        safe_height = trigger_height + self._home_trigger_safe_start_offset

        if self.tap_config is None:
            safe_time = print_time + self.eddy.params.home_trigger_safe_time_offset
            trigger_freq = self.eddy.height_to_freq(trigger_height)
            safe_freq = self.eddy.height_to_freq(safe_height)
        else:
            # TODO: the home trigger safe time won't work, because we'll pass
            # the home_trigger_height maybe by default given where tap might
            # start
            safe_time = 0
            # initial freq to pass through
            safe_freq = self.eddy.height_to_freq(self._home_trigger_height)
            # second freq to pass through; toolhead acceleration
            # must be smooth after this point
            trigger_freq = self.eddy.height_to_freq(self.eddy.params.tap_trigger_safe_start_height)

        trigger_completion = self._dispatch.start(print_time)

        if self.tap_config is not None:
            for i, section in enumerate(self.tap_config.sos):
                self._sensor.set_sos_section(i, section)
            tap_threshold = self.tap_config.threshold
        else:
            tap_threshold = None

        self.eddy._log_debug(
            f"EDDYng home_start {'tap' if tap_threshold is not None else 'home'}: {print_time:.3f} freq: {trigger_freq:.2f} safe-start: {safe_freq:.2f} @ {safe_time:.3f}"
        )
        # setup homing -- will start scanning and trigger when we hit
        # trigger_freq
        self._sensor.setup_home(
            self._dispatch.get_oid(),
            mcu.MCU_trsync.REASON_ENDSTOP_HIT,
            self.REASON_BASE,
            trigger_freq,
            safe_freq,
            safe_time,
            tap_threshold=tap_threshold,
            max_errors=self.eddy.params.max_errors,
        )

        return trigger_completion

    def home_wait(self, home_end_time):
        self.eddy._log_debug(f"home_wait until {home_end_time:.3f}")
        # logging.info(f"EDDYng home_wait {home_end_time} cur {curtime} ept {est_print_time} ehe {est_he_time}")
        self._dispatch.wait_end(home_end_time)

        # make sure homing is stopped, and grab the trigger_time from the mcu
        home_result = self._sensor.finish_home()
        trigger_time = home_result.trigger_time
        tap_start_time = home_result.tap_start_time
        error = self._sensor.data_error_to_str(home_result.error) if home_result.error != 0 else ""

        is_tap = self.tap_config is not None

        self._sampler.memo("trigger_time", trigger_time)
        if is_tap:
            self._sampler.memo("tap_start_time", tap_start_time)
            self._sampler.memo("tap_threshold", self.tap_config.threshold)

        self.eddy._log_debug(
            f"trigger_time {trigger_time} (mcu: {self._mcu.print_time_to_clock(trigger_time)}) tap time: {tap_start_time}-{trigger_time} {error}"
        )

        # nb: _dispatch.stop() will treat anything >= REASON_COMMS_TIMEOUT as an error,
        # and will only return those results. Fine for us since we only have one trsync,
        # but annoying in general.
        res = self._dispatch.stop()

        # clean these up, and only update them if successful
        self.last_trigger_time = 0.0
        self.last_tap_start_time = 0.0

        # always reset this; taps are one-shot usages of the endstop wrapper
        self.tap_config = None

        # if we're doing a tap, we wait for samples for the end as well so that we can get
        # beter data for analysis
        self._sampler.wait_for_sample_at_time(trigger_time)

        # success?
        if res == mcu.MCU_trsync.REASON_ENDSTOP_HIT:
            self.last_trigger_time = trigger_time
            self.last_tap_start_time = tap_start_time
            if is_tap:
                return self.eddy.tap_time(tap_start_time, trigger_time)
            return trigger_time

        # various errors
        if res == mcu.MCU_trsync.REASON_COMMS_TIMEOUT:
            raise self._printer.command_error("Communication timeout during homing")
        if res == self.REASON_ERROR_SENSOR:
            raise self._printer.command_error(f"Sensor error ({error})")
        if res == self.REASON_ERROR_PROBE_TOO_LOW:
            raise self._printer.command_error("Probe too low at start of homing, did not clear safe height.")
        if res == self.REASON_ERROR_TOO_EARLY:
            raise self._printer.command_error("Probe cleared safe height too early.")
        if res == mcu.MCU_trsync.REASON_PAST_END_TIME:
            raise self._printer.command_error(
                "Probe completed movement before triggering. If this is a tap, try lowering TARGET_Z or adjusting the THRESHOLD."
            )

        raise self._printer.command_error(f"Unknown homing error: {res}")

    def query_endstop(self, print_time):
        return False

