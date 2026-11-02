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

import inspect

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

# Kalico (PR #1052) gives every command an execution context, and HomingMove
# takes the command (and a required toolhead): HomingMove(printer, endstops,
# toolhead, gcmd). Klipper and older Kalico have HomingMove(printer, endstops,
# toolhead=None).
try:
    HOMING_MOVE_TAKES_GCMD = "gcmd" in inspect.signature(HomingMove.__init__).parameters
except (TypeError, ValueError):
    HOMING_MOVE_TAKES_GCMD = False


def make_homing_move(printer: Printer, endstops, toolhead: ToolHead, gcmd: GCodeCommand) -> HomingMove:
    if HOMING_MOVE_TAKES_GCMD:
        return HomingMove(printer, endstops, toolhead, gcmd)
    return HomingMove(printer, endstops, toolhead)


# Set the toolhead position and mark Z as homed
def set_position_z_homed(toolhead: ToolHead, pos):
    # Klipper (Jan 2025) changed homing_axes from a tuple of axis indices
    # to a string of axis names
    if isinstance(toolhead.set_position.__defaults__[0], str):
        toolhead.set_position(pos, homing_axes="z")
    else:
        toolhead.set_position(pos, homing_axes=(2,))


# Mark Z as no longer homed
def clear_z_homed(toolhead: ToolHead, command_error):
    kin = toolhead.get_kinematics()
    # Klipper replaced note_z_not_homed with clear_homing_state
    if hasattr(kin, "note_z_not_homed"):
        kin.note_z_not_homed()
        return
    try:
        kin.clear_homing_state("z")
    except TypeError:
        raise command_error(
            "clear_homing_state failed: please update Klipper, your klipper is from the brief 5 day window where this was broken"
        )


# Register a callback for a message from the mcu. Klipper replaced
# mcu.register_response(cb, name, oid) with register_serial_response(cb,
# msgformat, oid), which validates the format against the firmware; Kalico
# still has the former. An optional response is only registered (on Klipper)
# if the firmware can send it.
def register_mcu_response(mcu_obj, cb, msgformat: str, oid=None, optional: bool = False):
    if hasattr(mcu_obj, "register_serial_response"):
        if optional and not mcu_obj.check_valid_response(msgformat):
            return
        mcu_obj.register_serial_response(cb, msgformat, oid)
    else:
        mcu_obj.register_response(cb, msgformat.split()[0], oid)


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
    "HOMING_MOVE_TAKES_GCMD",
    "make_homing_move",
    "set_position_z_homed",
    "clear_z_homed",
    "register_mcu_response",
]
