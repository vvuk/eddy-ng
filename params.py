# EDDY-ng
#
# Copyright (C) 2025  Vladimir Vukicevic <vladimir@pobox.com>
#
# Configuration parameters
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .compat import ConfigWrapper


@dataclass
class ProbeEddyParams:
    # The speed at which to perform normal homing operations
    probe_speed: float = 5.0
    # The speed at which to lift the toolhead during probing operations
    lift_speed: float = 10.0
    # The speed at which to move in the xy plane (typically only for calibration)
    move_speed: float = 50.0
    # The height at which the virtual endstop should trigger. A value
    # between 1.0 and 3.0 is recommended, with 2.0 or 2.5 being good
    # choices.
    home_trigger_height: float = 2.0
    # The amount higher the probe needs to detect the toolhead is at  in order to
    # allow homing to begin. For example, if the trigger height is 2.0, and the
    # start offset is 1.5, then homing will abort if the sensor detects the
    # toolhead is below 3.5mm off the print bed.
    home_trigger_safe_start_offset: float = 1.0
    # The amount of time that must elapse from the start of probing until the
    # safe start position is crossed. This is to make sure there are some values
    # that are above the safe position before it's crossed, to ensure that homing
    # doesn't begin with the toolhead too low.
    home_trigger_safe_time_offset: float = 0.100
    # The maximum z value to calibrate from. 15.0 is fine as a default, calibrating
    # at higher values is not needed. Calibration will start with the first
    # valid height.
    calibration_z_max: float = 15.0
    # The "drive current" for the LDC1612 sensor. This value is typically
    # sensor specific and depends on the coil design and the operating distance.
    # A good starting value for BTT Eddy is 15. A good value can be obtained
    # by placing the toolhead ~10mm above the bed and running LDC_NG_CALIBRATE_
    # DRIVE_CURRENT.
    reg_drive_current: int = 0
    # The drive current to use for tap operations. If not set, the `reg_drive_current`
    # value will be used. Tapping involves reading values much closer to the print
    # bed than basic homing, and may require a different, typically higher,
    # drive current. For example, BTT Eddy performs best with this value at 16.
    # Note that the sensor needs to be calibrated for both drive currents separately.
    # Pass the DRIVE_CURRENT argument to EDDY_NG_CALIBRATE.
    tap_drive_current: int = 0
    # The Z position at which to start a tap-home operation. This height may
    # need to be fine-tuned to ensure that the sensor can provide readings across the
    # entire tap range (i.e. from this value down to tap_target_z), which in turn
    # will depend on the tap_drive_current. When the tap_drive_current is
    # increased, the sensor may not be able to read values at higher heights.
    # For example, BTT Eddy typically cannot work with heights above 3.5mm with
    # a drive current of 16.
    #
    # Note that all of these values are in terms of offsets from the nozzle
    # to the toolhead. The actual sensor coil is mounted higher -- but must be placed
    # between 2.5 and 3mm above the nozzle, ideally around 2.75mm. If there are
    # amplitude errors, try raising or lowering the sensor coil slightly.
    tap_start_z: float = 3.0
    # The target Z position for a tap operation. This is the lowest position that
    # the toolhead may travel to in case of a failed tap. Do not set this very low,
    # as it will cause your toolhead to try to push through your build plate in
    # the case of a failed tap. A value like -0.250 is no worse than moving the
    # nozzle down one or two notches too far when doing manual Z adjustment.
    tap_target_z: float = -0.250
    # The threshold at which to detect a tap: how far (in Hz) the bandpass
    # filtered frequency must drop from its most recent peak. You can
    # experiment to arrive at this value. Typically, a lower value will make
    # tap detection more sensitive, but might lead to false positives (too
    # early detections). A higher value may cause the detection to wait too
    # long or miss a tap entirely. You can pass a THRESHOLD parameter to the
    # TAP command to experiment to find a good value.
    #
    # You may also need to use different thresholds for different build plates.
    tap_threshold: float = 250.0
    # The speed at which a tap operation should be performed at. This shouldn't
    # be much slower than 3.0, but you can experiment with lower or higher values.
    # Don't go too high though, because Klipper needs some small amount of time
    # to react to a tap trigger, and the toolhead will still be moving at this
    # speed even past the tap point. So, consider any speed you'd feel comfortable
    # triggering a toolhead move to tap_target_z at.
    tap_speed: float = 3.0
    # A static additional amount to add to the computed tap Z offset. Use this if
    # the computed tap is a bit too high or too low for your taste. Positive
    # values will raise the toolhead, negative values will lower it.
    tap_adjust_z: float = 0.0
    # The number of times to do a tap, averaging the results.
    tap_samples: int = 3
    # The maximum number of tap samples.
    tap_max_samples: int = 5
    # The maximum standard deviation for any 3 samples to be considered valid.
    tap_samples_stddev: float = 0.020
    # Use the median value instead of the mean
    tap_use_median: bool = False
    # Where in the time range of tap detection start to the time the threshold
    # is crossed should the tap be placed. 0.0 places it at the earliest start
    # of tap detection; 1.0 places it at the point where the threshold is hit.
    # A value between 0.2-0.5 generally results in more consistent tap position detection,
    # but you may want to adjust this for your configuration. This is a number
    # in the range of 0.0 to 1.0.
    tap_time_position: float = 0.3

    # When probing multiple points (not rapid scan), how long to sample for at each probe point,
    # after a scan_sample_time_delay delay. The total dwell time at each probe point is
    # scan_sample_time + scan_sample_time_delay.
    scan_sample_time: float = 0.100
    # When probing multiple points (not rapid scan), how long to delay at each probe point
    # before the scan_sample_time kicks in.
    scan_sample_time_delay: float = 0.050
    # configuration for butterworth filter
    tap_butter_lowcut: float = 5.0
    tap_butter_highcut: float = 25.0
    tap_butter_order: int = 2
    # Probe position relative to toolhead
    x_offset: float = 0.0
    y_offset: float = 0.0
    # remove some safety checks, largely for testing/development
    allow_unsafe: bool = False
    # whether to write the tap plot for the last tap
    write_tap_plot: bool = False
    # whether to write the tap plot for every tap
    write_every_tap_plot: bool = False
    # maximum number of errors to allow in a row on the sensor
    max_errors: int = 0
    # whether to print lots of verbose debug info to the log
    debug: bool = False
    bigfoot: bool = False
    primary: bool = True
    bigfoot_scan_position: list[float] | None = None

    tap_trigger_safe_start_height: float = 1.5

    _warning_msgs: List[str] = field(default_factory=list)

    def is_default_butter_config(self):
        return self.tap_butter_lowcut == 5.0 and self.tap_butter_highcut == 25.0 and self.tap_butter_order == 2

    def load_from_config(self, config: ConfigWrapper):
        self.probe_speed = config.getfloat("probe_speed", self.probe_speed, above=0.0)
        self.lift_speed = config.getfloat("lift_speed", self.lift_speed, above=0.0)
        self.move_speed = config.getfloat("move_speed", self.move_speed, above=0.0)
        self.reg_drive_current = config.getint("reg_drive_current", 0, minval=0, maxval=31)

        self.allow_unsafe = config.getboolean("allow_unsafe", self.allow_unsafe)
        self.debug = config.getboolean("debug", self.debug)
        self.max_errors = config.getint("max_errors", self.max_errors)
        self.primary = config.getboolean("primary", default=self.primary)

        self.bigfoot = config.getboolean("bigfoot", self.bigfoot)
        if self.bigfoot:
            self.bigfoot_scan_position = config.getfloatlist("bigfoot_scan_position", count=3, default=None)
            return

        self.home_trigger_height = config.getfloat("home_trigger_height", self.home_trigger_height, minval=1.0)
        self.home_trigger_safe_start_offset = config.getfloat(
            "home_trigger_safe_start_offset",
            self.home_trigger_safe_start_offset,
            minval=0.5,
        )
        self.calibration_z_max = config.getfloat("calibration_z_max", self.calibration_z_max, above=0.0)

        self.tap_drive_current = config.getint("tap_drive_current", 0, minval=0, maxval=31)
        self.tap_start_z = config.getfloat("tap_start_z", self.tap_start_z, above=0.0)
        self.tap_target_z = config.getfloat("tap_target_z", self.tap_target_z)
        self.tap_speed = config.getfloat("tap_speed", self.tap_speed, above=0.0)
        self.tap_adjust_z = config.getfloat("tap_adjust_z", self.tap_adjust_z)

        # butter is the only tap mode; still accept it being named explicitly
        if config.get("tap_mode", "butter").lower() != "butter":
            raise config.error("ProbeEddy: only tap_mode 'butter' is supported; remove tap_mode (and tap_threshold) to use it")
        self.tap_threshold = config.getfloat("tap_threshold", 250.0)

        self.scan_sample_time = config.getfloat("scan_sample_time", self.scan_sample_time, above=0.0)
        self.scan_sample_time_delay = config.getfloat("scan_sample_time_delay", self.scan_sample_time_delay, minval=0.0)

        # for 'butter'
        self.tap_butter_lowcut = config.getfloat("tap_butter_lowcut", self.tap_butter_lowcut, above=0.0)
        self.tap_butter_highcut = config.getfloat(
            "tap_butter_highcut",
            self.tap_butter_highcut,
            above=self.tap_butter_lowcut,
        )
        # each order is one filter section, and the firmware has room for MAX_SOS_SECTIONS
        self.tap_butter_order = config.getint("tap_butter_order", self.tap_butter_order, minval=1, maxval=4)

        self.tap_samples = config.getint("tap_samples", self.tap_samples, minval=1)
        self.tap_max_samples = config.getint("tap_max_samples", self.tap_max_samples, minval=self.tap_samples)
        self.tap_samples_stddev = config.getfloat("tap_samples_stddev", self.tap_samples_stddev, above=0.0)
        self.tap_use_median = config.getboolean("tap_use_median", self.tap_use_median)
        self.tap_trigger_safe_start_height = config.getfloat(
            "tap_trigger_safe_start_height",
            -1.0,
            above=0.0,
        )
        self.tap_time_position = config.getfloat("tap_time_position", self.tap_time_position, minval=0.0, maxval=1.0)

        if self.tap_trigger_safe_start_height == -1.0:  # sentinel
            self.tap_trigger_safe_start_height = self.home_trigger_height / 2.0

        self.write_tap_plot = config.getboolean("write_tap_plot", self.write_tap_plot)
        self.write_every_tap_plot = config.getboolean("write_every_tap_plot", self.write_every_tap_plot)

        self.x_offset = config.getfloat("x_offset", self.x_offset)
        self.y_offset = config.getfloat("y_offset", self.y_offset)

        self.validate(config)

    def validate(self, config: ConfigWrapper = None):
        printer = config.get_printer()
        req_cal_z_max = self.home_trigger_safe_start_offset + self.home_trigger_height + 1.0
        if self.calibration_z_max < req_cal_z_max:
            raise printer.config_error(
                f"calibration_z_max must be at least home_trigger_safe_start_offset+home_trigger_height+1.0 ({self.home_trigger_safe_start_offset:.3f}+{self.home_trigger_height:.3f}+1.0={req_cal_z_max:.3f})"
            )
        if self.x_offset == 0.0 and self.y_offset == 0.0 and not self.allow_unsafe:
            raise printer.config_error("ProbeEddy: x_offset and y_offset are both 0.0; is the sensor really mounted at the nozzle?")

        if self.home_trigger_height <= self.tap_trigger_safe_start_height:
            raise printer.config_error("ProbeEddy: home_trigger_height must be greater than tap_trigger_safe_start_height")

