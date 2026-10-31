# Support for reading frequency samples from ldc1612 (v2)
#
# Copyright (C) 2020-2024  Kevin O'Connor <kevin@koconnor.net>
# Copyright (C) 2025  Vladimir Vukicevic <vladimir@pobox.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import math
import logging
import struct
import threading
from dataclasses import dataclass
from typing import List, Optional
import numpy as np

try:
    from klippy.extras import bus
    from klippy.printer import Printer

    IS_KALICO = True
except ImportError:
    from . import bus
    from klippy import Printer

    IS_KALICO = False

MIN_MSG_TIME = 0.100

BATCH_UPDATES = 0.100

LDC1612_ADDR = 0x2A

# TODO: configure these as part of calibration
DEGLITCH_1_0MHZ = 0x01
DEGLITCH_3_3MHZ = 0x04
DEGLITCH_10MHZ = 0x05
DEGLITCH_33MHZ = 0x07

LDC1612_MANUF_ID = 0x5449
LDC1612_DEV_ID = 0x3055

REG_RCOUNT0 = 0x08
REG_OFFSET0 = 0x0C
REG_SETTLECOUNT0 = 0x10
REG_CLOCK_DIVIDERS0 = 0x14
REG_ERROR_CONFIG = 0x19
REG_CONFIG = 0x1A
REG_MUX_CONFIG = 0x1B
REG_RESET_DEV = 0x1C
REG_DRIVE_CURRENT0 = 0x1E
REG_MANUFACTURER_ID = 0x7E
REG_DEVICE_ID = 0x7F

# Device product (match sensor_ldc1612_ng.c)
PRODUCT_UNKNOWN = 0
PRODUCT_BTT_EDDY = 1
PRODUCT_CARTOGRAPHER = 2
PRODUCT_MELLOW_FLY = 3
PRODUCT_LDC1612_INTERNAL_CLK = 4

HOME_MODE_NONE = 0
HOME_MODE_HOME = 1
HOME_MODE_WMA = 2
HOME_MODE_SOS = 3

# should match sensor_ldc1612_ng.c
START_FLAG_SEND_FILTER_VALUE = 1 << 0

# ldc1612_ng_data sample layouts by fmt: mcu clock, raw value[, filter value]
DATA_FORMATS = {
    0: "<II",
    1: "<IIf",
}


@dataclass
class LDC1612_ng_value:
    status: int
    freqval: int
    freq: float


@dataclass
class LDC1612_ng_homing_result:
    trigger_time: float
    tap_start_time: float
    error: int

@dataclass
class LDC1612_ng_sample_data:
    times: list[float]
    raw_freqs: list[int]
    accum_vals: list[float]


