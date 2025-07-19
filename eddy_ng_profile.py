# EDDY-ng
#
# Copyright (C) 2025  Vladimir Vukicevic <vladimir@pobox.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

import os
from copy import deepcopy
import logging
import math
import bisect
import re
import traceback
import pickle
import base64
import time
import numpy as np
import numpy.polynomial as npp
from itertools import combinations
from functools import cmp_to_key

from dataclasses import dataclass, field
from typing import (
    Dict,
    List,
    Optional,
    Tuple,
    ClassVar,
    final,
    TYPE_CHECKING
)

try:
    from klippy import mcu, pins, chelper
    from klippy.printer import Printer
    from klippy.configfile import ConfigWrapper
    from klippy.configfile import error as ConfigError
    from klippy.gcode import GCodeCommand, CommandError
    from klippy.toolhead import ToolHead
    from klippy.extras import probe, manual_probe, bed_mesh
    from klippy.extras.homing import HomingMove

    IS_KALICO = True
except ImportError:
    import mcu
    import pins
    import chelper
    from klippy import Printer
    from configfile import ConfigWrapper
    from configfile import error as ConfigError
    from gcode import GCodeCommand, CommandError
    from toolhead import ToolHead
    from . import probe, manual_probe, bed_mesh
    from .homing import HomingMove

    IS_KALICO = False

try:
    import scipy
except ImportError:
    scipy = None

if TYPE_CHECKING:
    from .probe_eddy_ng import ProbeEddyFrequencyMap

