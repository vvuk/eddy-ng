# EDDY-ng
#
# Copyright (C) 2025  Vladimir Vukicevic <vladimir@pobox.com>
#
# Based on original probe_eddy_current code by:
# Copyright (C) 2020-2024  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

import os
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
    Any,
    Dict,
    List,
    Optional,
    Tuple,
    ClassVar,
    final,
)

try:
    from klippy import mcu, pins, chelper
    from klippy.printer import Printer
    from klippy.configfile import ConfigWrapper
    from klippy.configfile import error as configerror
    from klippy.gcode import GCodeCommand
    from klippy.toolhead import ToolHead
    from klippy.extras import probe, manual_probe, bed_mesh
    from klippy.extras.homing import HomingMove

    IS_KALICO = True
    HAS_PROBE_RESULT_TYPE = False
except ImportError:
    import mcu
    import pins
    import chelper
    from klippy import Printer
    from configfile import ConfigWrapper
    from configfile import error as configerror
    from gcode import GCodeCommand
    from toolhead import ToolHead
    from . import probe, manual_probe, bed_mesh
    from .homing import HomingMove

    IS_KALICO = False
    HAS_PROBE_RESULT_TYPE = hasattr(manual_probe, "ProbeResult")

from . import ldc1612_ng

try:
    import plotly  # noqa
except ImportError:
    plotly = None

try:
    import scipy  # noqa
except ImportError:
    scipy = None

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
    # number of points to save for calibration
    calibration_points: int = 150
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
    debug: bool = True

    tap_trigger_safe_start_height: float = 1.5
    sensor_temp_sensor: Optional[str] = None

    _warning_msgs: List[str] = field(default_factory=list)

    @staticmethod
    def str_to_floatlist(s):
        if s is None:
            return None
        try:
            return [float(v) for v in re.split(r"\s*,\s*|\s+", s)]
        except:
            raise configerror(f"Can't parse '{s}' as list of floats")

    def is_default_butter_config(self):
        return self.tap_butter_lowcut == 5.0 and self.tap_butter_highcut == 25.0 and self.tap_butter_order == 2

    def load_from_config(self, config: ConfigWrapper):
        mode_choices = ["wma", "butter"]

        self.probe_speed = config.getfloat("probe_speed", self.probe_speed, above=0.0)
        self.lift_speed = config.getfloat("lift_speed", self.lift_speed, above=0.0)
        self.move_speed = config.getfloat("move_speed", self.move_speed, above=0.0)
        self.home_trigger_height = config.getfloat("home_trigger_height", self.home_trigger_height, minval=1.0)
        self.home_trigger_safe_start_offset = config.getfloat(
            "home_trigger_safe_start_offset",
            self.home_trigger_safe_start_offset,
            minval=0.5,
        )
        self.calibration_z_max = config.getfloat("calibration_z_max", self.calibration_z_max, above=0.0)

        self.reg_drive_current = config.getint("reg_drive_current", 0, minval=0, maxval=31)
        self.tap_drive_current = config.getint("tap_drive_current", 0, minval=0, maxval=31)

        self.tap_start_z = config.getfloat("tap_start_z", self.tap_start_z, above=0.0)
        self.tap_target_z = config.getfloat("tap_target_z", self.tap_target_z)
        self.tap_speed = config.getfloat("tap_speed", self.tap_speed, above=0.0)
        self.tap_adjust_z = config.getfloat("tap_adjust_z", self.tap_adjust_z)
        self.calibration_points = config.getint("calibration_points", self.calibration_points)

        self.tap_mode = config.getchoice("tap_mode", mode_choices, self.tap_mode)
        default_tap_threshold = 1000.0  # for wma
        if self.tap_mode == "butter":
            default_tap_threshold = 250.0
        self.tap_threshold = config.getfloat("tap_threshold", default_tap_threshold)

        self.scan_sample_time = config.getfloat("scan_sample_time", self.scan_sample_time, above=0.0)
        self.scan_sample_time_delay = config.getfloat("scan_sample_time_delay", self.scan_sample_time_delay, minval=0.0)

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
        self.tap_use_median = config.getboolean("tap_use_median", self.tap_use_median)
        self.tap_trigger_safe_start_height = config.getfloat(
            "tap_trigger_safe_start_height",
            -1.0,
            above=0.0,
        )
        self.tap_time_position = config.getfloat("tap_time_position", self.tap_time_position, minval=0.0, maxval=1.0)

        if self.tap_trigger_safe_start_height == -1.0:  # sentinel
            self.tap_trigger_safe_start_height = self.home_trigger_height / 2.0

        self.allow_unsafe = config.getboolean("allow_unsafe", self.allow_unsafe)
        self.write_tap_plot = config.getboolean("write_tap_plot", self.write_tap_plot)
        self.write_every_tap_plot = config.getboolean("write_every_tap_plot", self.write_every_tap_plot)
        self.debug = config.getboolean("debug", self.debug)

        self.max_errors = config.getint("max_errors", self.max_errors)

        self.x_offset = config.getfloat("x_offset", self.x_offset)
        self.y_offset = config.getfloat("y_offset", self.y_offset)
        self.sensor_temp_sensor = config.get("sensor_temp_sensor", self.sensor_temp_sensor)

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

        need_scipy = False
        if self.tap_mode == "butter" and not self.is_default_butter_config():
            need_scipy = True

        if need_scipy and not scipy:
            raise printer.config_error(
                "ProbeEddy: butter mode with custom filter parameters requires scipy, which is not available; please install scipy, use the defaults, or use wma mode"
            )


@dataclass
class ProbeEddyProbeResult:
    samples: List[float]
    mean: float = 0.0
    median: float = 0.0
    min_value: float = 0.0
    max_value: float = 0.0
    tstart: float = 0.0
    tend: float = 0.0
    errors: int = 0

    USE_MEAN_FOR_VALUE: ClassVar[bool] = False

    @property
    def valid(self):
        return len(self.samples) > 0

    @property
    def value(self):
        return self.mean if self.USE_MEAN_FOR_VALUE else self.median

    @property
    def stddev(self):
        stddev_sum = np.sum([(s - self.value) ** 2.0 for s in self.samples])
        return float((stddev_sum / len(self.samples)) ** 0.5)

    @classmethod
    def make(cls, times: List[float], heights: List[float], errors: int = 0) -> ProbeEddyProbeResult:
        h = np.array(heights)
        return ProbeEddyProbeResult(
            samples=h.tolist(),
            mean=float(np.mean(h)),
            median=float(np.median(h)),
            min_value=float(np.min(h)),
            max_value=float(np.max(h)),
            tstart=float(times[0]),
            tend=float(times[-1]),
            errors=errors
        )

    def __format__(self, spec):
        if spec == "v":
            return f"{self.value:.3f}"
        if self.USE_MEAN_FOR_VALUE:
            value = f"{self.mean:.3f}"
            extra = f"med={self.median:.3f}"
        else:
            value = f"{self.median:.3f}"
            extra = f"avg={self.mean:.3f}"

        return f"{value} ({extra}, {self.min_value:.3f} to {self.max_value:.3f}, [{self.stddev:.3f}])"