# The mcu timestamps each sample when it reads it, which is some time after
# the ldc1612 finished the conversion: we poll the status register, so the
# delay varies between ~0 and the poll interval, plus task latency. The
# conversions themselves happen at exact intervals of the sensor's reference
# clock, so the real conversion times are t0 + n * period. Neither t0 nor
# period is known precisely in mcu time (sensor and mcu clocks differ by some
# ppm, and much more with the internal oscillator), so fit them from the read
# times. The slope comes from a least-squares fit; since a read can never
# happen before its conversion completes, t0 comes from the lower envelope of
# the read times rather than from the (always late) mean.
class ConversionTimeFit:
    MIN_SAMPLES = 16
    # Keep the window short enough that clock drift within it is linear
    MAX_SAMPLES = 2000
    # The ldc1612 has no FIFO, so a read always returns the latest completed
    # conversion: the read delay is in [0, period). Assign a read to the latest
    # conversion that completed before it, with a small margin for fit error.
    INDEX_BIAS = 0.02
    # The lower envelope is taken as this quantile of the read offsets rather
    # than the minimum, so that a rare read delayed by almost a full period
    # (and so assigned to the following conversion) can't drag it earlier.
    # Normal reads cover [0, poll interval] about evenly, so this adds only
    # ~1% of the poll interval of bias.
    ENVELOPE_QUANTILE = 0.01
    # Normal read delays are under one poll interval (< 0.5 periods); reads
    # delayed more than this (task latency spikes) are left out of the slope fit
    MAX_INLIER_DELAY = 0.6
    # If more reads than this are outside MAX_INLIER_DELAY, the fit is wrong
    # (most likely the conversion indices slipped); don't use it
    MAX_OUTLIER_FRACTION = 0.1
    # If the low end of a batch's read delays (this quantile, less sensitive to
    # latency spikes than the envelope one) moves by more than MAX_PHASE_DIFF
    # periods relative to the running fit, the conversion phase changed (e.g.
    # the chip was reconfigured); start over from that batch. Smaller shifts
    # are absorbed by the running fit as new reads replace old ones.
    PHASE_QUANTILE = 0.05
    MAX_PHASE_DIFF = 0.1

    def __init__(self, center_offset: float):
        # time from the middle of the conversion's integration window to its completion
        self._center_offset = center_offset
        self._reads: list[float] = []
        self._t0: Optional[float] = None
        self._period: Optional[float] = None

    def add_reads(self, read_times: list[float]) -> list[float]:
        if len(read_times) >= self.MIN_SAMPLES and abs(self._phase_shift(read_times)) > self.MAX_PHASE_DIFF:
            self._reads = []
            self._t0 = self._period = None

        self._reads.extend(read_times)
        del self._reads[: -self.MAX_SAMPLES]
        if len(self._reads) >= self.MIN_SAMPLES:
            r = np.asarray(self._reads)
            if self._period is None:
                fit = self._initial_fit(r)
                if fit is None:
                    # don't let a bad stretch of reads block every later attempt
                    self._reads = list(read_times)
            else:
                fit = self._refine(r, self._t0, self._period)
            if fit is not None:
                self._t0, self._period = fit[:2]
        return [self.to_sample_time(t) for t in read_times]

    def to_sample_time(self, read_time: float) -> float:
        """Map a mcu read time to the middle of that sample's conversion window"""
        if self._period is None:
            return read_time
        n = math.floor((read_time - self._t0) / self._period + self.INDEX_BIAS)
        return self._t0 + n * self._period - self._center_offset

    def _phase_shift(self, read_times: list[float]) -> float:
        if self._period is None:
            return 0.0
        # read delays relative to the running fit, in periods, unwrapped to
        # [-0.3, 0.7); normally they start at ~0
        delay = ((np.asarray(read_times) - self._t0) / self._period + 0.3) % 1.0 - 0.3
        return float(np.quantile(delay, self.PHASE_QUANTILE))

    def _initial_fit(self, r: np.ndarray):
        # Number of conversions over the span of the reads. Reads only happen on
        # poll ticks and dropped conversions show up as ~2x gaps, so count each
        # gap; a read delayed by latency can still make this off by one or two,
        # so try nearby counts and keep the one that fits best.
        span = r[-1] - r[0]
        if span <= 0.0:
            return None
        gaps = np.diff(r)
        period = span / (len(r) - 1)
        for _ in range(2):
            count = np.round(gaps / period).sum()
            if count <= 0:
                return None
            period = span / count

        best = None
        for c in range(int(count) - 2, int(count) + 3):
            if c <= 0:
                continue
            period = span / c
            # index relative to the first read, so a delayed read only
            # misplaces itself rather than every read after it
            n = np.round((r - r[0]) / period)
            offsets = r - period * n
            t0 = float(np.quantile(offsets[offsets > np.median(offsets) - 0.5 * period], self.ENVELOPE_QUANTILE))
            fit = self._refine(r, t0, period)
            if fit is not None and (best is None or fit[2] < best[2]):
                best = fit
        return best

    def _refine(self, r: np.ndarray, t0: float, period: float):
        for _ in range(3):
            n = np.floor((r - t0) / period + self.INDEX_BIAS)
            delay = (r - t0 - period * n) / period
            inliers = delay < self.MAX_INLIER_DELAY
            if np.count_nonzero(inliers) < self.MIN_SAMPLES:
                return None
            period = float(np.polyfit(n[inliers], r[inliers], 1)[0])
            if period <= 0.0:
                return None
            # reads never happen before the conversion completes, so the
            # conversion times are the lower envelope of the read times
            t0 = float(np.quantile((r - period * n)[inliers], self.ENVELOPE_QUANTILE))

        n = np.floor((r - t0) / period + self.INDEX_BIAS)
        delay = (r - t0 - period * n) / period
        outliers = np.count_nonzero(delay >= self.MAX_INLIER_DELAY)
        if outliers > self.MAX_OUTLIER_FRACTION * len(r):
            return None
        return t0, period, outliers