@dataclass
class ProbeEddyNgProfile:
    # The profile name
    name: str = "default"
    # The speed at which to perform normal homing operations
    probe_speed: float = 5.0
    # The speed at which to lift the toolhead during probing operations
    lift_speed: float = 10.0
    # The speed at which to move in the xy plane (typically only for calibration)
    move_speed: float = 50.0
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
    # the tap mode to use. 'wma' is a derivative of weighted moving average,
    # 'butter' is a butterworth filter
    tap_mode: str = "butter"
    # The threshold at which to detect a tap. This value is raw sensor value
    # specific. A good value can be obtained by running [....] and examining
    # the graph. See [calibration docs coming soon].
    #
    # The meaning of this depends on tap_mode, and the value will be different
    # if a different tap_mode is used.  You can experiment to arrive at this
    # value. Typically, a lower value will make tap detection more sensitive,
    # but might lead to false positives (too early detections). A higher value
    # may cause the detection to wait too long or miss a tap entirely.
    # You can pass a THRESHOLD parameter to the TAP command to experiment to
    # find a good value.
    #
    # You may also need to use different thresholds for different build plates.
    # Note that the default value of this threshold depends on the tap_mode.
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
    # Where in the time range of tap detection start to the time the threshold
    # is crossed should the tap be placed. 0.0 places it at the earliest start
    # of tap detection; 1.0 places it at the point where the threshold is hit.
    # A value between 0.2-0.5 generally results in more consistent tap position detection,
    # but you may want to adjust this for your configuration. This is a number
    # in the range of 0.0 to 1.0.
    tap_time_position: float = 0.3

    # configuration for butterworth filter
    tap_butter_lowcut: float = 5.0
    tap_butter_highcut: float = 25.0
    tap_butter_order: int = 2

    # drive current to frequency map calibration
    calibratin_invalid: bool = False
    calibration_map: Dict[int, "ProbeEddyFrequencyMap"] = {}

    @staticmethod
    def str_to_floatlist(s):
        if s is None:
            return None
        try:
            return [float(v) for v in re.split(r"\s*,\s*|\s+", s)]
        except Exception as _:
            raise ConfigError(f"Can't parse '{s}' as list of floats")

    def is_default_butter_config(self):
        return self.tap_butter_lowcut == 5.0 and self.tap_butter_highcut == 25.0 and self.tap_butter_order == 2

    def load_from_config(self, config: ConfigWrapper):
        mode_choices = ["wma", "butter"]

        self.probe_speed = config.getfloat("probe_speed", self.probe_speed, above=0.0)
        self.lift_speed = config.getfloat("lift_speed", self.lift_speed, above=0.0)
        self.move_speed = config.getfloat("move_speed", self.move_speed, above=0.0)

        self.reg_drive_current = config.getint("reg_drive_current", 0, minval=0, maxval=31)
        self.tap_drive_current = config.getint("tap_drive_current", 0, minval=0, maxval=31)

        self.tap_start_z = config.getfloat("tap_start_z", self.tap_start_z, above=0.0)
        self.tap_target_z = config.getfloat("tap_target_z", self.tap_target_z)
        self.tap_speed = config.getfloat("tap_speed", self.tap_speed, above=0.0)
        self.tap_adjust_z = config.getfloat("tap_adjust_z", self.tap_adjust_z)

        self.tap_mode = config.getchoice("tap_mode", mode_choices, self.tap_mode)
        default_tap_threshold = 1000.0  # for wma
        if self.tap_mode == "butter":
            default_tap_threshold = 250.0
        self.tap_threshold = config.getfloat("tap_threshold", default_tap_threshold)

        # for 'butter'
        self.tap_butter_lowcut = config.getfloat("tap_butter_lowcut", self.tap_butter_lowcut, above=0.0)
        self.tap_butter_highcut = config.getfloat(
            "tap_butter_highcut",
            self.tap_butter_highcut,
            above=self.tap_butter_lowcut,
        )
        self.tap_butter_order = config.getint("tap_butter_order", self.tap_butter_order, minval=1)

        self.tap_samples = config.getint("tap_samples", self.tap_samples, minval=1)
        self.tap_max_samples = config.getint("tap_max_samples", self.tap_max_samples, minval=self.tap_samples)
        self.tap_samples_stddev = config.getfloat("tap_samples_stddev", self.tap_samples_stddev, above=0.0)
        self.tap_time_position = config.getfloat("tap_time_position", self.tap_time_position, minval=0.0, maxval=1.0)

        self.load_calibration_map(config)
        self.validate(config)

    def load_calibration_map(self, config: ConfigWrapper):
        version = config.getint("calibration_version", default=-1)
        calibration_bad = False
        if version == -1:
            if config.get("calibrated_drive_currents", None) is not None:
                calibration_bad = True
        elif version != ProbeEddyFrequencyMap.calibration_version:
            calibration_bad = True

        calibrated_drive_currents = config.getintlist("calibrated_drive_currents", [])

        if calibration_bad:
            for dc in calibrated_drive_currents:
                # read so that there are no warnings about unknown fields
                _ = config.get(f"calibration_{dc}")
            self.calibration_invalid = True
            #self._warning_msgs.append("EDDYng calibration: calibration data invalid, please recalibrate")
            return

        for dc in calibrated_drive_currents:
            fmap = ProbeEddyFrequencyMap()
            if fmap.load_from_config(config, dc):
                self.calibration_map[dc] = fmap

    def validate(self, config: ConfigWrapper = None):
        if self.tap_mode == "butter" and not self.is_default_butter_config() and not scipy:
            raise ConfigError(
                "ProbeEddy: butter mode with custom filter parameters requires scipy, which is not available; please install scipy, use the defaults, or use wma mode"
            )

class EddyNgProfile:
    def __init__(self, config: ConfigWrapper):
        self.config = config
        self.full_name = config.get_name()
        self.name = self.full_name.split()[-1]

        if self.name == "default":
            raise ConfigError(
                "eddy-ng: the profile name 'default' is reserved and cannot be used in eddy_ng_profile; specify default values in the probe_eddy_ng section"
            )
        self.profile = ProbeEddyNgProfile()
        self.profile.load_from_config(config)

    def save(self):
        pass

def load_config_prefix(config: ConfigWrapper):
    return EddyNgProfile(config)
