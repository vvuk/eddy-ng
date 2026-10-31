# EDDY-ng
#
# Copyright (C) 2025  Vladimir Vukicevic <vladimir@pobox.com>
#
# Differences between the hosts eddy-ng runs on. Kalico's klippy is a
# package (and loads eddy-ng as a plugin); Klipper puts klippy/ on the path
# and loads eddy-ng from klippy/extras/probe_eddy_ng/.
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

try:
    from klippy import mcu, pins, chelper
    from klippy.printer import Printer
    from klippy.configfile import ConfigWrapper
    from klippy.configfile import error as configerror
    from klippy.gcode import GCodeCommand
    from klippy.toolhead import ToolHead
    from klippy.extras import bus, probe, manual_probe, bed_mesh
    from klippy.extras.homing import HomingMove

    IS_KALICO = True
except ImportError:
    import mcu
    import pins
    import chelper
    from klippy import Printer
    from configfile import ConfigWrapper
    from configfile import error as configerror
    from gcode import GCodeCommand
    from toolhead import ToolHead
    from extras import bus, probe, manual_probe, bed_mesh
    from extras.homing import HomingMove

    IS_KALICO = False

# Newer Klipper passes probe results around as manual_probe.ProbeResult
HAS_PROBE_RESULT_TYPE = hasattr(manual_probe, "ProbeResult")

__all__ = [
    "mcu",
    "pins",
    "chelper",
    "Printer",
    "ConfigWrapper",
    "configerror",
    "GCodeCommand",
    "ToolHead",
    "bus",
    "probe",
    "manual_probe",
    "bed_mesh",
    "HomingMove",
    "IS_KALICO",
    "HAS_PROBE_RESULT_TYPE",
]
