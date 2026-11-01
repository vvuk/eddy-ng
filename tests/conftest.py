# Tests run without Klipper: eddy-ng's imports of Klipper's modules (see
# probe_eddy_ng/compat.py) get small stand-ins, and the code under test is
# called with fakes for the printer objects it uses (see fakes.py).
import os
import sys
import types

from fakes import CommandError, ConfigError


def _install_klipper_stubs():
    def module(name, **attrs):
        m = types.ModuleType(name)
        m.__dict__.update(attrs)
        sys.modules[name] = m
        return m

    class _Placeholder:
        pass

    class ProbeResult(tuple):
        pass

    # Klipper's layout (klippy/ on the path); with no klippy.mcu, compat.py
    # takes this branch rather than Kalico's
    module("klippy", Printer=_Placeholder)
    # Klipper's value; eddy-ng numbers its own homing errors after it
    module("mcu", MCU_trsync=types.SimpleNamespace(REASON_COMMS_TIMEOUT=4))
    module("pins")
    module("chelper", get_ffi=lambda: (None, None))
    module("configfile", ConfigWrapper=_Placeholder, error=ConfigError)
    module("gcode", GCodeCommand=_Placeholder, CommandError=CommandError)
    module("toolhead", ToolHead=_Placeholder)
    extras = module("extras")
    extras.__path__ = []
    for name, attrs in (
        ("bus", {}),
        ("probe", {}),
        ("manual_probe", {"ProbeResult": ProbeResult}),
        ("bed_mesh", {}),
        ("homing", {"HomingMove": _Placeholder}),
    ):
        setattr(extras, name, module(f"extras.{name}", **attrs))


_install_klipper_stubs()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