@final
class ProbeEddy:
    def __init__(self, config: ConfigWrapper):
        logging.info("Hello from ProbeEddyNG")

        self._printer: Printer = config.get_printer()
        self._reactor = self._printer.get_reactor()
        self._gcode = self._printer.lookup_object("gcode")
        self._full_name = config.get_name()
        self._name = self._full_name.split()[-1]

        sensors = {
            "ldc1612": ldc1612_ng.LDC1612_ng,
            "btt_eddy": ldc1612_ng.LDC1612_ng,
            "cartographer": ldc1612_ng.LDC1612_ng,
            "mellow_fly": ldc1612_ng.LDC1612_ng,
            "ldc1612_internal_clk": ldc1612_ng.LDC1612_ng,
        }
        sensor_type = config.getchoice("sensor_type", {s: s for s in sensors})

        self._sensor_type = sensor_type
        self._sensor = sensors[sensor_type](config)
        self._mcu = self._sensor.get_mcu()
        self._toolhead: ToolHead = None  # filled in _handle_connect
        self._trapq = None

        self.params = ProbeEddyParams()
        self.params.load_from_config(config)

        # figure out if either of these comes from the autosave section
        # so we can sort out what we want to write out later on
        asfc = self._printer.lookup_object("configfile").autosave.fileconfig
        self._saved_reg_drive_current = asfc.getint(self._full_name, "reg_drive_current", fallback=None)
        self._saved_tap_drive_current = asfc.getint(self._full_name, "tap_drive_current", fallback=None)

        # in case there's legacy drive currents
        old_saved_reg_drive_current = asfc.getint(self._full_name, "saved_reg_drive_current", fallback=0)
        old_saved_tap_drive_current = asfc.getint(self._full_name, "saved_tap_drive_current", fallback=0)

        self._reg_drive_current = self.params.reg_drive_current or self._saved_reg_drive_current or old_saved_reg_drive_current or self._sensor._drive_current
        self._tap_drive_current = self.params.tap_drive_current or self._saved_tap_drive_current or old_saved_tap_drive_current or self._reg_drive_current

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
        elif version != ProbeEddyFrequencyMap.calibration_version:
            calibration_bad = True

        calibrated_drive_currents = config.getintlist("calibrated_drive_currents", [])

        self._dc_to_fmap: Dict[int, ProbeEddyFrequencyMap] = {}
        self._dc_to_temp_fmaps: Dict[int, List[Tuple[float, ProbeEddyFrequencyMap]]] = {}
        self._dc_to_drift_coefs: Dict[int, Tuple[float, float, float, float]] = {}

        # 1. Load baseline and Z-drift coefficients from config
        for dc in calibrated_drive_currents:
            baseline_str = config.get(f"calibration_3d_baseline_{dc}", None)
            htof_str = config.get(f"calibration_3d_htof_{dc}", None)
            drift_str = config.get(f"calibration_3d_drift_{dc}", None)

            if baseline_str is not None and htof_str is not None:
                try:
                    b_parts = [float(v) for v in baseline_str.split(",")]
                    h_parts = [float(v) for v in htof_str.split(",")]
                    if len(b_parts) == 9 and len(h_parts) == 10:
                        p_inf, c0, c1, c2, c3, hmin, hmax, fmin, fmax = b_parts

                        fmap = ProbeEddyFrequencyMap(self)
                        fmap.drive_current = dc
                        fmap.height_range = [hmin, hmax]
                        fmap.freq_range = [fmin, fmax]

                        # Reconstruct ftoh (rational fit)
                        fmap._ftoh = ProbeEddyRationalFit(p_inf, [c0, c1, c2, c3], [1.0/fmax, 1.0/fmin])
                        # Reconstruct htof (polynomial fit)
                        fmap._htof = npp.Polynomial(h_parts, domain=[hmin, hmax])

                        self._dc_to_fmap[dc] = fmap
                        logging.info(f"EDDYng: Loaded reconstructed 3D baseline mapping for drive current {dc}")
                except Exception as e:
                    logging.exception(f"EDDYng: Failed to load 3D baseline for drive current {dc}")
                    self.params._warning_msgs.append(f"EDDYng 3D baseline: load failed for DC {dc} ({e})")

            if drift_str is not None:
                try:
                    d_parts = [float(v) for v in drift_str.split(",")]
                    if len(d_parts) == 4:
                        ref_temp, drift_c3, drift_c2, drift_c1 = d_parts
                        self._dc_to_drift_coefs[dc] = (ref_temp, drift_c3, drift_c2, drift_c1)
                        logging.info(f"EDDYng: Loaded 3D Z-drift coefficients for drive current {dc}")
                except Exception as e:
                    logging.exception(f"EDDYng: Failed to load 3D drift for drive current {dc}")
                    self.params._warning_msgs.append(f"EDDYng 3D Z-drift: load failed for DC {dc} ({e})")

        # Fall back to 2D calibration loading if 3D is not populated
        if not self._dc_to_fmap and not self._dc_to_drift_coefs:
            if not calibration_bad:
                loaded_2d = False
                for dc in calibrated_drive_currents:
                    fmap = ProbeEddyFrequencyMap(self)
                    if fmap.load_from_config(config, dc):
                        self._dc_to_fmap[dc] = fmap
                        loaded_2d = True
                if loaded_2d:
                    self.params._warning_msgs.append(
                        "EDDYng: Using legacy 2D Z-calibration. 3D Z-drift temperature "
                        "calibration is missing. We recommend running PROBE_EDDY_NG_SETUP."
                    )
            else:
                for dc in calibrated_drive_currents:
                    # read so that there are no warnings about unknown fields
                    _ = config.get(f"calibration_{dc}")
                self.params._warning_msgs.append("EDDYng calibration: calibration data invalid, please recalibrate")

        # Our virtual endstop wrapper -- used for homing.
        self._endstop_wrapper = ProbeEddyEndstopWrapper(self)

        # There can only be one active sampler at a time
        self._sampler: ProbeEddySampler = None
        self._last_sampler: ProbeEddySampler = None
        # True while EDDYNG_START_STREAM_EXPERIMENTAL is holding the sampler on purpose
        self._streaming = False
        self.save_samples_path = None

        # The last tap Z value, in absolute axis terms. Used for status.
        self._last_tap_z = 0.0
        # The last gcode offset applied after tap, either the tap
        # value, or 0.0 if HOME_Z=1
        self._last_tap_gcode_adjustment = 0.0

        # This class emulates "PrinterProbe". We use some existing helpers to implement
        # functionality like start_session
        self._printer.add_object("probe", self)

        self._bed_mesh_helper = BedMeshScanHelper(self, config)

        # TODO: get rid of this
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

        # define our own commands
        self._dummy_gcode_cmd: GCodeCommand = self._gcode.create_gcode_command("", "", {})
        self.define_commands(self._gcode)

        self._printer.register_event_handler("gcode:command_error", self._handle_command_error)
        self._printer.register_event_handler("klippy:connect", self._handle_connect)

        # patch bed_mesh because Klipper
        if not IS_KALICO:
            bed_mesh.ProbeManager.start_probe = bed_mesh_ProbeManager_start_probe_override

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

    def _log_calibration_sweep_data(self, temp: float, dc: int, mapping: ProbeEddyFrequencyMap):
        if mapping is None:
            return
        ftoh_coefs = mapping._ftoh.coef.tolist() if mapping._ftoh else None
        htof_coefs = mapping._htof.coef.tolist() if mapping._htof else None
        self._log_msg(
            f"3D Sweep Log: temp={temp:.2f}C, dc={dc}, "
            f"ftoh_coefs={ftoh_coefs}, htof_coefs={htof_coefs}"
        )
        grid_data = []
        for h in [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 6.0, 7.0]:
            if mapping.height_range[0] <= h <= mapping.height_range[1]:
                try:
                    f = mapping.height_to_freq(h)
                    grid_data.append(f"{h:.1f}mm:{f:.1f}Hz")
                except Exception:
                    pass
        self._log_msg(f"3D Sweep Grid: temp={temp:.2f}C, dc={dc} | " + ", ".join(grid_data))

    def define_commands(self, gcode):
        gcode.register_command("PROBE_EDDY_NG_STATUS", self.cmd_STATUS, self.cmd_STATUS_help)
        gcode.register_command(
            "PROBE_EDDY_NG_CALIBRATE",
            self.cmd_CALIBRATE,
            self.cmd_CALIBRATE_help,
        )
        gcode.register_command(
            "PROBE_EDDY_NG_CALIBRATION_STATUS",
            self.cmd_CALIBRATION_STATUS,
            self.cmd_CALIBRATION_STATUS_help,
        )
        gcode.register_command(
            "PROBE_EDDY_NG_SETUP",
            self.cmd_SETUP,
            self.cmd_SETUP_help,
        )
        gcode.register_command(
            "PROBE_EDDY_NG_CLEAR_CALIBRATION",
            self.cmd_CLEAR_CALIBRATION,
            self.cmd_CLEAR_CALIBRATION_help,
        )
        gcode.register_command("PROBE_EDDY_NG_PROBE", self.cmd_PROBE, self.cmd_PROBE_help)
        gcode.register_command(
            "PROBE_EDDY_NG_PROBE_STATIC",
            self.cmd_PROBE_STATIC,
            self.cmd_PROBE_STATIC_help,
        )
        gcode.register_command(
            "PROBE_EDDY_NG_PROBE_ACCURACY",
            self.cmd_PROBE_ACCURACY,
            self.cmd_PROBE_ACCURACY_help,
        )
        gcode.register_command("PROBE_EDDY_NG_TAP", self.cmd_TAP, self.cmd_TAP_help)
        gcode.register_command(
            "PROBE_EDDY_NG_SET_TAP_OFFSET",
            self.cmd_SET_TAP_OFFSET,
            "Set or clear the tap offset for the bed mesh scan and other probe operations",
        )
        gcode.register_command(
            "PROBE_EDDY_NG_SET_TAP_ADJUST_Z",
            self.cmd_SET_TAP_ADJUST_Z,
            "Set the tap adjustment value",
        )
        gcode.register_command(
            "PROBE_EDDY_NG_TEST_DRIVE_CURRENT",
            self.cmd_TEST_DRIVE_CURRENT,
            "Test a drive current.",
        )
        gcode.register_command("Z_OFFSET_APPLY_PROBE", None)
        gcode.register_command(
            "Z_OFFSET_APPLY_PROBE",
            self.cmd_Z_OFFSET_APPLY_PROBE,
            "Apply the current G-Code Z offset to tap_adjust_z",
        )

        # some handy aliases while I'm debugging things to save my fingers
        gcode.register_command(
            "PES",
            self.cmd_STATUS,
            self.cmd_STATUS_help + " (alias for PROBE_EDDY_NG_STATUS)",
        )
        gcode.register_command(
            "PEP",
            self.cmd_PROBE,
            self.cmd_PROBE_help + " (alias for PROBE_EDDY_NG_PROBE)",
        )
        gcode.register_command(
            "PEPS",
            self.cmd_PROBE_STATIC,
            self.cmd_PROBE_STATIC_help + " (alias for PROBE_EDDY_NG_PROBE_STATIC)",
        )
        gcode.register_command(
            "PETAP",
            self.cmd_TAP,
            self.cmd_TAP_help + " (alias for PROBE_EDDY_NG_TAP)",
        )

        gcode.register_command("EDDYNG_BED_MESH_EXPERIMENTAL", self.cmd_MESH, "")
        gcode.register_command("EDDYNG_START_STREAM_EXPERIMENTAL", self.cmd_START_STREAM, "")
        gcode.register_command("EDDYNG_STOP_STREAM_EXPERIMENTAL", self.cmd_STOP_STREAM, "")

    def _handle_command_error(self, gcmd=None):
        try:
            self.recover_probe_state("command error")
        except:
            logging.exception("EDDYng handle_command_error: recovery failed")

    BUILD_ID = "myfork-fix6-2026-10-03"

    def _handle_connect(self):
        logging.info(f"{self._name}: probe_eddy_ng build {self.BUILD_ID} loaded from {os.path.abspath(__file__)}")
        self._toolhead = self._printer.lookup_object("toolhead")
        self._trapq = self._toolhead.get_trapq()
        self._temp_sensor_obj = None
        if self.params.sensor_temp_sensor:
            self._temp_sensor_obj = self._printer.lookup_object(self.params.sensor_temp_sensor, None)
            if self._temp_sensor_obj is None:
                self._log_warning(f"Configured sensor_temp_sensor '{self.params.sensor_temp_sensor}' not found")
        for msg in self.params._warning_msgs:
            self._log_warning(msg)

    def _get_trapq_position(self, print_time: float) -> Tuple[Tuple[float, float, float], float]:
        ffi_main, ffi_lib = chelper.get_ffi()
        data = ffi_main.new("struct pull_move[1]")
        count = ffi_lib.trapq_extract_old(self._trapq, data, 1, 0.0, print_time)
        if not count:
            return None, None
        move = data[0]
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

    def get_default_butter_sos(self, data_rate: int) -> Optional[List[List[float]]]:
        """
        Returns the pre-calculated Second-Order Sections (SOS) matrix for the default
        bandpass Butterworth filter at standard data rates (250Hz or 500Hz).

        This acts as a fallback to avoid dependency on scipy on lightweight host systems.
        The coefficients are calculated using scipy:
            scipy.signal.butter(
                N=params.tap_butter_order (default 4),
                Wn=[params.tap_butter_lowcut (default 2), params.tap_butter_highcut (default 20)],
                btype='bandpass',
                fs=data_rate,
                output='sos'
            )
        Each section has the format [b0, b1, b2, a0, a1, a2].
        """
        if data_rate == 250:
            return [
                [ 0.046131802093312926, 0.09226360418662585, 0.046131802093312926, 1.0, -1.3297767184682712, 0.5693902189294331, ],
                [ 1.0, -2.0, 1.0, 1.0, -1.845000600983779, 0.8637525213328747, ],
            ]
        elif data_rate == 500:
            return [
                [ 0.013359200027856505, 0.02671840005571301, 0.013359200027856505, 1.0, -1.686278256753083, 0.753714473246724, ],
                [ 1.0, -2.0, 1.0, 1.0, -1.9250515947328444, 0.9299234737648037, ],
            ]
        return None

    def current_drive_current(self) -> int:
        return self._sensor.get_drive_current()

    def reset_drive_current(self, tap=False):
        dc = self._tap_drive_current if tap else self._reg_drive_current
        if dc == 0:
            raise self._printer.command_error(f"Unknown {'tap' if tap else 'homing'} drive current")
        self._sensor.set_drive_current(dc)

    def map_for_drive_current(self, dc: Optional[int] = None) -> ProbeEddyFrequencyMap:
        if dc is None:
            dc = self.current_drive_current()
        if dc not in self._dc_to_fmap:
            if dc in self._dc_to_temp_fmaps and self._dc_to_temp_fmaps[dc]:
                # Fallback to the first temp calibration map if 3D is active but 2D map is requested
                return self._dc_to_temp_fmaps[dc][0][1]
            raise self._printer.command_error(f"Drive current {dc} not calibrated")
        return self._dc_to_fmap[dc]

    def get_sensor_temp(self) -> Optional[float]:
        if getattr(self, "_temp_sensor_obj", None) is None:
            return None
        try:
            curtime = self._reactor.monotonic()
            status = self._temp_sensor_obj.get_status(curtime)
            return status.get("temperature")
        except Exception:
            return None

    # helpers to forward to the map, with optional 3D calibration support
    def height_to_freq(self, height: float, drive_current: Optional[int] = None) -> float:
        if drive_current is None:
            drive_current = self.current_drive_current()
        temp = self.get_sensor_temp()
        if temp is not None and drive_current in self._dc_to_drift_coefs:
            # Exact inverse of freq_to_height(): that one returns h_baseline(f) - delta_h(T),
            # so the frequency that reads as `height` is baseline(height + delta_h). Without
            # this the homing/tap trigger frequencies sent to the MCU ignored the drift while
            # every sampled height included it, so a warm sensor (e.g. right after a print)
            # triggered at the wrong height or failed the "safe start" checks.
            ref_temp, c3, c2, c1 = self._dc_to_drift_coefs[drive_current]
            dT = temp - ref_temp
            delta_h = c3 * (dT**3) + c2 * (dT**2) + c1 * dT
            return self.map_for_drive_current(drive_current).height_to_freq(height + delta_h)
        if temp is not None and drive_current in self._dc_to_temp_fmaps and self._dc_to_temp_fmaps[drive_current]:
            mappings = self._dc_to_temp_fmaps[drive_current]
            if len(mappings) == 1:
                return mappings[0][1].height_to_freq(height)
            temps = [m[0] for m in mappings]
            if temp <= temps[0]:
                return mappings[0][1].height_to_freq(height)
            elif temp >= temps[-1]:
                return mappings[-1][1].height_to_freq(height)
            else:
                for j in range(len(temps) - 1):
                    if temps[j] <= temp <= temps[j+1]:
                        t_low, map_low = mappings[j]
                        t_high, map_high = mappings[j+1]
                        f_low = map_low.height_to_freq(height)
                        f_high = map_high.height_to_freq(height)
                        fraction = (temp - t_low) / (t_high - t_low) if t_high != t_low else 0.0
                        return f_low + fraction * (f_high - f_low)
        return self.map_for_drive_current(drive_current).height_to_freq(height)

    def freq_to_height(self, freq: float, drive_current: Optional[int] = None) -> float:
        if drive_current is None:
            drive_current = self.current_drive_current()

        temp = self.get_sensor_temp()
        if temp is not None and drive_current in self._dc_to_drift_coefs:
            h_baseline = self.map_for_drive_current(drive_current).freq_to_height(freq)
            ref_temp, c3, c2, c1 = self._dc_to_drift_coefs[drive_current]
            dT = temp - ref_temp
            delta_h = c3*(dT**3) + c2*(dT**2) + c1*dT
            return h_baseline - delta_h

        if temp is not None and drive_current in self._dc_to_temp_fmaps and self._dc_to_temp_fmaps[drive_current]:
            mappings = self._dc_to_temp_fmaps[drive_current]
            if len(mappings) == 1:
                return mappings[0][1].freq_to_height(freq)
            temps = [m[0] for m in mappings]
            if temp <= temps[0]:
                return mappings[0][1].freq_to_height(freq)
            elif temp >= temps[-1]:
                return mappings[-1][1].freq_to_height(freq)
            else:
                for j in range(len(temps) - 1):
                    if temps[j] <= temp <= temps[j+1]:
                        t_low, map_low = mappings[j]
                        t_high, map_high = mappings[j+1]
                        h_low = map_low.freq_to_height(freq)
                        h_high = map_high.freq_to_height(freq)
                        fraction = (temp - t_low) / (t_high - t_low) if t_high != t_low else 0.0
                        return h_low + fraction * (h_high - h_low)

        return self.map_for_drive_current(drive_current).freq_to_height(freq)

    def freqs_to_heights_np(self, freqs: np.array, drive_current: Optional[int] = None) -> np.array:
        if drive_current is None:
            drive_current = self.current_drive_current()

        temp = self.get_sensor_temp()
        if temp is not None and drive_current in self._dc_to_drift_coefs:
            h_baseline = self.map_for_drive_current(drive_current).freqs_to_heights_np(freqs)
            ref_temp, c3, c2, c1 = self._dc_to_drift_coefs[drive_current]
            dT = temp - ref_temp
            delta_h = c3*(dT**3) + c2*(dT**2) + c1*dT
            return h_baseline - delta_h

        if temp is not None and drive_current in self._dc_to_temp_fmaps and self._dc_to_temp_fmaps[drive_current]:
            mappings = self._dc_to_temp_fmaps[drive_current]
            if len(mappings) == 1:
                return mappings[0][1].freqs_to_heights_np(freqs)
            temps = [m[0] for m in mappings]
            if temp <= temps[0]:
                return mappings[0][1].freqs_to_heights_np(freqs)
            elif temp >= temps[-1]:
                return mappings[-1][1].freqs_to_heights_np(freqs)
            else:
                for j in range(len(temps) - 1):
                    if temps[j] <= temp <= temps[j+1]:
                        t_low, map_low = mappings[j]
                        t_high, map_high = mappings[j+1]
                        h_low = map_low.freqs_to_heights_np(freqs)
                        h_high = map_high.freqs_to_heights_np(freqs)
                        fraction = (temp - t_low) / (t_high - t_low) if t_high != t_low else 0.0
                        return h_low + fraction * (h_high - h_low)

        return self.map_for_drive_current(drive_current).freqs_to_heights_np(freqs)

    def calibrated(self, drive_current: Optional[int] = None) -> bool:
        if drive_current is None:
            drive_current = self.current_drive_current()
        if drive_current in self._dc_to_temp_fmaps and self._dc_to_temp_fmaps[drive_current]:
            return all(m[1].calibrated() for m in self._dc_to_temp_fmaps[drive_current])
        return drive_current in self._dc_to_fmap and self._dc_to_fmap[drive_current].calibrated()

    def _print_time_now(self):
        return self._mcu.estimated_print_time(self._reactor.monotonic())

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

    def _set_toolhead_position(self, pos, homing_axes):
        # klipper changed homing_axes to be a "xyz" string instead
        # of a tuple randomly on jan10 without support for the old
        # syntax
        func = self._toolhead.set_position
        kind = type(func.__defaults__[0])
        if kind is str:
            # new
            homing_axes_str = "".join(["xyz"[axis] for axis in homing_axes])
            return self._toolhead.set_position(pos, homing_axes=homing_axes_str)
        else:
            # old
            return self._toolhead.set_position(pos, homing_axes=homing_axes)

    def _z_not_homed(self):
        kin = self._toolhead.get_kinematics()
        # klipper got rid of this
        if hasattr(kin, "note_z_not_homed"):
            kin.note_z_not_homed()
        else:
            try:
                kin.clear_homing_state("z")
            except TypeError:
                raise self._printer.command_error(
                    "clear_homing_state failed: please update Klipper, your klipper is from the brief 5 day window where this was broken"
                )

    def save_calibration_3d(self):
        configfile = self._printer.lookup_object("configfile")

        # 1. Save raw sweep data to a CSV file for the user
        if self._dc_to_temp_fmaps:
            csv_filepath = os.path.expanduser("~/printer_data/config/eddy_calibration_3d.csv")
            try:
                with open(csv_filepath, "w") as f:
                    f.write("temperature,drive_current,time,frequency,height,velocity\n")
                    for dc, mappings_list in self._dc_to_temp_fmaps.items():
                        for temp, fmap in mappings_list:
                            if hasattr(fmap, "raw_data") and fmap.raw_data is not None:
                                times, freqs, heights, vels = fmap.raw_data
                                for i in range(len(freqs)):
                                    t = times[i]
                                    freq = freqs[i]
                                    h = heights[i]
                                    v = vels[i] if vels is not None else 0.0
                                    f.write(f"{temp:.4f},{dc},{t:.6f},{freq:.2f},{h:.6f},{v:.6f}\n")
                self._log_msg(f"Raw 3D calibration sweep data saved to CSV: {csv_filepath}")
            except Exception as e:
                self._log_error(f"Failed to save raw 3D calibration sweep data to CSV: {e}")

        # 2. Save coefficients to configuration file
        dcs = self._dc_to_temp_fmaps.keys() if self._dc_to_temp_fmaps else self._dc_to_drift_coefs.keys()
        for dc in dcs:
            if self._dc_to_temp_fmaps:
                baseline_temp, baseline_map = self._dc_to_temp_fmaps[dc][0]
            else:
                baseline_map = self._dc_to_fmap[dc]

            p_inf = baseline_map._ftoh.p_inf
            c0, c1, c2, c3 = baseline_map._ftoh.coef
            hmin, hmax = baseline_map.height_range
            fmin, fmax = baseline_map.freq_range
            configfile.set(
                self._full_name,
                f"calibration_3d_baseline_{dc}",
                f"{p_inf:.12e},{c0:.12e},{c1:.12e},{c2:.12e},{c3:.12e},{hmin:.4f},{hmax:.4f},{fmin:.2f},{fmax:.2f}"
            )

            htof_coefs = baseline_map._htof.coef.tolist()
            htof_str = ",".join([f"{c:.12e}" for c in htof_coefs])
            configfile.set(
                self._full_name,
                f"calibration_3d_htof_{dc}",
                htof_str
            )

            if dc in self._dc_to_drift_coefs:
                ref_temp, drift_c3, drift_c2, drift_c1 = self._dc_to_drift_coefs[dc]
                configfile.set(
                    self._full_name,
                    f"calibration_3d_drift_{dc}",
                    f"{ref_temp:.4f},{drift_c3:.12e},{drift_c2:.12e},{drift_c1:.12e}"
                )

    def save_config(self, log_msg=True):
        configfile = self._printer.lookup_object("configfile")
        configfile.remove_section(self._full_name)

        if self._dc_to_temp_fmaps:
            configfile.set(
                self._full_name,
                "calibrated_drive_currents",
                str.join(", ", [str(dc) for dc in self._dc_to_temp_fmaps.keys()]),
            )
        else:
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

        configfile.set(self._full_name, "tap_start_z", f"{self.params.tap_start_z:.2f}")

        if self._dc_to_temp_fmaps or self._dc_to_drift_coefs:
            self.save_calibration_3d()
        else:
            for _, fmap in self._dc_to_fmap.items():
                fmap.save_calibration()

        if log_msg:
            self._log_msg("Calibration saved. Issue a SAVE_CONFIG to write the values to your config file and restart Klipper.")

    def _next_lower_calibrated_dc(self, dc: int) -> Optional[int]:
        """The highest calibrated drive current below `dc`, or None if there isn't one."""
        known = set(self._dc_to_fmap.keys()) | set(self._dc_to_temp_fmaps.keys())
        lower = [d for d in known if d < dc and self.calibrated(d)]
        return max(lower) if lower else None

    SENSOR_ERROR_MARKERS = ("Sensor error", "Watchdog", "No samples received", "Communication timeout")

    def recover_probe_state(self, reason: str = ""):
        """
        Put the probing machinery back into a clean idle state.

        Klipper only sends `homing:homing_move_end` when HomingMove.homing_move() gets past
        home_start(); an error raised earlier (or one that a caller swallowed, like a failed
        tap sample) therefore leaves a sampler registered, the endstop marked as "homing" and
        possibly the MCU still armed. Every later tap/probe/mesh then fails with
        "Already sampling!" until the firmware is restarted. This undoes all of that and is
        safe to call at any time while nothing is streaming.
        """
        if self._streaming:
            return
        if self._sampler is not None or self._endstop_wrapper.needs_recovery():
            self._log_warning(f"EDDYng: cleaning up leftover probe state{(' (' + reason + ')') if reason else ''}")
        self._endstop_wrapper.reset_after_failure()
        sampler = self._sampler
        if sampler is not None:
            try:
                sampler.finish()
            except Exception:
                logging.exception("EDDYng recover_probe_state: sampler.finish() failed")
            if self._sampler is sampler:
                # finish() refused or failed; drop it by force so we can sample again
                sampler._stopped = True
                self._sampler = None

    def start_sampler(self, *args, **kwargs) -> ProbeEddySampler:
        if self._sampler:
            # A leftover sampler from an aborted probe/tap can never be valid here
            # (G-code commands are serialized), so clean it up instead of locking up
            # every future probe until the next FIRMWARE_RESTART.
            self.recover_probe_state("start_sampler found a stale sampler")
            if self._sampler:
                raise self._printer.command_error("EDDYng: Already sampling! (EDDYNG_STOP_STREAM_EXPERIMENTAL to stop streaming)")
        self._sampler = ProbeEddySampler(self, *args, **kwargs)
        self._sampler.start()
        return self._sampler

    def sampler_is_active(self):
        return self._sampler is not None and self._sampler.active()

    # Called by samplers when they're finished
    def _sampler_finished(self, sampler: ProbeEddySampler, **kwargs):
        if self._sampler is not sampler:
            raise self._printer.command_error("EDDYng finishing sampler that's not active")

        self._last_sampler = sampler
        self._sampler = None

        if self.save_samples_path is not None:
            with open(self.save_samples_path, "w") as data_file:
                times = sampler.times
                raw_freqs = sampler.raw_freqs
                freqs = sampler.freqs
                heights = sampler.heights

                data_file.write("time,frequency,z,kin_z,kin_v,raw_f,trigger_time,tap_start_time\n")
                trigger_time = kwargs.get("trigger_time", "")
                tap_start_time = kwargs.get("tap_start_time", "")
                for i in range(len(times)):
                    past_pos, past_v = self._get_trapq_position(times[i])
                    past_k_z = past_pos[2] if past_pos is not None else ""
                    past_v = past_v if past_v is not None else ""
                    data_file.write(f"{times[i]},{freqs[i]},{heights[i] if heights else ''},{past_k_z},{past_v},{raw_freqs[i]},{trigger_time},{tap_start_time}\n")
            logging.info(f"Wrote {len(times)} samples to {self.save_samples_path}")
            self.save_samples_path = None

    def cmd_MESH(self, gcmd: GCodeCommand):
        self.recover_probe_state("before mesh")
        try:
            self._bed_mesh_helper.scan(gcmd)
        except self._printer.command_error:
            raise
        except Exception as e:
            # An unexpected Python exception escaping a gcode handler is reported by Klipper as
            # "Internal error on command" and shuts the whole printer down (needing a
            # FIRMWARE_RESTART). Log the traceback and turn it into a regular, recoverable
            # command error instead.
            logging.exception("EDDYng: bed mesh scan failed")
            raise self._printer.command_error(f"EDDYng bed mesh scan failed: {type(e).__name__}: {e}")

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
        temp = self.get_sensor_temp()
        using_3d = self.current_drive_current() in self._dc_to_temp_fmaps and self._dc_to_temp_fmaps[self.current_drive_current()]
        temp_str = f"temp={temp:.1f}C (3D cal)" if (temp is not None and using_3d) else (f"temp={temp:.1f}C (no 3D cal)" if temp is not None else "temp=N/A")
        gcmd.respond_info(
            f"Active drive currents: homing={self._reg_drive_current}, tap={self._tap_drive_current} | {temp_str}"
        )

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

    def cmd_SET_TAP_OFFSET(self, gcmd: GCodeCommand):
        value = gcmd.get_float("VALUE", None)
        adjust = gcmd.get_float("ADJUST", None)
        tap_offset = self._tap_offset
        if value is not None:
            tap_offset = value
        if adjust is not None:
            tap_offset += adjust
        self._tap_offset = tap_offset
        gcmd.respond_info(f"Set tap offset: {tap_offset:.3f}")

    def cmd_SET_TAP_ADJUST_Z(self, gcmd: GCodeCommand):
        value = gcmd.get_float("VALUE", None)
        adjust = gcmd.get_float("ADJUST", None)
        tap_adjust_z = self._tap_adjust_z
        if value is not None:
            tap_adjust_z = value
        if adjust is not None:
            tap_adjust_z += adjust
        self._tap_adjust_z = tap_adjust_z

        if self.params.tap_adjust_z != self._tap_adjust_z:
            configfile = self._printer.lookup_object("configfile")
            configfile.set(self._full_name, "tap_adjust_z", str(float(self._tap_adjust_z)))

        gcmd.respond_info(f"Set tap_adjust_z: {tap_adjust_z:.3f} (SAVE_CONFIG to make it permanent)")

    def cmd_Z_OFFSET_APPLY_PROBE(self, gcmd: GCodeCommand):
        gcode_move = self._printer.lookup_object("gcode_move")
        offset = gcode_move.get_status()["homing_origin"].z
        offset += self.params.tap_adjust_z
        offset -= self._last_tap_gcode_adjustment
        configfile = self._printer.lookup_object("configfile")
        configfile.set(self._full_name, "tap_adjust_z", f"{offset:.3f}")
        self._log_msg(
            f"{self._name}: new tap_adjust_z: {offset:.3f}\n"
            "The SAVE_CONFIG command will update the printer config file\n"
            "with the above and restart the printer."
        )

    def probe_static_height(self, duration: float = 0.100) -> ProbeEddyProbeResult:
        with self.start_sampler() as sampler:
            now = self._print_time_now()
            sampler.wait_for_sample_at_time(now + (duration + self._sensor._ldc_settle_time))
            sampler.finish()

        if sampler.height_count == 0:
            return ProbeEddyProbeResult([])

        etime = sampler.times[-1]
        stime = etime - duration

        first_idx = bisect.bisect_left(sampler.times, stime)
        if first_idx == len(sampler.times):
            raise self._printer.command_error(f"No samples in time range")

        errors = sampler.error_count
        return ProbeEddyProbeResult.make(sampler.times[first_idx:], sampler.heights[first_idx:], errors=errors)

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
                self._set_toolhead_position(th_pos, [2])
                self._log_debug(f"Homed Z to {r}")
            else:
                self._log_msg(f"Probed {r}")

        finally:
            self._sensor.set_drive_current(old_drive_current)

    cmd_SETUP_help = (
        "Setup the eddy current sensor. Specify FILAMENT=<type> (PLA, PETG, TPU, ABS, ASA) or "
        "TARGET_TEMP=<temp> to preheat the bed. Specify NOHOME=1 to suppress homing X & Y and "
        "centering before setup."
    )

    def cmd_SETUP(self, gcmd: GCodeCommand):
        filament = gcmd.get("FILAMENT", None)
        target_temp = gcmd.get_float("TARGET_TEMP", None)

        if target_temp is None and filament is None:
            raise self._printer.command_error(
                "Please specify FILAMENT (PLA, PETG, TPU, ABS, ASA) or TARGET_TEMP. "
                "Example: PROBE_EDDY_NG_SETUP FILAMENT=PLA"
            )

        if target_temp is not None:
            if target_temp < 0.0 or target_temp > 130.0:
                raise self._printer.command_error("TARGET_TEMP must be between 0 and 130")

        if target_temp is None:
            filament = filament.strip().lower()
            filament_temps = {
                "pla": 60.0,
                "petg": 80.0,
                "tpu": 50.0,
                "abs": 110.0,
                "asa": 110.0,
            }
            if filament not in filament_temps:
                raise self._printer.command_error(
                    f"Unknown filament '{filament}'. Supported: {', '.join(sorted(filament_temps.keys()))}"
                )
            target_temp = filament_temps[filament]

        self._setup_target_temp = target_temp

        if self._temp_sensor_obj is not None:
            self._log_msg(
                f"3D calibration enabled with temperature sensor '{self.params.sensor_temp_sensor}'. "
                f"Manual Z alignment will be performed at room temperature, then calibration will run "
                f"while heating up to {target_temp:.1f}C."
            )
        else:
            if target_temp > 0.0:
                self._log_msg(f"Preheating bed to target temperature {target_temp:.1f}C for calibration...")
                self._gcode.run_script_from_command(f"M190 S{target_temp:.0f}")

        nohome = gcmd.get_int("NOHOME", 0) != 0
        if not nohome:
            if self._z_homed():
                self._z_hop()
            self._gcode.run_script_from_command("G28 X Y")

            th = self._printer.lookup_object("toolhead")
            center_x = 60.0
            center_y = 60.0
            try:
                kin = th.get_kinematics()
                rails = kin.rails
                x_min, x_max = rails[0].get_range()
                y_min, y_max = rails[1].get_range()
                center_x = (x_min + x_max) / 2.0
                center_y = (y_min + y_max) / 2.0
            except Exception:
                pass
            self._log_msg(f"Moving toolhead to center: X={center_x:.1f}, Y={center_y:.1f}")
            th.manual_move([center_x, center_y, None], self.params.move_speed)
            th.wait_moves()
        else:
            if not self._xy_homed():
                raise self._printer.command_error("X and Y must be homed before setup")

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
        self._set_toolhead_position(th_pos, [2])

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

        old_drive_current = self.current_drive_current()
        cal_z_max: float = self.params.calibration_z_max
        # Cap calibration Z max to 7.0 so that the maximum height (including 3.0mm backlash lift) is exactly 10.0mm
        cal_z_max = min(7.0, cal_z_max)
        z_target = 0.0

        # We just did a ManualProbeHelper, so we're going to zero the z-axis
        # to make the following code easier, so it can assume z=0 is actually real zero.
        # The Eddy sensor calibration is done to nozzle height (not sensor or trigger height).
        th = self._printer.lookup_object("toolhead")
        th_pos = th.get_position()
        th_pos[2] = 0.0
        self._set_toolhead_position(th_pos, [2])
        th.wait_moves()

        # 1. Determine valid drive currents at the bed (Z=0.0)
        self._log_msg("Checking valid drive currents at the bed (Z=0.0)...")
        dc_to_test = list(range(32))
        # For Z=0.0 (sensor is very close to the bed), we exclude ERR_ALE (Amplitude Low, bit 9)
        # and ERR_UR (Under-range, bit 13) because heavy loading near metal is expected and handled.
        error_mask_zero = (1 << 10) | (1 << 11) | (1 << 12)  # ERR_AHE, ERR_WD, ERR_OR
        valid_at_zero = []
        for dc in dc_to_test:
            self._sensor.set_drive_current(dc)
            th.dwell(0.100)
            th.wait_moves()
            is_valid = True
            for _ in range(5):
                val = self._sensor.read_one_value()
                if val.freq <= 0.0 or (val.status & error_mask_zero) != 0:
                    is_valid = False
                    break
                th.dwell(0.020)
                th.wait_moves()
            if is_valid:
                valid_at_zero.append(dc)

        self._sensor.set_drive_current(old_drive_current)
        self._log_msg(f"Drive currents valid at Z=0.0: {valid_at_zero}")
        if not valid_at_zero:
            self._log_error("No valid drive currents detected at Z=0.0")
            self._z_not_homed()
            return

        # lift up above cal_z_max, and then move over so the probe
        # is over the nozzle position
        th.manual_move([None, None, cal_z_max + 3.0], self.params.lift_speed)
        th.manual_move(
            [
                th_pos[0] - self.offset["x"],
                th_pos[1] - self.offset["y"],
                None,
            ],
            self.params.move_speed,
        )
        th.wait_moves()

        # 2. Verify drive currents at Z = cal_z_max + 3.0
        self._log_msg(f"Checking candidate drive currents at Z={cal_z_max + 3.0:.1f}...")
        error_mask_far = (1 << 9) | (1 << 10) | (1 << 11) | (1 << 12) | (1 << 13)
        valid_at_far = []
        for dc in dc_to_test:
            self._sensor.set_drive_current(dc)
            th.dwell(0.100)
            th.wait_moves()
            is_valid = True
            for _ in range(5):
                val = self._sensor.read_one_value()
                if val.freq <= 0.0 or (val.status & error_mask_far) != 0:
                    is_valid = False
                    break
                th.dwell(0.020)
                th.wait_moves()
            if is_valid:
                valid_at_far.append(dc)

        self._sensor.set_drive_current(old_drive_current)
        self._log_msg(f"Drive currents valid at Z={cal_z_max + 3.0:.1f}: {valid_at_far}")

        valid_currents = sorted(list(set(valid_at_zero) | set(valid_at_far)))
        self._log_msg(f"Drive currents to calibrate (union of valid ranges): {valid_currents}")
        if not valid_currents:
            self._log_error("No valid drive currents detected at Z=0.0 or Z=10.0")
            self._z_not_homed()
            return

        # 3. Calibrate all valid drive currents (with temperature loop if 3D calibration is active)
        self._dc_to_temp_fmaps = {}
        calibrated_mappings = {}

        if self._temp_sensor_obj is not None:
            calibrated_temp_mappings = {dc: [] for dc in valid_currents}
            room_temp = self.get_sensor_temp() or 0.0
            last_sweep_temp = room_temp

            self._log_msg(f"Running baseline room temperature calibration sweep at {room_temp:.1f}C...")
            for dc in valid_currents:
                self._log_msg(f"Calibrating drive current {dc} at {room_temp:.1f}C...")
                mapping, fth_fit, htf_fit = self._create_mapping(
                    cal_z_max,
                    z_target,
                    self.params.probe_speed,
                    self.params.lift_speed,
                    dc,
                    report_errors=False,
                    write_debug_files=debug,
                )
                if mapping is not None:
                    calibrated_temp_mappings[dc].append((room_temp, mapping, fth_fit))
                    self._log_msg(f"  Drive current {dc}: Calibrated successfully at room temperature")
                    self._log_calibration_sweep_data(room_temp, dc, mapping)

            # Raise toolhead high for safety between sweeps
            th.manual_move([None, None, cal_z_max + 3.0], self.params.lift_speed)
            th.wait_moves()

            # Round up to next multiple of 5 above room temp (toolhead sensor temp)
            start_target = math.ceil(room_temp / 5.0) * 5.0
            if start_target <= room_temp:
                start_target += 5.0

            targets = []
            t = start_target
            while t < self._setup_target_temp:
                targets.append(t)
                t += 5.0
            targets.append(self._setup_target_temp)

            self._log_msg(f"Generated step-by-step target bed temperatures: {targets}")

            for t in targets:
                self._log_msg(f"Heating bed to step target {t:.1f}C...")
                self._gcode.run_script_from_command(f"M190 S{t:.0f}")

                current_temp = self.get_sensor_temp() or 0.0
                self._log_msg(
                    f"Bed reached target {t:.1f}C. "
                    f"Sensor temperature is {current_temp:.1f}C. "
                    f"Running sweep for valid drive currents..."
                )

                for dc in valid_currents:
                    self._log_msg(f"Calibrating drive current {dc} at {current_temp:.1f}C...")
                    mapping, fth_fit, htf_fit = self._create_mapping(
                        cal_z_max,
                        z_target,
                        self.params.probe_speed,
                        self.params.lift_speed,
                        dc,
                        report_errors=False,
                        write_debug_files=debug,
                    )
                    if mapping is not None:
                        calibrated_temp_mappings[dc].append((current_temp, mapping, fth_fit))
                        self._log_calibration_sweep_data(current_temp, dc, mapping)

            # Build self._dc_to_temp_fmaps and evaluate final sweep
            for dc in valid_currents:
                if calibrated_temp_mappings[dc]:
                    self._dc_to_temp_fmaps[dc] = []
                    for temp, mapping, _ in calibrated_temp_mappings[dc]:
                        self._dc_to_temp_fmaps[dc].append((temp, mapping))
                    self._dc_to_temp_fmaps[dc].sort(key=lambda x: x[0])

                    # Calculate and log cubic Z-drift coefficients
                    try:
                        sweeps = calibrated_temp_mappings[dc][:]
                        # Sort sweeps by temperature
                        sweeps.sort(key=lambda x: x[0])
                        baseline_temp, baseline_map, _ = sweeps[0]

                        # Find safe height range intersection (limiting htof evaluation range up to 5.0mm)
                        common_min = max(item[1].height_range[0] for item in sweeps)
                        common_max = min(item[1].height_range[1] for item in sweeps)
                        common_max = min(5.0, common_max)

                        if common_min < common_max:
                            height_grid = np.linspace(common_min, common_max, 100)
                            delta_h_vals = []
                            delta_T_vals = []

                            for temp, mapping, _ in sweeps:
                                delta_T = temp - baseline_temp
                                delta_T_vals.append(delta_T)

                                if delta_T == 0.0:
                                    delta_h_vals.append(0.0)
                                    continue

                                # Evaluate periods and frequencies
                                freqs = []
                                for h in height_grid:
                                    p = mapping._htof(h)
                                    freqs.append(1.0 / p)

                                # Predict heights using baseline ftoh
                                h_preds = []
                                for f in freqs:
                                    h_preds.append(baseline_map._ftoh(1.0 / f))

                                delta_h = np.mean(np.array(h_preds) - height_grid)
                                delta_h_vals.append(delta_h)

                            delta_T_vals = np.array(delta_T_vals)
                            delta_h_vals = np.array(delta_h_vals)

                            # Solve least squares for cubic with zero-intercept
                            A = np.column_stack((delta_T_vals**3, delta_T_vals**2, delta_T_vals))
                            c3, c2, c1 = np.linalg.lstsq(A, delta_h_vals, rcond=None)[0]
                            self._dc_to_drift_coefs[dc] = (baseline_temp, c3, c2, c1)

                            self._log_msg(
                                f"Thermal Z-drift cubic coefficients for DC {dc} (ref {baseline_temp:.2f}C):\n"
                                f"  c3 = {c3:.4e}, c2 = {c2:.4e}, c1 = {c1:.4e}\n"
                                f"  Formula: delta_h(dT) = c3*dT^3 + c2*dT^2 + c1*dT (in mm)"
                            )
                    except Exception as e:
                        logging.warning(f"Could not calculate cubic Z-drift coefficients for DC {dc}: {e}")

                    # Select optimal homing/tap evaluation mapping from final sweep
                    final_sweep = calibrated_temp_mappings[dc][-1]
                    calibrated_mappings[dc] = (final_sweep[1], final_sweep[2])

        else:
            # Fallback to standard 2D calibration at target temperature
            for dc in valid_currents:
                self._log_msg(f"Calibrating drive current {dc}...")
                mapping, fth_fit, htf_fit = self._create_mapping(
                    cal_z_max,
                    z_target,
                    self.params.probe_speed,
                    self.params.lift_speed,
                    dc,
                    report_errors=False,
                    write_debug_files=debug,
                )
                if mapping is None or fth_fit is None or htf_fit is None:
                    self._log_msg(f"  Drive current {dc}: Calibration failed to fit mapping")
                    continue

                calibrated_mappings[dc] = (mapping, fth_fit)
                self._dc_to_fmap[dc] = mapping
                self._log_msg(f"  Drive current {dc}: Calibrated successfully")

        # 4. Select homing and tap drive currents
        homing_req_min = 0.5
        homing_req_max = min(
            self.params.home_trigger_height + self.params.home_trigger_safe_start_offset + 1.0,
            cal_z_max
        )
        tap_req_min = 0.025
        # Require tap candidates to support at least tap_start_z + 0.5 to allow for safety margin and backlash moves
        tap_req_max = max(
            self.params.home_trigger_height,
            self.params.tap_trigger_safe_start_height,
            self.params.tap_start_z + 0.5
        )

        self._log_msg(
            f"Evaluating drive currents (Homing req: Z={homing_req_min}..{homing_req_max:.2f}mm, "
            f"Tap req: Z={tap_req_min}..{tap_req_max:.2f}mm):"
        )

        valid_homing_candidates = []
        valid_tap_candidates = []

        for dc in sorted(calibrated_mappings.keys()):
            mapping, fth_rms = calibrated_mappings[dc]

            # Check if ok for homing
            ok_for_homing = True
            if mapping.height_range[0] > homing_req_min or mapping.height_range[1] < homing_req_max:
                ok_for_homing = False
            if mapping.freq_spread() < 0.30:
                ok_for_homing = False
            if fth_rms is None or fth_rms > 0.025:
                ok_for_homing = False

            # Check if ok for tap
            ok_for_tap = True
            if mapping.height_range[0] > tap_req_min or mapping.height_range[1] < tap_req_max:
                ok_for_tap = False
            if mapping.freq_spread() < 0.30:
                ok_for_tap = False
            if fth_rms is None or fth_rms > 0.025:
                ok_for_tap = False

            self._log_msg(
                f"  Drive current {dc}: range={mapping.height_range[0]:.3f}..{mapping.height_range[1]:.3f}mm, "
                f"spread={mapping.freq_spread():.2f}%, fit_rms={fth_rms:.4f} -> "
                f"homing={ok_for_homing}, tap={ok_for_tap}"
            )

            if ok_for_homing:
                valid_homing_candidates.append(dc)
            if ok_for_tap:
                valid_tap_candidates.append(dc)

        chosen_homing = None
        if valid_homing_candidates:
            # Optimal homing: lowest valid drive current (widest height range)
            chosen_homing = valid_homing_candidates[0]
            self._reg_drive_current = chosen_homing
            self._log_msg(f"using {chosen_homing} for homing (lowest valid).")
        else:
            self._log_warning("Could not find any drive current suitable for homing.")

        chosen_tap = None
        if valid_tap_candidates:
            # Optimal tap: highest valid drive current (highest near-bed sensitivity)
            chosen_tap = valid_tap_candidates[-1]
            self._log_msg(f"calculated tap drive current candidate: {chosen_tap}")

            # Construct TapConfig for scanning
            mode = self.params.tap_mode
            tap_threshold = self.params.tap_threshold
            tapcfg = ProbeEddy.TapConfig(mode=mode, threshold=tap_threshold)
            if mode == "butter":
                sos = None
                if self.params.is_default_butter_config():
                    sos = self.get_default_butter_sos(self._sensor._data_rate)
                if sos is None:
                    if scipy:
                        sos = scipy.signal.butter(
                            self.params.tap_butter_order,
                            [ self.params.tap_butter_lowcut, self.params.tap_butter_highcut, ],
                            btype="bandpass",
                            fs=self._sensor._data_rate,
                            output="sos",
                        ).tolist()
                    else:
                        raise self._printer.command_error("Scipy is not available, cannot use custom filter, or data rate is not 250 or 500")
                tapcfg.sos = sos

            # Scan the calculated drive current +/- 2
            tap_candidates = []
            for dc in range(chosen_tap - 2, chosen_tap + 3):
                if 1 <= dc <= 31 and dc in calibrated_mappings:
                    tap_candidates.append(dc)

            self._log_msg(f"Scanning tap viability around calculated drive current {chosen_tap} (candidates: {tap_candidates})...")
            working_tap_currents = []
            for dc in tap_candidates:
                tap_map = calibrated_mappings[dc][0]
                start_z = min(self.params.tap_start_z, tap_map.height_range[1] - 0.1)
                start_z = max(start_z, self.params.home_trigger_height + 0.1)
                target_z = self.params.tap_target_z

                self._log_msg(f"  Testing tap viability for drive current {dc} (start_z={start_z:.3f}, target_z={target_z:.3f})...")
                try:
                    success_count = 0
                    last_err = None
                    for attempt in range(1, 6):
                        tap_res = self.do_one_tap(
                            start_z=start_z,
                            target_z=target_z,
                            tap_speed=self.params.tap_speed,
                            lift_speed=self.params.lift_speed,
                            tapcfg=tapcfg,
                            drive_current=dc,
                        )

                        if tap_res.error is None:
                            success_count += 1
                            self._log_msg(f"    Attempt {attempt}: Tap SUCCEEDED (z={tap_res.probe_z:.3f}, overshoot={tap_res.overshoot:.3f})")
                        else:
                            last_err = tap_res.error
                            self._log_msg(f"    Attempt {attempt}: Tap FAILED ({tap_res.error})")

                        th.dwell(0.100)
                        th.wait_moves()

                    if success_count >= 3:
                        working_tap_currents.append(dc)
                        self._log_msg(f"    Drive current {dc}: Tap SUCCEEDED overall ({success_count}/5 attempts succeeded)")
                    else:
                        self._log_msg(f"    Drive current {dc}: Tap FAILED overall ({success_count}/5 attempts succeeded, last error: {last_err})")
                except Exception as e:
                    self._log_msg(f"    Drive current {dc}: Tap exception ({e})")

            self._log_msg(f"Working drive currents for tap: {working_tap_currents}")
            if working_tap_currents:
                highest_working = working_tap_currents[-1]
                self._tap_drive_current = highest_working
                self._log_msg(f"Selected highest working drive current {highest_working} for tap.")
            else:
                self._log_warning(
                    f"None of the tested drive currents in range {tap_candidates} worked for tap. "
                    f"Falling back to calculated chosen_tap: {chosen_tap}."
                )
                self._tap_drive_current = chosen_tap

            # Automatically update tap_start_z to a conservative value within the range
            tap_map = calibrated_mappings[self._tap_drive_current][0]
            suggested_start_z = math.floor(tap_map.height_range[1] * 10.0) / 10.0 - 0.1
            suggested_start_z = max(suggested_start_z, self.params.home_trigger_height + 0.1)
            self.params.tap_start_z = min(self.params.tap_start_z, suggested_start_z)
            self._log_msg(f"Automatically set tap_start_z to conservative value: {self.params.tap_start_z:.2f}mm")
        else:
            self._log_warning("Could not find any drive current suitable for tap.")

        if calibrated_mappings:
            self.save_config(log_msg=False)

            # Show summary of all the ranges at each drive strength, and the selected drive strength for homing, and tap
            self._log_msg("\n================== EDDY-ng Calibration Summary ==================")
            for dc in sorted(calibrated_mappings.keys()):
                mapping, fth_rms = calibrated_mappings[dc]
                self._log_msg(
                    f"  Drive Current {dc:2d}: Range = {mapping.height_range[0]:.3f} to {mapping.height_range[1]:.3f} mm, "
                    f"Fit RMS = {fth_rms:.4f}, Freq Spread = {mapping.freq_spread():.2f}%"
                )
            self._log_msg("-----------------------------------------------------------------")
            self._log_msg(f"  Selected Homing Drive Current: {chosen_homing}")
            self._log_msg(f"  Selected Tap Drive Current:    {self._tap_drive_current}")
            self._log_msg("=================================================================\n")

            if chosen_homing is not None and chosen_tap is not None:
                self._log_msg("Setup success. Homing and tap viability have been verified. Please issue SAVE_CONFIG to save calibration data and restart.")
            else:
                self._log_error("Setup completed but did not find suitable drive currents for both homing and tap.")
        else:
            self._log_error("Setup failed: Calibration failed for all candidate drive currents")

        self.reset_drive_current()
        self._z_not_homed()

    cmd_CALIBRATE_help = (
        "Calibrate the eddy current sensor. Requires TEMPERATURE to specify the calibration temperature. "
        + "Specify DRIVE_CURRENT to calibrate for a different drive current than the default. "
        + "Specify START_Z to set a different calibration start point."
    )

    def cmd_CALIBRATE(self, gcmd: GCodeCommand):
        temp = gcmd.get_float("TEMPERATURE", None)
        if temp is None:
            raise gcmd.error("TEMPERATURE parameter is required for PROBE_EDDY_NG_CALIBRATE")

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
        self._set_toolhead_position(th_pos, [2])

        manual_probe.ManualProbeHelper(
            self._printer,
            gcmd,
            lambda kin_pos: self.cmd_CALIBRATE_next(gcmd, kin_pos, temp),
        )

    def cmd_CALIBRATE_next(self, gcmd: GCodeCommand, kin_pos: Optional[List[float]], temp: float):
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
        self._set_toolhead_position(th_pos, [2])

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

        mapping, fth_fit, htf_fit = self._create_mapping(
            cal_z_max,
            z_target,
            probe_speed,
            lift_speed,
            drive_current,
            report_errors=True,
            write_debug_files=True,
        )
        if mapping is None or fth_fit is None or htf_fit is None:
            self._log_error("Calibration failed")
            return

        # Update the specified drive current and temperature in self._dc_to_temp_fmaps
        if drive_current not in self._dc_to_temp_fmaps:
            self._dc_to_temp_fmaps[drive_current] = []

        mappings_list = self._dc_to_temp_fmaps[drive_current]
        replaced = False
        for i, (t, m) in enumerate(mappings_list):
            if abs(t - temp) < 0.001:
                mappings_list[i] = (temp, mapping)
                replaced = True
                break
        if not replaced:
            mappings_list.append((temp, mapping))

        # Sort mappings list by temperature
        mappings_list.sort(key=lambda x: x[0])

        self.save_config()

        # reset the Z homing state after calibration
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
    ) -> Tuple[ProbeEddyFrequencyMap, float, float]:
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
            return None, None, None

        # and build a map
        mapping = ProbeEddyFrequencyMap(self)
        mapping.raw_data = (times, freqs, heights, vels)
        fth_fit, htf_fit = mapping.calibrate_from_values(
            drive_current,
            times,
            freqs,
            heights,
            vels,
            report_errors,
            write_debug_files,
        )

        return mapping, fth_fit, htf_fit

    def _capture_samples_down_to(self, z_target: float, probe_speed: float) -> tuple[List[float], List[float], List[float], List[float]]:
        th = self._printer.lookup_object("toolhead")
        th.dwell(0.500)  # give the sensor a bit to settle
        th.wait_moves()

        with self.start_sampler(calculate_heights=False) as sampler:
            first_sample_time = th.get_last_move_time()
            th.manual_move([None, None, z_target], probe_speed)
            last_sample_time = th.get_last_move_time()
            # Can't use wait_for_sample_at_time here, because the tail end of
            # samples might be errors so they won't be passed to the sampler.
            # Should fix that, but for now just wait an extra half second which
            # should be more than enough.
            # sampler.wait_for_sample_at_time(last_sample_time)
            th.dwell(0.500)
            th.wait_moves()
            sampler.finish()

        # the samples are a list of [print_time, freq, dummy_height] tuples
        if sampler.raw_count == 0:
            return None, None, None, None

        freqs = []
        heights = []
        times = []
        vels = []

        for i in range(sampler.raw_count):
            s_t = sampler.times[i]
            s_freq = sampler.freqs[i]
            s_pos, s_v = self._get_trapq_position(s_t)
            s_z = s_pos[2]
            if first_sample_time < s_t < last_sample_time and s_z >= z_target:
                times.append(s_t)
                freqs.append(s_freq)
                heights.append(s_z)
                vels.append(s_v)

        return times, freqs, heights, vels

    def cmd_TEST_DRIVE_CURRENT(self, gcmd: GCodeCommand):
        drive_current: int = gcmd.get_int("DRIVE_CURRENT", self._reg_drive_current, minval=1, maxval=31)
        z_start: float = gcmd.get_float("START_Z", self.params.calibration_z_max, above=2.0)
        z_end: float = gcmd.get_float("TARGET_Z", 0.0)
        debug: bool = gcmd.get_int("DEBUG", 0) == 1
        self._log_msg(f"Testing Z={z_start:.3f} to Z={z_end:.3f}")

        mapping, fth, htf = self._create_mapping(
            z_start,
            z_end,
            self.params.probe_speed,
            self.params.lift_speed,
            drive_current,
            report_errors=False,
            write_debug_files=debug,
        )
        if mapping is None or fth is None or htf is None:
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
        # method = gcmd.get('METHOD', 'automatic').lower()
        # if method in ('scan', 'rapid_scan'):
        #    session = ProbeEddyScanningProbe(self, gcmd)
        #    session._start_session()
        #    return session
        #
        # return self._probe_session.start_probe_session(gcmd)

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
        if not r.valid:
            raise self._printer.command_error("Probe captured no samples!")

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
    def _probe_to_start_position_unhomed(self, move_home=False):
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

        # If we can't get a value at all for right now, for safety, just abort.
        if now_height is None:
            raise self._printer.command_error("Couldn't get any valid samples from sensor.")

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
            self._set_toolhead_position(th_pos, [2])

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
            max_z = start_z + 1.0
            # limit backlash overshoot to not exceed sensor's calibrated height range if calibrated
            dc = self.current_drive_current()
            if self.calibrated(dc):
                fmap = self.map_for_drive_current(dc)
                if fmap.height_range[1] > start_z:
                    # leave a small buffer of 0.05mm below the absolute limit to be safe
                    max_z = min(max_z, fmap.height_range[1] - 0.05)
                    # make sure max_z is still above start_z so we do some overshoot
                    if max_z <= start_z:
                        max_z = start_z
            self._log_debug(f"probe_to_start_position: moving toolhead from {th_pos[2]:.3f} to {max_z:.3f}")
            th_pos[2] = max_z
            th.manual_move(th_pos, self.params.lift_speed)

        self._log_debug(f"probe_to_start_position: moving toolhead from {th_pos[2]:.3f} to {start_z:.3f}")
        th_pos[2] = start_z
        th.manual_move(th_pos, self.params.probe_speed)

        th.wait_moves()

    cmd_TAP_help = (
        "Calculate a z-offset by touching the build plate. "
        "Optional parameters: DRIVE_CURRENT (1-31), SPEED, RETRACT_SPEED, START_Z, "
        "TARGET_Z, THRESHOLD (or TT), ADJUST_Z, RETRACT (0 or 1), SAMPLES, "
        "MAX_SAMPLES, SAMPLES_STDDEV, USE_MEDIAN (0 or 1), HOME_Z (0 or 1), "
        "PLOT (0, 1, or 2), MODE (wma or butter)."
    )

    def cmd_TAP(self, gcmd: GCodeCommand):
        # Start from a clean slate even if an earlier probe/tap/mesh died half way
        self.recover_probe_state("before tap")
        drive_current = self._sensor.get_drive_current()
        try:
            self.cmd_TAP_next(gcmd)
        finally:
            self._sensor.set_drive_current(drive_current)
            self.recover_probe_state("after tap")

    @dataclass
    class TapResult:
        error: Optional[Exception]
        probe_z: float
        toolhead_z: float
        overshoot: float
        tap_time: float
        tap_start_time: float
        tap_end_time: float

    @dataclass
    class TapConfig:
        mode: str
        threshold: float
        sos: Optional[List[List[float]]] = None

    def do_one_tap(
        self,
        start_z: float,
        target_z: float,
        tap_speed: float,
        lift_speed: float,
        tapcfg: ProbeEddy.TapConfig,
        drive_current: Optional[int] = None,
    ) -> TapResult:
        self.probe_to_start_position(start_z)

        th = self._printer.lookup_object("toolhead")

        target_position = th.get_position()
        target_position[2] = target_z

        error = None
        orig_dc = self.current_drive_current()

        try:
            if drive_current is not None:
                self._sensor.set_drive_current(drive_current)
                th.dwell(0.050)
                th.wait_moves()

            # configure the endstop for tap (gets reset at the end of a tap sequence,
            # also in finally just in case
            self._endstop_wrapper.tap_config = tapcfg

            endstops = [(self._endstop_wrapper, "probe")]
            hmove = HomingMove(self._printer, endstops)

            try:
                probe_position = hmove.homing_move(target_position, tap_speed, probe_pos=True)

                # raise toolhead as soon as tap ends
                finish_z = th.get_position()[2]
                if finish_z < 1.0:
                    th.manual_move([None, None, start_z], lift_speed)

                if hmove.check_no_movement() is not None:
                    raise self._printer.command_error("Probe triggered prior to movement")

                probe_z = probe_position[2]

                self._log_debug(f"tap: probe_z: {probe_z:.3f} finish_z: {finish_z:.3f} moved up to {start_z:.3f}")

                if probe_z - target_z < 0.050:
                    # we detected a tap but it was too close to our target z
                    # to be trusted
                    # TODO: use velocity to determine this
                    return ProbeEddy.TapResult(
                        error=Exception("Tap detected too close to target z"),
                        toolhead_z=finish_z,
                        probe_z=probe_z,
                        overshoot=0.0,
                        tap_time=0.0,
                        tap_start_time=0.0,
                        tap_end_time=0.0,
                    )

            except self._printer.command_error as err:
                if self._printer.is_shutdown():
                    raise self._printer.command_error("Probing failed due to printer shutdown")

                # The failure may have happened before Klipper's homing:homing_move_end event
                # (and nothing fires gcode:command_error because we swallow the error below),
                # so make sure the sampler / endstop / MCU are not left armed.
                self.recover_probe_state("tap failed")

                # in case of failure don't leave the toolhead in a bad spot (i.e. in bed)
                finish_z = th.get_position()[2]
                if finish_z < 1.0:
                    th.manual_move([None, None, start_z], lift_speed)

                # If just sensor errors, let the caller handle it
                self._log_error(f"Tap failed with Z at {finish_z:.3f}: {err}")
                # NB: this used to be `"a" or "b" or "c" in str(err)`, which is always True and
                # so swallowed *every* error. These are the ones a retry can fix:
                retryable = (
                    "Sensor error",
                    "Probe completed movement",
                    "Probe triggered prior",
                    "Probe too low",
                    "Probe cleared safe height too early",
                    "No trigger on",
                    "No samples received",
                    "Watchdog",
                    "Communication timeout",
                )
                if any(m in str(err) for m in retryable):
                    return ProbeEddy.TapResult(
                        error=err,
                        toolhead_z=finish_z,
                        probe_z=0.0,
                        overshoot=0.0,
                        tap_time=0.0,
                        tap_start_time=0.0,
                        tap_end_time=0.0,
                    )
                else:
                    raise
        finally:
            self._endstop_wrapper.tap_config = None
            if self._sampler is not None and not self._streaming:
                # only possible if something above blew up half way through the homing move
                self.recover_probe_state("tap aborted")
            if drive_current is not None:
                self._sensor.set_drive_current(orig_dc)
                th.wait_moves()

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
        tap_time = tap_start_time + (tap_end_time - tap_start_time) * self.params.tap_time_position

        return ProbeEddy.TapResult(
            error=error,
            probe_z=probe_z,
            toolhead_z=finish_z,
            overshoot=overshoot,
            tap_time=tap_time,
            tap_start_time=tap_start_time,
            tap_end_time=tap_end_time,
        )

    def _compute_butter_tap(self, sampler):
        if not scipy:
            return None, None

        trigger_freq = self.height_to_freq(self.params.home_trigger_height)

        s_f = np.asarray(sampler.freqs)
        first_one = np.argmax(s_f >= trigger_freq)
        s_t = np.asarray(sampler.times[first_one:])
        s_f = np.asarray(sampler.freqs[first_one:])

        lowcut = self.params.tap_butter_lowcut
        highcut = self.params.tap_butter_highcut
        order = self.params.tap_butter_order

        sos = scipy.signal.butter(
            order,
            [lowcut, highcut],
            btype="bandpass",
            fs=self._sensor._data_rate,
            output="sos",
        )
        filtered = scipy.signal.sosfilt(sos, s_f - s_f[0])

        return s_t, filtered

    def cmd_TAP_next(self, gcmd: Optional[GCodeCommand] = None):
        self._log_debug("\nEDDYng Tap begin")

        if gcmd is None:
            gcmd = self._dummy_gcode_cmd

        orig_drive_current: int = self._sensor.get_drive_current()
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
        tap_threshold = gcmd.get_float("TT", tap_threshold)  # alias for THRESHOLD
        tap_adjust_z = gcmd.get_float("ADJUST_Z", self._tap_adjust_z)
        do_retract = gcmd.get_int("RETRACT", 1) == 1
        samples = gcmd.get_int("SAMPLES", self.params.tap_samples, minval=1)
        max_samples = gcmd.get_int("MAX_SAMPLES", self.params.tap_max_samples, minval=samples)
        samples_stddev = gcmd.get_float("SAMPLES_STDDEV", self.params.tap_samples_stddev, above=0.0)
        use_median: bool = gcmd.get_int("USE_MEDIAN", 1 if self.params.tap_use_median else 0) == 1
        home_z: bool = gcmd.get_int("HOME_Z", 1) == 1
        write_plot_arg: int = gcmd.get_int("PLOT", None)

        mode = gcmd.get("MODE", self.params.tap_mode).lower()
        if mode not in ("wma", "butter"):
            raise self._printer.command_error(f"Invalid mode: {mode}")

        # if the mode is different than the params, then require
        # specifying threshold
        if tap_threshold is None:
            if mode != self.params.tap_mode:
                raise self._printer.command_error(
                    f"THRESHOLD required when mode ({mode}) is different than configured default ({self.params.tap_mode})"
                )
            tap_threshold = self.params.tap_threshold

        if not self._z_homed():
            raise self._printer.command_error("Z axis must be homed before tapping")

        write_tap_plot = self.params.write_tap_plot
        write_every_tap_plot = self.params.write_every_tap_plot and write_tap_plot
        if write_plot_arg is not None:
            write_tap_plot = write_plot_arg > 0
            write_every_tap_plot = write_plot_arg > 1

        tapcfg = ProbeEddy.TapConfig(mode=mode, threshold=tap_threshold)
        # fmt: off
        if mode == "butter":
            sos = None
            if self.params.is_default_butter_config():
                sos = self.get_default_butter_sos(self._sensor._data_rate)
            if sos is None:
                if scipy:
                    sos = scipy.signal.butter(
                        self.params.tap_butter_order,
                        [ self.params.tap_butter_lowcut, self.params.tap_butter_highcut, ],
                        btype="bandpass",
                        fs=self._sensor._data_rate,
                        output="sos",
                    ).tolist()
                else:
                    raise self._printer.command_error("Scipy is not available, cannot use custom filter, or data rate is not 250 or 500")
            tapcfg.sos = sos
        # fmt: on

        results = []
        tap_z = None
        tap_stddev = None
        tap_overshoot = None
        sample_err_count = 0
        sensor_err_streak = 0
        tap = None

        sample_last_err = None

        # Samples that failed because of a sensor error while we were stepping down the
        # drive current don't count against max_samples (see below).
        attempt_limit = max_samples
        sample_i = -1
        while True:
            sample_i += 1
            if sample_i >= attempt_limit:
                break
            if self.params.debug:
                self.save_samples_path = f"/tmp/tap-samples-{sample_i+1}.csv"

            tap = self.do_one_tap(
                start_z=tap_start_z,
                target_z=target_z,
                tap_speed=tap_speed,
                lift_speed=lift_speed,
                tapcfg=tapcfg,
                drive_current=tap_drive_current,
            )

            if write_every_tap_plot:
                try:
                    self._write_tap_plot(tap, sample_i)
                except Exception as e:
                    self._log_error(f"Failed to write tap plot: {e}")

            if tap.error:
                if any(m in str(tap.error) for m in self.SENSOR_ERROR_MARKERS):
                    sensor_err_streak += 1
                    if sensor_err_streak >= 2:
                        lower_dc = self._next_lower_calibrated_dc(tap_drive_current)
                        if lower_dc is not None:
                            self._log_warning(
                                f"EDDYng: sensor keeps failing at tap drive current {tap_drive_current}; "
                                f"stepping down to {lower_dc} for this tap. If this repeats, lower "
                                "tap_drive_current (and recalibrate)."
                            )
                            tap_drive_current = lower_dc
                            sensor_err_streak = 0
                            attempt_limit += 2  # don't let the failed attempts eat the sample budget
                else:
                    sensor_err_streak = 0
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

            if samples == 1:
                # only one sample, we're done
                tap_z = tap.probe_z
                tap_stddev = 0.0
                tap_overshoot = tap.overshoot
                break

            if len(results) >= samples:
                tap_z, tap_stddev, tap_overshoot = self._compute_tap_z(results, samples, samples_stddev, use_median)
                if tap_z is not None:
                    break
        if write_tap_plot and not write_every_tap_plot and tap:
            try:
                self._write_tap_plot(tap)
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
            #true_z_zero = - (tap_adjust_z + tap_overshoot)
            true_z_zero = - computed_tap_z
            th_pos[2] = th_pos[2] + true_z_zero
            homed_to_str = f"homed z with true_z_zero={true_z_zero:.3f}, thz={th_z:.3f}, setz={th_pos[2]:.3f}, overshoot={tap_overshoot:.3f}, "
            self._set_toolhead_position(th_pos, [2])
            self._last_tap_gcode_adjustment = 0.0
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
        self._sensor.set_drive_current(orig_drive_current)
        th_now = th.get_position()
        th.manual_move([None, None, self.params.home_trigger_height + 1.0], lift_speed)
        th.manual_move([th_now[0] - self.params.x_offset, th_now[1] - self.params.y_offset, None], self.params.move_speed)
        th.manual_move([None, None, self.params.home_trigger_height], self.params.probe_speed)
        th.dwell(0.500)
        th.wait_moves()

        result = self.probe_static_height()
        self._tap_offset = float(self.params.home_trigger_height - result.value)

        # Restore XY position to where the tap was performed
        th.manual_move([None, None, self.params.home_trigger_height + 1.0], lift_speed)
        th.manual_move([th_now[0], th_now[1], None], self.params.move_speed)
        th.wait_moves()

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

    # Compute the average tap_z from a set of tap results, taking a cluster of samples
    # from the result that has the lowest standard deviation
    def _compute_tap_z(self, taps: List[ProbeEddy.TapResult], samples: int, req_stddev: float, use_median: bool) -> Tuple[float, float, float]:
        if len(taps) < samples:
            return None, None, None

        tap_z = math.inf
        std_min = math.inf
        overshoot = math.inf
        for cluster in combinations(taps, samples):
            tap_zs = np.array([t.probe_z for t in cluster])
            overshoots = np.array([t.overshoot for t in cluster])
            std = np.std(tap_zs)
            if std < std_min:
                std_min = std
                if use_median:
                    # we need the corresponding overshoot as well, so
                    # can't just use np.median().
                    sorted_indices = np.argsort(tap_zs)
                    idx = len(tap_zs) // 2
                    tap_z = tap_zs[sorted_indices[idx]]
                    overshoot = overshoots[sorted_indices[idx]]
                else:
                    tap_z = np.mean(tap_zs)
                    overshoot = np.mean(overshoots)

        if std_min <= req_stddev:
            return float(tap_z), float(std_min), float(overshoot)
        else:
            return None, float(std_min), None

    # Write a tap plot. This also has logic to compute the averages
    # and the filter mostly-exactly how it's done on the probe MCU itself
    # (vs using numpy or similar) to make these graphs more reprensetative
    def _write_tap_plot(self, tap: ProbeEddy.TapResult, tapnum: int = -1):
        if not plotly:
            return

        if tapnum == -1:
            filename_base = "tap"
        else:
            filename_base = f"tap-{tapnum+1}"
        tapplot_path_png = f"/tmp/{filename_base}.png"
        tapplot_path_html = f"/tmp/{filename_base}.html"

        # delete any old plots to avoid confusion
        if tapplot_path_html and os.path.exists(tapplot_path_html):
            os.remove(tapplot_path_html)
        if tapplot_path_png and os.path.exists(tapplot_path_png):
            os.remove(tapplot_path_png)

        if not self._last_sampler or not self._last_sampler.times:
            return

        s_t = np.asarray(self._last_sampler.times)
        s_f = np.asarray(self._last_sampler.freqs)
        s_z = np.asarray(self._last_sampler.heights)
        s_kinz = np.vectorize(lambda t: self._get_trapq_height(t) or -10)(s_t)

        # Any values below 0.0 are suspect because they were not calibrated,
        # and so are just extrapolated from the fit. Show them differently.
        s_lowz = np.ma.masked_where(s_z >= 0, s_z)
        s_z = np.ma.masked_where(s_z < 0, s_z)

        time_start = s_t.min()

        # normalize times to start at 0
        s_t = s_t - time_start
        tap_start_time = self._last_sampler.memos.get("tap_start_time", time_start) - time_start
        tap_end_time = self._last_sampler.memos.get("trigger_time", time_start) - time_start
        trigger_time = tap_start_time + (tap_end_time - tap_start_time) * self.params.tap_time_position
        tap_threshold = self._last_sampler.memos.get("tap_threshold", 0)

        time_len = s_t.max()

        # compute the butterworth filter, if we have scipy
        if tap is not None and scipy:
            butter_s_t, butter_s_v = self._compute_butter_tap(self._last_sampler)
            butter_s_t = butter_s_t - time_start
        else:
            butter_s_t = butter_s_v = None

        # Do this roughly how the C code does it, to keep the values identical
        # TODO Just report the value from the mcu?
        butter_accum = None
        if butter_s_v is not None:
            # Note: we don't handle freq offset or
            # start this at same point as the C code does
            butter_accum = np.zeros(len(butter_s_v))
            last_value = butter_s_v[0]
            falling = False
            accum_val = 0.0
            for bi, bv in enumerate(butter_s_v):
                if bv <= last_value:
                    falling = True
                    accum_val += last_value - bv
                elif falling and bv > last_value:
                    falling = False
                    accum_val = 0.0
                butter_accum[bi] = accum_val
                last_value = bv

        import plotly.graph_objects as go

        (c_red, c_lt_red) = ('#9e4058', '#C2697F')
        (c_orange, c_lt_orange) = ('#d0641e', '#E68E54')
        (c_yellow, c_lt_yellow) = ('#f9ab0e', '"#FBC559')
        (c_green, c_lt_green) = ('#589e40', '#7FC269')
        (c_blue, c_lt_blue) = ('#2c3778', '#4151B0')
        (c_purple, c_lt_purple) = ('#513965', '#785596')

        fig = go.Figure()

        # fmt: off
        if tap_start_time > 0:
            fig.add_shape(type="line", x0=tap_start_time, x1=tap_start_time, y0=0, y1=1,
                          xref="x", yref="paper", line=dict(color=c_purple, width=2),)
        if trigger_time > 0:
            fig.add_shape(type="line", x0=trigger_time, x1=trigger_time, y0=0, y1=1,
                          xref="x", yref="paper", line=dict(color=c_lt_orange, width=2),)
        if tap_end_time > 0:
            fig.add_shape(type="line", x0=tap_end_time, x1=tap_end_time, y0=0, y1=1,
                          xref="x", yref="paper", line=dict(color=c_purple, width=2),)
        if tap_threshold > 0:
            fig.add_shape(type="line", x0=0, x1=1, y0=tap_threshold, y1=tap_threshold,
                          xref="paper", yref="y3", line=dict(color="gray", width=1, dash="dash"),)

        fig.add_shape(type="line", x0=0, x1=1, y0=tap.probe_z, y1=tap.probe_z,
                      xref="paper", yref="y", line=dict(color=c_lt_orange, width=1),)

        # Computed Z, Toolhead Z, Sensor F
        fig.add_trace(go.Scatter(x=s_t, y=s_z, mode="lines", name="Z", line=dict(color=c_blue)))
        fig.add_trace(go.Scatter(x=s_t, y=s_lowz, mode="lines", name="Z (low)", line=dict(color=c_lt_blue, dash="dash")))
        fig.add_trace(go.Scatter(x=s_t, y=s_kinz, mode="lines", name="KinZ", line=dict(color=c_lt_red)))
        fig.add_trace(go.Scatter(x=s_t, y=s_f, mode="lines", name="Freq", yaxis="y2", line=dict(color=c_orange)))

        # the butter tap if we have the data
        if butter_s_t is not None:
            fig.add_trace(go.Scatter(x=butter_s_t, y=butter_s_v, mode="lines", name="signal", yaxis="y4", line=dict(color=c_green)))
            fig.add_trace(go.Scatter(x=butter_s_t, y=butter_accum, mode="lines", name="threshold", yaxis="y3", line=dict(color="#626b73")))

        fig.update_xaxes(range=[max(0.0, time_len - 0.60), time_len], autorange=False)

        fig.update_layout(
            hovermode="x unified",
            title=dict(text=f"Tap {tapnum+1}: {tap.probe_z:.3f}"),
            yaxis=dict(title="Z", side="right"),  # Z axis
            yaxis2=dict(overlaying="y", title="Freq", tickformat="d", side="left"),  # Freq + WMA
            yaxis3=dict(overlaying="y", side="left", tickformat="d", position=0.2),  # derivatives, tap accum
            yaxis4=dict(overlaying="y", side="right", showticklabels=False),  # filter
            height=800,
        )
        # fmt: on

        timg = 0.0
        thtml = 0.0
        if tapplot_path_png:
            t0 = time.time()
            try:
                fig.write_image(tapplot_path_png)
            except:
                tapplot_path_png = None
            timg = time.time() - t0
        if tapplot_path_html:
            t0 = time.time()
            fig.write_html(tapplot_path_html, include_plotlyjs="cdn")
            thtml = time.time() - t0
        self._log_info(f"Wrote tap plot to {tapplot_path_png or ''} {tapplot_path_html or ''}  [took {timg:.1f}, {thtml:.1f}]")

    def cmd_START_STREAM(self, gcmd):
        self.save_samples_path = "/tmp/stream.csv"
        self._log_info("Eddy sampling enabled")
        self.start_sampler()
        self._streaming = True

    def cmd_STOP_STREAM(self, gcmd):
        self._log_info("Eddy sampling finished")
        self._streaming = False
        if self._sampler is not None:
            self._sampler.finish()
        self._sampler = None



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
        self._toolhead_kin = self._toolhead.get_kinematics()

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
        self._notes.append([start_time, time, th_pos])

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
        self._notes.append((start_time, start_time + self._sample_time / 2.0, th_pos))

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
        for start_time, sample_time, th_pos in self._notes:
            if th_pos is None:
                th_pos, _ = self.eddy._get_trapq_position(sample_time)
                if th_pos is None:
                    raise self._printer.command_error(f"No trapq history found for {sample_time:.3f} and no position!")

            end_time = start_time + self._sample_time
            height = self._sampler.find_height_at_time(start_time, end_time)

            if not math.isclose(th_pos[2], self._scan_z, rel_tol=1e-3):
                logging.info(
                    f"ProbeEddyScanningProbe warning: toolhead not at home_trigger_height ({self._scan_z:.3f}) during probes (saw {th_pos[2]:.3f})"
                )

            h_orig = height
            tz_orig = th_pos[2]

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

        # these two are filled in by the outside.
        self.tap_config: Optional[ProbeEddy.TapConfig] = None
        # if not None, after a probe session is finished we'll
        # write all samples here
        self.save_samples_path: Optional[str] = None

        self._multi_probe_in_progress = False

        self._dispatch = mcu.TriggerDispatch(self._mcu)

        # the times of the last successful endstop home_wait
        self.last_trigger_time = 0.0
        self.last_tap_start_time = 0.0

        self._homing_in_progress = False
        self._sampler: ProbeEddySampler = None

        # Register z_virtual_endstop pin
        self._printer.lookup_object("pins").register_chip("probe", self)
        # Register event handlers
        self._printer.register_event_handler("klippy:mcu_identify", self._handle_mcu_identify)
        self._printer.register_event_handler("homing:homing_move_begin", self._handle_homing_move_begin)
        self._printer.register_event_handler("homing:homing_move_end", self._handle_homing_move_end)
        self._printer.register_event_handler("homing:home_rails_begin", self._handle_home_rails_begin)
        self._printer.register_event_handler("homing:home_rails_end", self._handle_home_rails_end)
        self._printer.register_event_handler("gcode:command_error", self._handle_command_error)

        # copy some things in for convenience
        self._home_trigger_height = self.eddy.params.home_trigger_height
        self._home_trigger_safe_start_offset = self.eddy.params.home_trigger_safe_start_offset
        self._home_start_height = self.eddy._home_start_height  # this is trigger + safe_start + 1.0
        self._probe_speed = self.eddy.params.probe_speed
        self._lift_speed = self.eddy.params.lift_speed

    def _handle_mcu_identify(self):
        kin = self._printer.lookup_object("toolhead").get_kinematics()
        for stepper in kin.get_steppers():
            if stepper.is_active_axis("z"):
                self.add_stepper(stepper)

    def _handle_home_rails_begin(self, homing_state, rails):
        endstops = [es for rail in rails for es, name in rail.get_endstops()]
        if self not in endstops:
            return
        # Nothing to do
        pass

    def _handle_homing_move_begin(self, hmove):
        if self not in hmove.get_mcu_endstops():
            return
        self._sampler = self.eddy.start_sampler()
        self._homing_in_progress = True
        # if we're doing a tap, we're already in the right position;
        # otherwise move there
        if self.tap_config is None:
            self.eddy._probe_to_start_position_unhomed(move_home=True)

    def _handle_homing_move_end(self, hmove):
        if self not in hmove.get_mcu_endstops():
            return
        self._sampler.finish()
        self._homing_in_progress = False

    def _handle_home_rails_end(self, homing_state, rails):
        endstops = [es for rail in rails for es, name in rail.get_endstops()]
        if self not in endstops:
            return
        # Nothing to do
        pass

    def needs_recovery(self) -> bool:
        return self._homing_in_progress or self.tap_config is not None

    def reset_after_failure(self):
        """Undo whatever a homing/tap move that died half way left behind."""
        was_homing = self._homing_in_progress
        self.tap_config = None
        self._homing_in_progress = False
        if was_homing:
            # disarm the MCU side and release the trsync, like home_wait() would have
            try:
                self._sensor.finish_home()
            except Exception:
                logging.exception("EDDYng reset_after_failure: finish_home() failed")
            try:
                self._dispatch.stop()
            except Exception:
                logging.exception("EDDYng reset_after_failure: dispatch.stop() failed")
        self.last_trigger_time = 0.0
        self.last_tap_start_time = 0.0

    def _handle_command_error(self, gcmd=None):
        try:
            self.eddy.recover_probe_state("command error")
        except:
            logging.exception("EDDYng handle_command_error: recovery failed")
        self._homing_in_progress = False

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
            if self.tap_config.mode == "butter":
                sos = self.tap_config.sos
                assert sos
                for i in range(len(sos)):
                    self.eddy._sensor.set_sos_section(i, sos[i])
                mode = "sos"
            elif self.tap_config.mode == "wma":
                mode = "wma"
            else:
                raise self._printer.command_error(f"Invalid tap mode: {self.tap_config.mode}")
            tap_threshold = self.tap_config.threshold
        else:
            mode = "home"
            tap_threshold = None

        self.eddy._log_debug(
            f"EDDYng home_start {mode}: {print_time:.3f} freq: {trigger_freq:.2f} safe-start: {safe_freq:.2f} @ {safe_time:.3f}"
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
            mode=mode,
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
                return tap_start_time + (trigger_time - tap_start_time) * self.eddy.params.tap_time_position
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

    def _setup_sampler(self):
        self._sampler = self.eddy.start_sampler()

    def _finish_sampler(self):
        self._sampler.finish()
        self._sampler = None


# Helper to gather samples and convert them to probe positions
@final
class ProbeEddySampler:
    def __init__(
        self,
        eddy: ProbeEddy,
        calculate_heights: bool = True,
    ):
        self.eddy = eddy
        self._sensor = eddy._sensor
        self._printer = self.eddy._printer
        self._reactor = self._printer.get_reactor()
        self._mcu = self._sensor.get_mcu()
        self._stopped = False
        self._started = False
        self._errors = 0
        self._fmap = eddy.map_for_drive_current() if calculate_heights else None

        self.times = []
        self.raw_freqs = []
        self.freqs = []
        self.heights = [] if self._fmap is not None else None

        self.memos = dict()

    @property
    def raw_count(self):
        return len(self.times)

    @property
    def height_count(self):
        return len(self.heights) if self.heights else 0

    # this is just a handy way to communicate values between different parts of the system,
    # specifically to record things like trigger times for plotting
    def memo(self, name, value):
        self.memos[name] = value

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.finish()

    def active(self):
        return self._started and not self._stopped

    # bulk sample callback for when new data arrives
    # from the probe
    def _add_hw_measurement(self, msg):
        if self._stopped:
            return False

        self._errors += msg["errors"]
        data = msg["data"]

        # data is (t, fv)
        if data:
            times, raw_freqs = zip(*data)
        else:
            times, raw_freqs = [], []

        self.times.extend(times)
        self.raw_freqs.extend(raw_freqs)

        return True

    def start(self):
        if self._stopped:
            raise self._printer.command_error("ProbeEddySampler.start() called after finish()")
        if not self._started:
            self._sensor.add_bulk_sensor_data_client(self._add_hw_measurement)
            self._started = True

    def finish(self):
        if self._stopped:
            return
        if not self._started:
            raise self._printer.command_error("ProbeEddySampler.finish() called without start()")
        if self.eddy._sampler is not self:
            raise self._printer.command_error("ProbeEddySampler.finish(): eddy._sampler is not us!")
        self._update_samples()
        self.eddy._sampler_finished(self)
        self._stopped = True

    def _update_samples(self):
        if len(self.freqs) == len(self.raw_freqs):
            return

        conv_ratio = self._sensor.freqval_conversion_value()

        start_idx = len(self.freqs)
        freqs_np = np.asarray(self.raw_freqs[start_idx:]) * conv_ratio
        self.freqs.extend(freqs_np.tolist())

        if self._fmap is not None:
            heights_np = self.eddy.freqs_to_heights_np(freqs_np)
            self.heights.extend(heights_np.tolist())

    @property
    def error_count(self):
        return self._errors

    # get the last sampled height
    def get_last_height(self) -> float:
        if self.heights is None:
            raise self._printer.command_error("ProbeEddySampler: no height mapping")
        self._update_samples()
        if len(self.heights) == 0:
            raise self._printer.command_error("ProbeEddySampler: no samples")
        return self.heights[-1]

    # wait for a sample for the current time and get a new height
    def get_height_now(self) -> Optional[float]:
        now = self.eddy._print_time_now()
        if not self.wait_for_sample_at_time(now, max_wait_time=1.000, raise_error=False):
            return None
        return self.get_last_height()

    # Wait until a sample for the given time arrives
    def wait_for_sample_at_time(self, sample_print_time, max_wait_time=0.250, raise_error=True) -> bool:
        def report_no_samples():
            if raise_error:
                raise self._printer.command_error(f"No samples received for time {sample_print_time:.3f} (waited for {max_wait_time:.3f})")
            return False

        if self._stopped:
            # if we're not getting any more samples, we can check directly
            if len(self.times) == 0:
                return report_no_samples()
            return self.times[-1] >= sample_print_time

        # quick check
        if self.times and self.times[-1] >= sample_print_time:
            return True

        wait_start_time = self.eddy._print_time_now()

        # if sample_print_time is in the future, make sure to wait max_wait_time
        # past the expected time
        if sample_print_time > wait_start_time:
            max_wait_time = max_wait_time + (sample_print_time - wait_start_time)

        # this is just a sanity check, there shouldn't be any reason to ever wait this long
        if max_wait_time > 30.0:
            traceback.print_stack()
            msg = f"ProbeEddyFrequencySampler: max_wait_time {max_wait_time:.3f} is too far into the future"
            raise self._printer.command_error(msg)

        self.eddy._log_debug(
            f"EDDYng waiting for sample at {sample_print_time:.3f} (now: {wait_start_time:.3f}, max_wait_time: {max_wait_time:.3f})"
        )
        now = self.eddy._print_time_now()
        while len(self.times) == 0 or self.times[-1] < sample_print_time:
            now = self.eddy._print_time_now()
            if now - wait_start_time > max_wait_time:
                return report_no_samples()
            self._reactor.pause(self._reactor.monotonic() + 0.010)

        if now - wait_start_time > 1.0:
            self.eddy._log_info(f"note: waited {now - wait_start_time:.3f}s for sample")

        return True

    # Wait for some samples to be collected, even if errors
    # TODO: there's a minimum wait time -- we need to fill up the buffer before data is sent, and that
    # depends on the data rate
    def wait_for_samples(
        self,
        max_wait_time=0.300,
        count_errors=False,
        min_samples=1,
        new_only=False,
        raise_error=True,
    ):
        # Make sure enough samples have been collected
        wait_start_time = self.eddy._print_time_now()

        start_error_count = self._errors
        start_count = 0
        if new_only:
            start_count = len(self.raw_freqs) + (self._errors if count_errors else 0)

        while (len(self.raw_freqs) + (self._errors if count_errors else 0)) - start_count < min_samples:
            now = self.eddy._print_time_now()
            if now - wait_start_time > max_wait_time:
                if raise_error:
                    raise self._printer.command_error(
                        f"probe_eddy_ng sensor outage: no samples for {max_wait_time:.2f}s (got {self._errors - start_error_count} errors)"
                    )
                return False
            self._reactor.pause(self._reactor.monotonic() + 0.010)

        return True

    def find_heights_at_times(self, intervals):
        self._update_samples()
        times = self.times
        heights = np.asarray(self.heights)
        num_samples = len(times)

        interval_heights = []
        i = 0
        for iv_start, iv_end in intervals:
            while i < num_samples and times[i] < iv_start:
                i += 1
            istart = i

            while i < num_samples and times[i] < iv_end:
                i += 1
            iend = i

            if istart == iend:
                # no samples in this range
                raise self._printer.command_error(f"No samples in time range {iv_start}-{iv_end}")

            median = np.median(heights[istart:iend])
            interval_heights.append(float(median))

        return interval_heights

    def find_height_at_time(self, start_time, end_time):
        if end_time < start_time:
            raise self._printer.command_error("find_height_at_time: end_time is before start_time")

        self._update_samples()

        if len(self.times) == 0:
            raise self._printer.command_error("No samples at all, so none in time range")

        if not self.heights:
            raise self._printer.command_error("Update samples didn't compute heights")

        self.eddy._log_debug(
                f"find_height_at_time: looking between {start_time:.3f}s-{end_time:.3f}s, inside {len(self.times)} samples, time range {self.times[0]:.3f}s to {self.times[-1]:.3f}s"
        )

        # find the first sample that is >= start_time
        start_idx = bisect.bisect_left(self.times, start_time)
        if start_idx >= len(self.times):
            raise self._printer.command_error("Nothing after start_time?")

        # find the last sample that is < end_time
        end_idx = start_idx
        while end_idx < len(self.times) and self.times[end_idx] < end_time:
            end_idx += 1

        # average the heights of the samples in the range
        heights = self.heights[start_idx:end_idx]
        if len(heights) == 0:
            raise self._printer.command_error(f"no samples between time {start_time:.1f} and {end_time:.1f}!")
        hmin, hmax = np.min(heights), np.max(heights)
        mean = np.mean(heights)
        median = np.median(heights)
        self.eddy._log_debug(
            f"find_height_at_time: {len(heights)} samples, median: {median:.3f}, mean: {mean:.3f} (range {hmin:.3f}-{hmax:.3f})"
        )

        return float(median)


class ProbeEddyRationalFit:
    """
    Fits and represents the Z height mapping as a rational function of period:
      h(p) = c0 + c1/(p - p_inf) + c2/(p - p_inf)^2 + c3/(p - p_inf)^3
    where p is in seconds (internally scaled to microseconds for stability).
    Mimics numpy.polynomial.Polynomial interface for compatibility.
    """
    def __init__(self, p_inf, coefs, domain):
        self.p_inf = p_inf
        self.coef = np.asarray(coefs)
        self.domain = np.asarray(domain)

    def __call__(self, p):
        # Scale to microseconds to keep numerical coefficients stable
        p_us = p * 1e6
        p_inf_us = self.p_inf * 1e6
        diff = p_us - p_inf_us
        # Guard against division by zero at the pole (diff -> 0)
        if isinstance(diff, np.ndarray):
            diff = np.where(np.abs(diff) < 1e-9, -1e-9, diff)
        else:
            if abs(diff) < 1e-9:
                diff = -1e-9
        x = 1.0 / diff
        return self.coef[0] + self.coef[1]*x + self.coef[2]*(x**2) + self.coef[3]*(x**3)


@final
class ProbeEddyFrequencyMap:
    calibration_version = 5
    low_z_threshold = 5.0

    def __init__(self, eddy: ProbeEddy):
        self._eddy = eddy
        self._sensor = eddy._sensor

        self.drive_current = 0
        self.height_range = (math.inf, -math.inf)
        self.freq_range = (math.inf, -math.inf)
        self._ftoh = None
        self._ftoh_high = None
        self._htof = None
        self.raw_data = None

    def _str_to_exact_floatlist(self, str):
        return [float.fromhex(v) for v in str.split(",")]

    def _exact_floatlist_to_str(self, vals):
        return str.join(", ", [float.hex(v) for v in vals])

    def _coefs_to_str(self, coefs):
        return ", ".join([format(c, ".3f") for c in coefs])

    def freq_spread(self) -> float:
        return ((self.freq_range[1] / self.freq_range[0]) - 1.0) * 100.0

    def load_from_config(self, config: ConfigWrapper, drive_current: int):
        calibstr = config.get(f"calibration_{drive_current}", None)
        if calibstr is None:
            self.drive_current = 0
            self._ftoh = None
            self._htof = None
            self.height_range = (math.inf, -math.inf)
            self.freq_range = (math.inf, -math.inf)
            return

        data = pickle.loads(base64.b64decode(calibstr))
        v = data.get("v", None)
        if v is None or v < self.calibration_version:
            self._eddy._log_info(f"Calibration for dc {drive_current} is old ({v}), needs recalibration")
            return False

        ftoh = data.get("ftoh", None)
        ftoh_high = data.get("ftoh_high", None)
        htof = data.get("htof", None)
        dc = data.get("dc", None)
        h_range = data.get("h_range", (math.inf, -math.inf))
        f_range = data.get("f_range", (math.inf, -math.inf))

        if dc != drive_current:
            raise configerror(f"ProbeEddyFrequencyMap: drive current mismatch: loaded {dc} != requested {drive_current}")

        self._ftoh = ftoh
        self._ftoh_high = ftoh_high
        self._htof = htof
        self.height_range = h_range
        self.freq_range = f_range
        self.drive_current = drive_current

        self._eddy._log_info(f"Loaded calibration for drive current {drive_current}")
        return True

    def save_calibration(self):
        if self._ftoh is None or self._htof is None:
            return

        configfile = self._eddy._printer.lookup_object("configfile")
        data = {
            "v": self.calibration_version,
            "ftoh": self._ftoh,
            "ftoh_high": self._ftoh_high,
            "htof": self._htof,
            "h_range": self.height_range,
            "f_range": self.freq_range,
            "dc": self.drive_current,
        }
        calibstr = base64.b64encode(pickle.dumps(data)).decode()
        configfile.set(self._eddy._full_name, f"calibration_{self.drive_current}", calibstr)

    def calibrate_from_values(
        self,
        drive_current: int,
        raw_times: List[float],
        raw_freqs_list: List[float],
        raw_heights_list: List[float],
        raw_vels_list: List[float],
        report_errors: bool,
        write_debug_files: bool,
    ):
        if len(raw_freqs_list) != len(raw_heights_list):
            raise ValueError("freqs and heights must be the same length")

        if len(raw_freqs_list) == 0:
            self._eddy._log_info("calibrate_from_values: empty list")
            return None, None

        # everything must be a np.array or things get confused below
        times = np.asarray(raw_times)
        freqs = np.asarray(raw_freqs_list)
        heights = np.asarray(raw_heights_list)
        vels = np.asarray(raw_vels_list) if raw_vels_list else None

        if write_debug_files:
            with open("/tmp/eddy-calibration.csv", "w") as data_file:
                data_file.write("time,frequency,avg_freq,z,avg_z,v\n")
                for i in range(len(freqs)):
                    s_t = times[i]
                    s_f = freqs[i]
                    s_z = heights[i]
                    s_v = vels[i] if vels is not None else 0.0
                    data_file.write(f"{s_t},{s_f},{s_z},,{s_v}\n")
                self._eddy._log_info(f"Wrote {len(freqs)} samples to /tmp/eddy-calibration.csv")

        if len(freqs) == 0 or len(heights) == 0:
            if report_errors:
                self._eddy._log_error(
                    f"Drive current {drive_current}: Calibration failed, couldn't compute averages ({len(raw_freqs_list)}, {len(raw_heights_list)}), probably due to no valid samples received."
                )
            return None, None

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
                    return None, None

            if min_height > 0.65:  # this is a bit arbitrary; but if it's this far off we shouldn't trust it
                self._eddy._log_error(
                    f"Drive current {drive_current} error: min height for valid samples is too high: {min_height:.3f} > 0.65. Possible causes: bad drive current, bad sensor mount height."
                )
                if not self._eddy.params.allow_unsafe:
                    return None, None

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

        # Fit 3rd-degree rational model for ftoh_low_fn (height as rational function of period)
        periods_us = (1.0 / freqs[low_samples]) * 1e6
        p_max = periods_us.max()
        # Search space for p_inf (free-space period, larger than maximum active period)
        p_inf_grid = np.linspace(p_max * 1.001, p_max * 1.5, 1000)

        best_rmse = np.inf
        best_p_inf = None
        best_beta = None

        for p_inf_candidate in p_inf_grid:
            x = 1.0 / (periods_us - p_inf_candidate)
            M = np.column_stack([np.ones_like(periods_us), x, x**2, x**3])
            beta, residuals, rank, s = np.linalg.lstsq(M, heights[low_samples], rcond=None)
            pred = M @ beta
            rmse = np.sqrt(np.mean((heights[low_samples] - pred)**2))
            if rmse < best_rmse:
                best_rmse = rmse
                best_p_inf = p_inf_candidate
                best_beta = beta

        p_inf_sec = best_p_inf / 1e6
        ftoh_low_fn = ProbeEddyRationalFit(
            p_inf_sec,
            best_beta.tolist(),
            [periods_us.min() / 1e6, periods_us.max() / 1e6]
        )
        htof_low_fn = npp.Polynomial.fit(heights[low_samples], 1.0 / freqs[low_samples], deg=9)

        if np.count_nonzero(high_samples) > 50:
            ftoh_high_fn = npp.Polynomial.fit(1.0 / freqs[high_samples], heights[high_samples], deg=9)
        else:
            self._eddy._log_debug(f"not computing ftoh_high, not enough high samples")
            ftoh_high_fn = None

        # Calculate rms, only for the low values (where error is most relevant)
        rmse_fth = np_rmse(
            ftoh_low_fn,
            1.0 / freqs[low_samples],
            heights[low_samples],
        )
        rmse_htf = np_rmse(
            htof_low_fn,
            heights[low_samples],
            1.0 / freqs[low_samples],
        )

        if report_errors:
            if rmse_fth > 0.050:
                self._eddy._log_error(
                    f"Drive current {drive_current} error: calibration error margin is too high ({rmse_fth:.3f}). Possible causes: bad drive current, bad sensor mount height."
                )
                if not self._eddy.params.allow_unsafe:
                    return None, None

        self._ftoh = ftoh_low_fn
        self._htof = htof_low_fn
        self._ftoh_high = ftoh_high_fn
        self.drive_current = drive_current
        self.height_range = [min_height, max_height]
        self.freq_range = [min_freq, max_freq]

        self._eddy._log_msg(
            f"Drive current {drive_current}: valid height: {min_height:.3f} to {max_height:.3f}, "
            f"freq spread {freq_spread:.2f}% ({min_freq:.1f} - {max_freq:.1f}), "
            f"Fit {rmse_fth:.4f} ({rmse_htf:.2f})"
        )

        if write_debug_files:
            self._write_calibration_plot(
                times,
                freqs,
                heights,
                rmse_fth,
                rmse_htf,
                vels=vels,
            )

        return rmse_fth, rmse_htf

    def _write_calibration_plot(
        self,
        times,
        freqs,
        heights,
        rmse_fth,
        rmse_htf,
        vels=None,
    ):
        if not plotly:
            return

        if self._ftoh is None or self._htof is None:
            logging.warning(f"write_calibration_plot: null calibration?")
            return

        import plotly.graph_objects as go

        low_samples = heights <= ProbeEddyFrequencyMap.low_z_threshold
        high_samples = heights >= ProbeEddyFrequencyMap.low_z_threshold - 0.5

        f_to_z_low_err = heights[low_samples] - self._ftoh(1.0 / freqs[low_samples])

        if self._ftoh_high is not None:
            f_to_z_high_err = heights[high_samples] - self._ftoh_high(1.0 / freqs[high_samples])
        else:
            f_to_z_high_err = None

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=times, y=heights, mode="lines", name="Z"))

        fig.add_trace(
            go.Scatter(
                x=times[low_samples],
                y=self._ftoh(1.0 / freqs[low_samples]),
                mode="lines",
                name=f"Z {rmse_fth:.4f}",
            )
        )

        if self._ftoh_high is not None:
            fig.add_trace(
                go.Scatter(
                    x=times[high_samples],
                    y=self._ftoh_high(1.0 / freqs[high_samples]),
                    mode="lines",
                    name=f"Z (high)",
                )
            )

        fig.add_trace(go.Scatter(x=times, y=freqs, mode="lines", name="F", yaxis="y2"))

        fig.add_trace(
            go.Scatter(
                x=times[low_samples],
                y=1.0 / self._htof(heights[low_samples]),
                mode="lines",
                name=f"F ({rmse_htf:.2f})",
                yaxis="y2",
            )
        )

        fig.add_trace(
            go.Scatter(
                x=times[low_samples],
                y=f_to_z_low_err,
                mode="lines",
                name="Err",
                yaxis="y3",
            )
        )
        if f_to_z_high_err is not None:
            fig.add_trace(
                go.Scatter(
                    x=times[high_samples],
                    y=f_to_z_high_err,
                    mode="lines",
                    name="Err (high)",
                    yaxis="y3",
                )
            )

        if vels is not None:
            fig.add_trace(go.Scatter(x=times, y=vels, mode="lines", name="V", yaxis="y4"))

        fig.update_layout(
            hovermode="x unified",
            title=f"Calibration for drive current {self.drive_current}",
            yaxis2=dict(title="Freq", overlaying="y", tickformat="d", side="right"),
            yaxis3=dict(overlaying="y", side="right", position=0.1),
            yaxis4=dict(overlaying="y", side="right", position=0.2),
        )
        fig.write_html("/tmp/eddy-calibration.html")

    def freq_to_height(self, freq: float) -> float:
        if self._ftoh is None:
            raise self._eddy._printer.command_error("Calling freq_to_height on uncalibrated map")
        invfreq = 1.0 / freq
        if self._ftoh_high is not None and invfreq > self._ftoh.domain[1]:
            invfreq_cap = min(invfreq, self._ftoh_high.domain[1])
            invfreq_cap = max(invfreq_cap, self._ftoh_high.domain[0])
            return float(self._ftoh_high(invfreq_cap))
        invfreq_cap = min(invfreq, self._ftoh.domain[1])
        invfreq_cap = max(invfreq_cap, self._ftoh.domain[0])
        return float(self._ftoh(invfreq_cap))

    def freqs_to_heights_np(self, freqs: np.array) -> np.array:
        if self._ftoh is None:
            raise self._eddy._printer.command_error("Calling freqs_to_heights on uncalibrated map")
        invfreqs = 1.0 / freqs
        if self._ftoh_high is not None:
            heights = np.zeros(len(invfreqs))
            low_freq_vals = invfreqs > self._ftoh.domain[1]

            # High Z values (low frequency)
            high_inv = invfreqs[low_freq_vals]
            if len(high_inv) > 0:
                high_inv_cap = np.clip(high_inv, self._ftoh_high.domain[0], self._ftoh_high.domain[1])
                heights[low_freq_vals] = np.vectorize(self._ftoh_high, otypes=[float])(high_inv_cap)

            # Low Z values (high frequency)
            low_inv = invfreqs[~low_freq_vals]
            if len(low_inv) > 0:
                low_inv_cap = np.clip(low_inv, self._ftoh.domain[0], self._ftoh.domain[1])
                heights[~low_freq_vals] = np.vectorize(self._ftoh, otypes=[float])(low_inv_cap)
        else:
            invfreqs_cap = np.clip(invfreqs, self._ftoh.domain[0], self._ftoh.domain[1])
            heights = self._ftoh(invfreqs_cap)
        return heights

    def height_to_freq(self, height: float) -> float:
        if self._htof is None:
            raise self._eddy._printer.command_error("Calling height_to_freq on uncalibrated map")
        return 1.0 / float(self._htof(height))

    def calibrated(self) -> bool:
        return self._ftoh is not None and self._htof is not None