# Interface class to LDC1612 mcu support
class LDC1612_ng:
    def __init__(self, config, name: str | None = None, primary: bool = True):
        self.printer: Printer = config.get_printer()

        self._name = config.get_name().split()[-1]
        self._verbose = config.getboolean("debug", False)

        device_choices = {
            "ldc1612": PRODUCT_UNKNOWN,
            "btt_eddy": PRODUCT_BTT_EDDY,
            "cartographer": PRODUCT_CARTOGRAPHER,
            "mellow_fly": PRODUCT_MELLOW_FLY,
            "ldc1612_internal_clk": PRODUCT_LDC1612_INTERNAL_CLK,
        }
        self._device_product = config.getchoice("sensor_type", device_choices, PRODUCT_UNKNOWN)

        # Fin0 = Fsensor0 / FIN_DIVIDER0
        # Fref0 = Fclk / FREF_DIVIDER0
        if self._device_product == PRODUCT_CARTOGRAPHER:
            self._ldc_freq_clk = 24_000_000
            self._ldc_settle_time = 0.0001706
            self._ldc_fin_divider = 1
            self._ldc_fref_divider = 1
            self._default_drive_current = 26
            self._drive_current_range = (26, 26)
        elif self._device_product == PRODUCT_MELLOW_FLY:
            self._ldc_freq_clk = 40_000_000
            self._ldc_settle_time = 0.00125
            self._ldc_fin_divider = 1
            self._ldc_fref_divider = 3
            self._default_drive_current = 15
            self._drive_current_range = (14, 17)
        elif self._device_product == PRODUCT_LDC1612_INTERNAL_CLK:
            # A generic setup that usees internal LDC1612 clock
            # using LDC1612 internal typical clock frequency 43.4MHz
            self._ldc_freq_clk = 43_400_000
            self._ldc_settle_time = 0.00125
            self._ldc_fin_divider = 1
            self._ldc_fref_divider = 1
            self._default_drive_current = 15
            self._drive_current_range = (14, 20)
        else:  # Generic/BTT Eddy using external 12MHz clock source
            self._ldc_freq_clk = 12_000_000
            self._ldc_settle_time = 0.005
            self._ldc_fin_divider = 1
            self._ldc_fref_divider = 2
            self._default_drive_current = 15
            self._drive_current_range = (14, 20)

        # allow overriding these
        self._ldc_fref_divider = config.getint("ldc_fref_divider", self._ldc_fref_divider)
        self._ldc_fin_divider = config.getint("ldc_fin_divider", self._ldc_fin_divider)

        self._ldc_freq_ref = self._ldc_freq_clk / self._ldc_fref_divider
        self._freq_conv = self._ldc_fin_divider * self._ldc_freq_ref

        drive_current: int = config.getint("reg_drive_current", 0, minval=0, maxval=31)
        saved_drive_current: int = config.getint("saved_reg_drive_current", 0, minval=0, maxval=31)
        if drive_current == 0:
            drive_current = saved_drive_current
        if drive_current == 0:
            drive_current = self._default_drive_current
        self._drive_current = drive_current

        self._deglitch: str = config.get("ldc_deglitch", "default").lower()
        self._data_rate: int = config.getint("samples_per_second", 250, minval=50)
        self._ldc_settle_time = min(self._ldc_settle_time, 1.0 / self._data_rate)
        self._ldc_settle_time = config.getfloat("ldc_settle_time", self._ldc_settle_time)
        # Frequency offset (in Hz) the chip subtracts before conversion; stored
        # as the OFFSET0 register value, which the conversions below account for
        self._ldc_offset_reg: int = self._offset_hz_to_reg(config.getint("ldc_offset", 0), config.error)

        # Setup mcu sensor_ldc1612 bulk query code
        self._i2c = bus.MCU_I2C_from_config(config, default_addr=LDC1612_ADDR, default_speed=400000)
        self._mcu = mcu = self._i2c.get_mcu()
        self._oid = oid = mcu.create_oid()
        self._data_lock = threading.Lock()
        self._pending_data = []
        self._time_fit: Optional[ConversionTimeFit] = None

        logging.info(f"LDC1612ng {self._name} oid: {oid} i2c_oid {self._i2c.get_oid()}")

        # params ending in "_pin" are magic and are a %s on this side, but %c on
        # the native side. There doesn't seem to be a way to pass "no pin". So we
        # need two separate config commands.
        if config.get("intb_pin", None) is not None:
            ppins = config.get_printer().lookup_object("pins")
            pin_params = ppins.lookup_pin(config.get("intb_pin"))
            if pin_params["chip"] != mcu:
                raise config.error("ldc1612 intb_pin must be on same mcu")
            mcu.add_config_cmd(
                "config_ldc1612_ng_with_intb oid=%d i2c_oid=%d product=%i in_div=%hu ref_div=%hu intb_pin=%s"
                % (
                    oid,
                    self._i2c.get_oid(),
                    self._device_product,
                        self._ldc_fin_divider, self._ldc_fref_divider,
                    pin_params["pin"],
                )
            )
        else:
            mcu.add_config_cmd("config_ldc1612_ng oid=%d i2c_oid=%d product=%i in_div=%hu ref_div=%hu" % (oid, self._i2c.get_oid(), self._device_product, self._ldc_fin_divider, self._ldc_fref_divider))

        # Make sure the sensor is stopped on restart
        mcu.add_config_cmd(
            "ldc1612_ng_start_stop oid=%d rest_ticks=0 flags=0" % (oid,),
            on_restart=True,
        )
        mcu.register_config_callback(self._build_config)

        self._chip_initialized = False
        self._data_callback = None
        self._data_timer = None

        gcode = self.printer.lookup_object("gcode")

        names = [name]
        if primary:
            names.append(None)
        for name in names:
            gcode.register_mux_command(
                "LDC_NG_CALIBRATE_DRIVE_CURRENT",
                "CHIP",
                name,
                self.cmd_LDC_CALIBRATE,
                desc=self.cmd_LDC_CALIBRATE_help,
            )
            gcode.register_mux_command(
                "LDC_NG_SET_DRIVE_CURRENT",
                "CHIP",
                name,
                self.cmd_LDC_SET_DC,
                desc=self.cmd_LDC_SET_DC_help,
            )
            gcode.register_mux_command("LDC_NG_SET", "CHIP", name, self.cmd_LDC_SET, desc="Set various LDC config values")
            gcode.register_mux_command("LDC_NG_DUMP", "CHIP", name, self.cmd_LDC_DUMP, desc="Dump LDC state")
            gcode.register_mux_command("LDC_NG_RESET", "CHIP", name, self.cmd_LDC_RESET, desc="Reset LDC")

    cmd_LDC_SET_DC_help = "Set LDC1612 DRIVE_CURRENT register (idrive value only)"

    def cmd_LDC_SET_DC(self, gcmd):
        drive_cur = gcmd.get_int("VAL", minval=0, maxval=31)
        self.set_drive_current(drive_cur)

    cmd_LDC_CALIBRATE_help = "Calibrate LDC1612 DRIVE_CURRENT register"

    def cmd_LDC_CALIBRATE(self, gcmd):
        self.start_streaming(lambda _: _)
        toolhead = self.printer.lookup_object("toolhead")
        toolhead.dwell(0.100)
        toolhead.wait_moves()

        old_config = self.read_reg(REG_CONFIG)
        if self._device_product == PRODUCT_LDC1612_INTERNAL_CLK:
            self.set_reg(REG_CONFIG, 0x001)
        else:
            self.set_reg(REG_CONFIG, 0x001 | (1 << 9))

        toolhead.dwell(0.100)
        toolhead.wait_moves()
        reg_drive_current0 = self.read_reg(REG_DRIVE_CURRENT0)
        self.stop_streaming()

        self.set_reg(REG_CONFIG, old_config)
        drive_cur = (reg_drive_current0 >> 6) & 0x1F
        gcmd.respond_info(
            f"{self._name}: Estimated reg_drive_current: {drive_cur}\n"
            "Add this to your config as either reg_drive_current or tap_drive_current\n"
            "then restart."
        )

    def cmd_LDC_RESET(self, gcmd):
        if self._data_timer is not None:
            gcmd.respond_info(f"Streaming active, stopping before reset")
            self.stop_streaming()

        self.set_reg(REG_RESET_DEV, 0x0001)

        reactor = self.printer.get_reactor()
        reactor.pause(reactor.monotonic() + 0.500)
        self._init_chip(force=True)
        gcmd.respond_info(f"Reset complete")

    def _build_config(self):
        cmdqueue = self._i2c.get_command_queue()

        # Older firmware silently accepts the new config command (extra params are
        # ignored) but never sends ldc1612_ng_data, so check for it explicitly,
        # along with the most recent protocol change.
        for cmd in (
            "config_ldc1612_ng oid=%c i2c_oid=%c product=%i in_div=%hu ref_div=%hu",
            "ldc1612_ng_start_stop oid=%c rest_ticks=%u flags=%c",
        ):
            if self._mcu.try_lookup_command(cmd) is None:
                raise self.printer.config_error(
                    f"LDC1612ng {self._name}: sensor firmware is out of date for this version of eddy-ng. Please rebuild and flash the sensor firmware."
                )

        self._ldc1612_ng_start_stop_cmd = self._mcu.lookup_command("ldc1612_ng_start_stop oid=%c rest_ticks=%u flags=%c", cq=cmdqueue)

        self._ldc1612_ng_latched_status_cmd = self._mcu.lookup_query_command(
            "query_ldc1612_ng_latched_status_v2 oid=%c",
            "ldc1612_ng_latched_status oid=%c status=%u lastval=%u",
            oid=self._oid,
            cq=cmdqueue,
        )

        self._ldc1612_ng_setup_home_cmd = self._mcu.lookup_command(
            "ldc1612_ng_setup_home oid=%c"
            " trsync_oid=%c trigger_reason=%c other_reason_base=%c"
            " trigger_freq=%u start_freq=%u start_time=%u mode=%c tap_threshold=%i"
            " err_max=%c",
            cq=cmdqueue,
        )

        self._ldc1612_ng_finish_home_cmd = self._mcu.lookup_query_command(
            "ldc1612_ng_finish_home oid=%c",
            "ldc1612_ng_finish_home_reply oid=%c trigger_clock=%u tap_start_clock=%u error=%u",
            oid=self._oid,
            cq=cmdqueue,
        )

        self._ldc1612_ng_set_sos_section = self._mcu.lookup_command(
            "ldc1612_ng_set_sos_section oid=%c section=%c values=%*s",
            cq=cmdqueue,
        )

        self._mcu.register_response(self._handle_data, "ldc1612_ng_data", self._oid)

        if hasattr(self._mcu, "register_serial_response"):
            # infuriating: these used to be able to be registered for optional
            # things (that the firmware never sends)
            #self._mcu.register_serial_response(self._handle_debug_print, "debug_print m=%*s")
            pass
        else:
            self._mcu.register_response(self._handle_debug_print, "debug_print")

    def _handle_debug_print(self, params):
        logging.info(params["m"])

    def _clock32_to_print_time(self, clock) -> float:
        return self._mcu.clock_to_print_time(self._mcu.clock32_to_clock64(clock))

    def get_mcu(self):
        return self._i2c.get_mcu()

    def read_reg(self, reg):
        params = self._i2c.i2c_read([reg], 2)
        response = bytearray(params["response"])
        return (response[0] << 8) | response[1]

    def set_reg(self, reg, val, minclock=0):
        logging.info(f"set_reg: {reg:2x} {val:8x}")
        self._i2c.i2c_write([reg, (val >> 8) & 0xFF, val & 0xFF], minclock=minclock)

    def latched_status(self):
        response = self._ldc1612_ng_latched_status_cmd.send([self._oid])
        return response["status"]

    def latched_status_str(self):
        s = self.latched_status()
        return self.status_to_str(s)

    def status_to_str(self, s: int):
        status_bits = [
            "UNREADONCV3",
            "UNREADCONV2",
            "UNREADCONV1",
            "UNREADCONV0",
            "4",
            "5",
            "DRDY",
            "7",
            "ERR_ZC",
            "ERR_ALE",
            "ERR_AHE",
            "ERR_WD",
            "ERR_OR",
            "ERR_UR",
            "ERR_CHBIT0",
            "ERR_CHBIT1",
        ]
        flags = []
        for bit, flag in enumerate(status_bits):
            if s & (1 << bit):
                flags.append(flag)
        return " ".join(flags)

    def data_error_to_str(self, d: int):
        err_bits = [
            "Under-range",
            "Over-range",
            "Watchdog",
            "Amplitude",
        ]
        d = d >> 28
        if d == 0:
            return ""
        errors = []
        for bit, err in enumerate(err_bits):
            if d & (1 << bit):
                errors.append(err)
        return "[" + " ".join(errors) + "]"

    def read_one_value(self):
        self._init_chip()
        # the status command also returns the last value
        res = self._ldc1612_ng_latched_status_cmd.send([self._oid])
        status = res["status"]
        lastval = res["lastval"]
        freq = self.from_ldc_freqval(lastval) if lastval <= 0x0FFFFFFF else 0.0

        return LDC1612_ng_value(
            status=status,
            freqval=lastval,
            freq=freq,
        )

    # fsensor = freq_conv * (DATA / 2^28 + OFFSET / 2^16)
    def _offset_hz_to_reg(self, offset_hz, error):
        reg = int(offset_hz * (1 << 16) / self._freq_conv + 0.5)
        if not 0 <= reg <= 0xFFFF:
            raise error(f"ldc_offset {offset_hz} out of range (max {self._freq_conv * 0xFFFF / (1 << 16):.0f})")
        return reg

    def to_ldc_freqval(self, freq):
        return int(freq * (1 << 28) / self._freq_conv - (self._ldc_offset_reg << 12) + 0.5)

    def from_ldc_freqval(self, val, ignore_err=False):
        if (val & 0xF0000000) != 0 and not ignore_err:
            raise self.printer.command_error(f"LDC1612 frequency value has error bits: {hex(val)}")
        return round((val + (self._ldc_offset_reg << 12)) * self._freq_conv / (1 << 28), 3)

    def ldc_freqval_error(self, val):
        return val >> 28

    #
    # Homing
    #
    def setup_home(
        self,
        trsync_oid: int,
        hit_reason: int,
        other_reason_base: int,
        trigger_freq: float,
        start_freq: float,
        start_time: float,
        mode: str = "home",
        tap_threshold: Optional[float] = None,
        max_errors: int = 0,
    ):
        MODES = {
            "home": HOME_MODE_HOME,
            "sos": HOME_MODE_SOS,
        }
        mode_val = MODES.get(mode.lower(), None)
        if mode_val is None:
            raise self.printer.command_error(f"Invalid mode: {mode}")

        t_freqvl = self.to_ldc_freqval(trigger_freq)
        s_freqval = self.to_ldc_freqval(start_freq)
        start_time_mcu = self._mcu.print_time_to_clock(start_time) if start_time > 0 else 0
        tap_threshold_val = int(tap_threshold * 65536.0) if tap_threshold is not None else 0

        if self._verbose:
            logging.info(
                f"LDC1612_ng setup_home: {mode} trigger: {trigger_freq:.2f} ({t_freqvl}) "
                f"safe: {start_freq:.2f} ({s_freqval}) @ {start_time:.2f} ({start_time_mcu}) "
                f"trsync: {trsync_oid} {hit_reason} {other_reason_base} TAP: {tap_threshold} ({tap_threshold_val:x})"
            )

        self._ldc1612_ng_setup_home_cmd.send(
            [
                self._oid,
                trsync_oid,
                hit_reason,
                other_reason_base,
                t_freqvl,
                s_freqval,
                start_time_mcu,
                mode_val,
                tap_threshold_val,
                max_errors,
            ]
        )

    def _convert_clock(self, c):
        if c == 0:
            return 0
        t = self._clock32_to_print_time(c)
        # these are read times of the samples that triggered; put them on the
        # same timebase as the streamed sample data
        if self._time_fit is not None:
            t = self._time_fit.to_sample_time(t)
        return t

    def finish_home(self):
        # "ldc1612_finish_home2_reply oid=%c homing=%c trigger_clock=%u tap_start_clock=%u",
        reply = self._ldc1612_ng_finish_home_cmd.send([self._oid])
        trigger_clock = reply["trigger_clock"]
        tap_start_clock = reply["tap_start_clock"]
        error = reply["error"]
        trigger_time = self._convert_clock(trigger_clock)
        tap_start_time = self._convert_clock(tap_start_clock)

        return LDC1612_ng_homing_result(trigger_time, tap_start_time, error)

    def set_sos_section(self, sect_num: int, sect_vals: List[float]):
        # pack sect_vals into a byte array using struct.pack
        sect_bytes = [b for b in struct.pack("<6f", *sect_vals)]
        self._ldc1612_ng_set_sos_section.send([self._oid, sect_num, sect_bytes])

    # The value that should be added to freqvals and multiplied by
    # to get an actual frequency
    def freqval_conversion_values(self):
        return self._ldc_offset_reg << 12, self._freq_conv / (1 << 28)

    def _verify_chip(self):
        # In case of miswiring, testing LDC1612 device ID prevents treating
        # noise or wrong signal as a correctly initialized device
        manuf_id = self.read_reg(REG_MANUFACTURER_ID)
        dev_id = self.read_reg(REG_DEVICE_ID)
        if manuf_id != LDC1612_MANUF_ID or dev_id != LDC1612_DEV_ID:
            raise self.printer.command_error(
                "Invalid ldc1612 id (got %x,%x vs %x,%x).\n"
                "This is generally indicative of connection problems\n"
                "(e.g. faulty wiring) or a faulty ldc1612 chip." % (manuf_id, dev_id, LDC1612_MANUF_ID, LDC1612_DEV_ID)
            )

    def cmd_LDC_DUMP(self, gcmd):
        manuf_id = self.read_reg(REG_MANUFACTURER_ID)
        dev_id = self.read_reg(REG_DEVICE_ID)
        reg_mux_config = self.read_reg(REG_MUX_CONFIG)
        reg_settlecount = self.read_reg(REG_SETTLECOUNT0)
        reg_rcount = self.read_reg(REG_RCOUNT0)
        reg_offset = self.read_reg(REG_OFFSET0)

        deglitch_now = self.get_deglitch()
        if deglitch_now == DEGLITCH_1_0MHZ:
            deglitch_now = "1mhz"
        if deglitch_now == DEGLITCH_3_3MHZ:
            deglitch_now = "3.3mhz"
        if deglitch_now == DEGLITCH_10MHZ:
            deglitch_now = "10mhz"
        if deglitch_now == DEGLITCH_33MHZ:
            deglitch_now = "33mhz"

        offset_now = round(reg_offset / (1 << 16) * self._freq_conv)
        settle_now = reg_settlecount * 16.0 / self._ldc_freq_ref * 1000.0
        rcount_now = self.get_rcount_sec() * 1000.0

        gcmd.respond_info(f"LDC: {manuf_id:04x}:{dev_id:04x} Mux config: 0x{reg_mux_config:08x} ldc_freq_ref: {self._ldc_freq_ref}")
        gcmd.respond_info(f"Offset: {offset_now} (0x{reg_offset:8x}) settle time: {settle_now:.6f}ms (0x{reg_settlecount:8x}) rcount: {rcount_now:.6f}ms (0x{reg_rcount:8x}) deglitch: {deglitch_now}")

    def cmd_LDC_SET(self, gcmd):
        offset = gcmd.get_float("OFFSET", None)
        rcount = gcmd.get_float("RCOUNT", None, above=0.0)
        deglitch = gcmd.get("DEGLITCH", None)
        settle = gcmd.get_float("SETTLE", None, above=0.0)

        if deglitch is not None:
            if deglitch == "1mhz":
                deglitch = DEGLITCH_1_0MHZ
            elif deglitch == "3.3mhz":
                deglitch = DEGLITCH_3_3MHZ
            elif deglitch == "10mhz":
                deglitch = DEGLITCH_10MHZ
            elif deglitch == "33mhz":
                deglitch = DEGLITCH_33MHZ
            else:
                raise self.printer.command_error(f"Invalid deglitch value: {deglitch}")
            self.set_reg(REG_MUX_CONFIG, 0x0208 | deglitch)

        if offset is not None:
            self._ldc_offset_reg = self._offset_hz_to_reg(offset, self.printer.command_error)
            self.set_reg(REG_OFFSET0, self._ldc_offset_reg)

        if settle is not None:
            self.set_reg(REG_SETTLECOUNT0, int((settle / 1000.0) * self._ldc_freq_ref / 16.0 + 0.5))

        if rcount is not None:
            self.set_reg(REG_RCOUNT0, int((rcount / 1000.0) * self._ldc_freq_ref / 16.0 + 0.5))

        self.cmd_LDC_DUMP(gcmd)

    def get_rcount_sec(self):
        return self.read_reg(REG_RCOUNT0) * 16.0 / self._ldc_freq_ref

    def _init_chip(self, *, force: bool = False):
        if self._chip_initialized and not force:
            return

        self._verify_chip()

        # TODO: have a max_frequency and pick the best deglitch for it
        if self._deglitch == "1mhz":
            deglitch = DEGLITCH_1_0MHZ
        elif self._deglitch == "3.3mhz":
            deglitch = DEGLITCH_3_3MHZ
        elif self._deglitch == "10mhz" or self._deglitch == "default":
            deglitch = DEGLITCH_10MHZ
        elif self._deglitch == "33mhz":
            deglitch = DEGLITCH_33MHZ
        else:
            raise self.printer.error(f"Invalid {self._name} deglitch value: {self._deglitch}")

        # This is the TI-recommended register configuration order
        # Setup chip in requested query rate
        rcount0 = self._ldc_freq_ref / (16.0 * (self._data_rate - 4))

        self.set_reg(REG_RCOUNT0, int(rcount0 + 0.5))
        self.set_reg(REG_OFFSET0, self._ldc_offset_reg)
        self.set_reg(
            REG_SETTLECOUNT0,
            int(self._ldc_settle_time * self._ldc_freq_ref / 16.0 + 0.5),
        )
        self.set_reg(
            REG_CLOCK_DIVIDERS0,
            (self._ldc_fin_divider << 12) | (self._ldc_fref_divider),
        )
        self.set_reg(REG_ERROR_CONFIG, 0b1111_1100_1111_1001)  # report everything to STATUS and INTB except ZC
        self.set_reg(REG_MUX_CONFIG, 0x0208 | deglitch)
        if (self._device_product == PRODUCT_LDC1612_INTERNAL_CLK):
            # use internal oscillator
            # RP_OVERRIDE_EN | AUTO_AMP_DIS | reserved
            self.set_reg(REG_CONFIG, (1 << 12) | (1 << 10) | 0x001)
        else:
            # RP_OVERRIDE_EN | AUTO_AMP_DIS | REF_CLK_SRC=clkin | reserved
            self.set_reg(REG_CONFIG, (1 << 12) | (1 << 10) | (1 << 9) | 0x001)
        self.set_reg(REG_DRIVE_CURRENT0, self._drive_current << 11)

        self._chip_initialized = True

    def get_deglitch(self):
        return self.read_reg(REG_MUX_CONFIG) & ~0x0208

    def set_deglitch(self, val: int):
        logging.info(f"LDC1612ng {self._name} deglitch set {val}")
        self.set_reg(REG_MUX_CONFIG, val | 0x0208)

    def get_drive_current(self) -> int:
        return self._drive_current

    def set_drive_current(self, cval: int, maxfreq: float = None):
        if cval < 0 or cval > 31:
            raise self.printer.command_error("Drive current must be between 0 and 31")
        if self._drive_current == cval:
            return

        if maxfreq is not None:
            if maxfreq < 1_000_000.0:
                self.set_deglitch(DEGLITCH_1_0MHZ)
            elif maxfreq < 3_300_000.0:
                self.set_deglitch(DEGLITCH_3_3MHZ)
            elif maxfreq < 10_000_000.0:
                self.set_deglitch(DEGLITCH_10MHZ)
            else:
                self.set_deglitch(DEGLITCH_33MHZ)

        logging.info(f"LDC1612ng {self._name} set drive current {cval}")
        self._drive_current = cval
        self.set_reg(REG_DRIVE_CURRENT0, cval << 11)

    # Start, stop, and process message batches
    def start_streaming(self, callback, callback_interval=0.5, send_filter_values=False):
        if self._data_callback is not None:
            raise self.printer.command_error("start_streaming: already streaming")

        logging.info("LDC1612 starting'%s' measurements", self._name)

        self._init_chip()
        self._next_seq = 0
        # anything still pending belongs to a previous stream
        with self._data_lock:
            self._pending_data = []
        self._data_callback = callback
        self._data_interval = callback_interval

        reactor = self.printer.get_reactor()
        self._data_timer = reactor.register_timer(self._process_data, reactor.monotonic() + callback_interval)

        rcount_now_sec = self.get_rcount_sec()
        self._time_fit = ConversionTimeFit(rcount_now_sec / 2.0)

        # Poll more than twice per conversion so we don't miss samples. Deliberately
        # not an integer fraction of the conversion period, so that the poll phase
        # sweeps across the conversions and ConversionTimeFit sees reads that
        # happen right after a conversion completes.
        rest_ticks = self._mcu.seconds_to_clock(rcount_now_sec / 2.3)
        flags = START_FLAG_SEND_FILTER_VALUE if send_filter_values else 0
        self._ldc1612_ng_start_stop_cmd.send([self._oid, rest_ticks, flags])

    def stop_streaming(self):
        self._ldc1612_ng_start_stop_cmd.send_wait_ack([self._oid, 0, 0])
        reactor = self.printer.get_reactor()
        reactor.unregister_timer(self._data_timer)
        self._data_timer = None

        self._process_data(reactor.monotonic())

        self._next_seq = 0
        self._data_callback = None
        logging.info("LDC1612 finished '%s' measurements", self._name)

    def _handle_data(self, params):
        seq = params["seq"]
        overflows = params["ov"]
        fmt = params["fmt"]
        data_raw = params["data"]

        with self._data_lock:
            self._pending_data.append([seq, overflows, fmt, data_raw])

    def process_data(self):
        self._process_data(self.printer.get_reactor().monotonic())

    def _process_data(self, eventtime):
        with self._data_lock:
            pending_data = self._pending_data
            self._pending_data = []

        if not self._data_callback:
            raise self.printer.command_error("DATA WITHOUT CALLBACK")

        times: list[float] = []
        values: list[int] = []
        accum_values: list[float] = []

        for seq, overflows, fmt, data_raw in pending_data:
            if overflows > 0:
                logging.error(f"LDC1612ng at least {overflows} dropped conversions")
            if seq != self._next_seq:
                logging.error(f"LDC1612ng lost data! Expected seq {self._next_seq} got {seq}")

            self._next_seq = (seq + 1) & 0xFF

            sample_fmt = DATA_FORMATS.get(fmt)
            if sample_fmt is None:
                logging.error(f"LDC1612ng unknown data format {fmt}, ignoring")
                continue
            sample_size = struct.calcsize(sample_fmt)
            if len(data_raw) % sample_size != 0:
                logging.error(f"LDC1612ng invalid data length: {len(data_raw)}, ignoring")
                continue

            for sample in struct.iter_unpack(sample_fmt, data_raw):
                times.append(self._clock32_to_print_time(sample[0]))
                values.append(sample[1])
                # the filter value is only sent when requested (for plotting)
                accum_values.append(sample[2] if len(sample) > 2 else math.nan)

        if self._time_fit is not None:
            times = self._time_fit.add_reads(times)

        data = LDC1612_ng_sample_data(times=times, raw_freqs=values, accum_vals=accum_values)
        self._data_callback(data)
        return self.printer.get_reactor().monotonic() + self._data_interval