@final
class BedMeshScanHelper:
    def __init__(self, eddy, config):
        self._eddy = eddy
        self._printer = eddy._printer

        self._bmc = bmc = config.getsection("bed_mesh")
        self._bed_mesh = eddy._printer.load_object(bmc, "bed_mesh")
        self._x_points, self._y_points = bmc.getintlist("probe_count", count=2, note_valid=False)
        self._x_min, self._y_min = bmc.getfloatlist("mesh_min", count=2, note_valid=False)
        self._x_max, self._y_max = bmc.getfloatlist("mesh_max", count=2, note_valid=False)
        self._speed = bmc.getfloat("speed", 100.0, above=0.0, note_valid=False)
        self._scan_z = bmc.getfloat("horizontal_move_z", self._eddy.params.home_trigger_height, above=0.0, note_valid=False)

        self._x_offset = self._eddy.params.x_offset
        self._y_offset = self._eddy.params.y_offset

        self._mesh_points, self._mesh_path = self._generate_path()


    def _generate_path(self):
        x_vals = np.linspace(self._x_min, self._x_max, self._x_points)
        y_vals = np.linspace(self._y_min, self._y_max, self._y_points)
        path = []
        reverse = False

        for y in y_vals:
            row = [(x, y, True) for x in (reversed(x_vals) if reverse else x_vals)]
            path.extend(row)
            reverse = not reverse
        return path, path

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
        # heights is in the order of the _mesh_path points; convert to
        # be ordered min_y..max_y, min_x..max_x, then pull out the heights
        indexed_points = []
        i = 0
        for x, y, include in self._mesh_path:
            if not include:
                continue
            indexed_points.append((x, y, i))
            i += 1

        def sort_points(a, b):
            if a[1] < b[1]: # y first
                return -1
            if a[1] > b[1]:
                return 1
            if a[0] < b[0]: # then x
                return -1
            if a[0] > b[0]:
                return 1
            return 0

        indices = [ki for _, _, ki in sorted(indexed_points, key=cmp_to_key(sort_points))]

        if len(indices) != self._x_points * self._y_points or len(heights) < len(indices):
            raise self._printer.command_error(
                f"EDDYng mesh: scanned {len(heights)} heights for a {self._x_points}x{self._y_points} "
                f"mesh ({len(indices)} path points)"
            )

        ki = 0
        matrix = []
        for _ in range(self._y_points):
            row = []
            for _ in range(self._x_points):
                v = heights[indices[ki]]
                row.append(self._scan_z - v)
                ki += 1
            matrix.append(row)

        params = self._bed_mesh.bmc.mesh_config.copy()
        params.update({
            "min_x": self._x_min,
            "max_x": self._x_max,
            "min_y": self._y_min,
            "max_y": self._y_max,
            "x_count": self._x_points,
            "y_count": self._y_points,
        })
        mesh = bed_mesh.ZMesh(params, None)
        try:
            mesh.build_mesh(matrix)
        except bed_mesh.BedMeshError as e:
            raise self._printer.command_error(str(e))
        self._bed_mesh.set_mesh(mesh)
        self._eddy._log_msg("Mesh scan complete")

    def _reset_mesh_area(self):
        """Restore the configured [bed_mesh] area and rebuild the scan path from it."""
        self._x_points, self._y_points = self._bmc.getintlist("probe_count", count=2, note_valid=False)
        self._x_min, self._y_min = self._bmc.getfloatlist("mesh_min", count=2, note_valid=False)
        self._x_max, self._y_max = self._bmc.getfloatlist("mesh_max", count=2, note_valid=False)
        self._mesh_points, self._mesh_path = self._generate_path()

    def _adaptive_mesh(self, gcmd: GCodeCommand):
        # IMPORTANT: this helper object lives as long as Klipper does, so everything that
        # scan() uses (point counts, bounds *and* the generated path) must be recomputed on
        # every call. Previously the early returns below left the path from the previous
        # (adaptive) print in place while the counts/bounds were reset to the config values,
        # which made _set_bed_mesh() raise IndexError -> "Internal error" -> printer shutdown.
        self._reset_mesh_area()

        if not gcmd.get_int("ADAPTIVE", 0):
            return

        exclude_objects = self._printer.lookup_object("exclude_object", None)
        if exclude_objects is None:
            self._eddy._log_msg("Exclude objects not enabled. Using full mesh...")
            return

        objects = exclude_objects.get_status().get("objects", [])
        points = [pt for obj in objects for pt in obj.get("polygon", [])]
        if not points:
            self._eddy._log_msg("No objects defined for adaptive mesh. Using full mesh...")
            return

        config_mesh_min = (self._x_min, self._y_min)
        config_mesh_max = (self._x_max, self._y_max)
        config_x_count, config_y_count = self._x_points, self._y_points

        margin = gcmd.get_float("ADAPTIVE_MARGIN", 5.0)

        # Bounds of the printed area (+ margin), clamped to the configured mesh area
        adjusted_mesh_min = [
            max(min(p[0] for p in points) - margin, config_mesh_min[0]),
            max(min(p[1] for p in points) - margin, config_mesh_min[1]),
        ]
        adjusted_mesh_max = [
            min(max(p[0] for p in points) + margin, config_mesh_max[0]),
            min(max(p[1] for p in points) + margin, config_mesh_max[1]),
        ]

        # Objects entirely outside of the mesh area (or a degenerate area) -> full mesh
        if adjusted_mesh_max[0] <= adjusted_mesh_min[0] or adjusted_mesh_max[1] <= adjusted_mesh_min[1]:
            self._eddy._log_msg("Adaptive mesh area is empty. Using full mesh...")
            return

        # Scale the probe counts by the ratio of adapted to configured area
        ratio_x = (adjusted_mesh_max[0] - adjusted_mesh_min[0]) / (config_mesh_max[0] - config_mesh_min[0])
        ratio_y = (adjusted_mesh_max[1] - adjusted_mesh_min[1]) / (config_mesh_max[1] - config_mesh_min[1])
        new_x_probe_count = int(math.ceil(config_x_count * ratio_x))
        new_y_probe_count = int(math.ceil(config_y_count * ratio_y))

        # Same rule as Klipper's adaptive bed mesh: at least 3 points per axis, 4 if the
        # other axis needs more than 6 (so that the interpolation algorithm is happy).
        min_num_of_probes = 3
        if max(new_x_probe_count, new_y_probe_count) > 6 and min(new_x_probe_count, new_y_probe_count) < 4:
            min_num_of_probes = 4

        new_x_probe_count = min(max(min_num_of_probes, new_x_probe_count), config_x_count)
        new_y_probe_count = min(max(min_num_of_probes, new_y_probe_count), config_y_count)

        self._x_points, self._y_points = new_x_probe_count, new_y_probe_count
        self._x_min, self._y_min = adjusted_mesh_min
        self._x_max, self._y_max = adjusted_mesh_max
        self._mesh_points, self._mesh_path = self._generate_path()

        self._eddy._log_msg(
            f"Adaptive mesh: {self._x_points}x{self._y_points} points, "
            f"({self._x_min:.1f},{self._y_min:.1f}) - ({self._x_max:.1f},{self._y_max:.1f})"
        )

    def scan(self, gcmd: GCodeCommand):
        th = self._eddy._toolhead

        self._adaptive_mesh(gcmd)

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
            sampler.wait_for_sample_at_time(path_times[-1] + sample_time*2.)
            sampler.finish()

            heights = sampler.find_heights_at_times([(t - sample_time/2., t + sample_time/2.) for t in path_times])
            # Note plus tap_offset here, vs -tap_offset when probing. These are actual
            # heights, the other is "offset from real"
            heights = [h + self._eddy._tap_offset for h in heights]

            with open("/tmp/mesh.csv", "w") as mfile:
                mfile.write("time,x,y,z\n")
                for i in range(len(self._mesh_points)):
                    t = path_times[i]
                    x = self._mesh_points[i][0]
                    y = self._mesh_points[i][1]
                    z = heights[i]
                    mfile.write(f"{t},{x},{y},{z}\n")

            self._set_bed_mesh(heights)


def np_rmse(p, x, y):
    y_hat = p(x)
    return np.sqrt(np.mean((y - y_hat) ** 2))


def bed_mesh_ProbeManager_start_probe_override(self, gcmd):
    method = gcmd.get("METHOD", "automatic").lower()
    can_scan = False
    pprobe = self.printer.lookup_object("probe", None)
    if pprobe is not None:
        probe_name = pprobe.get_status(None).get("name", "")
        can_scan = "eddy" in probe_name
    if method == "rapid_scan" and can_scan:
        self.rapid_scan_helper.perform_rapid_scan(gcmd)
    else:
        self.probe_helper.start_probe(gcmd)


def load_config_prefix(config: ConfigWrapper):
    return ProbeEddy(config)
