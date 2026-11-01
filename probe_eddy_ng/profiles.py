# EDDY-ng
#
# Copyright (C) 2025  Vladimir Vukicevic <vladimir@pobox.com>
#
# Plate profiles (the calibration for a build surface) and tap profiles
# (tap settings, and tap_adjust_z values per material)
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .compat import ConfigWrapper
from .params import ProbeEddyParams

if TYPE_CHECKING:
    from .fmap import ProbeEddyFrequencyMap

# The calibration and tap settings in the [probe_eddy_ng] section itself
DEFAULT_PROFILE = "default"

ADJUST_Z_PREFIX = "tap_adjust_z_"

_NAME_RE = re.compile(r"^[a-z0-9_-]+$")
_MATERIAL_RE = re.compile(r"^[A-Z0-9_+.-]+$")


# [probe_eddy_ng_plate <sensor> <plate>]; plates are per sensor because
# the calibration belongs to the coil
def plate_section_prefix(sensor_name: str) -> str:
    return f"probe_eddy_ng_plate {sensor_name} "


# [probe_eddy_ng_tap <profile>]
TAP_SECTION_PREFIX = "probe_eddy_ng_tap "


# Spaces become underscores, so that e.g. LOAD="Textured PEI Plate" (such as
# a slicer's bed type) finds textured_pei_plate
def profile_name(name: str, error) -> str:
    name = re.sub(r"\s+", "_", name.strip()).lower()
    if not _NAME_RE.match(name):
        raise error(f"Invalid profile name '{name}': use only letters, digits, '_' and '-'")
    return name


# Spaces become underscores here too, so MATERIAL="PLA Silk" is PLA_SILK
def material_code(code: str, error) -> str:
    code = re.sub(r"\s+", "_", code.strip()).upper()
    if not _MATERIAL_RE.match(code):
        raise error(f"Invalid material '{code}': use only letters, digits, '_', '-', '+' and '.'")
    return code


# Whether any of a section's options come from the user's config files,
# rather than only from the SAVE_CONFIG block (which is all we can remove)
def is_hand_written(config: ConfigWrapper, autosave_fileconfig) -> bool:
    section = config.get_name()
    saved = set(autosave_fileconfig.options(section)) if autosave_fileconfig.has_section(section) else set()
    return any(o not in saved for o in config.get_prefix_options(""))


# Whether an option is set in the config files, rather than only in the
# SAVE_CONFIG section. SAVE_CONFIG refuses to save a different value for it.
def is_hand_written_option(config: ConfigWrapper, autosave_fileconfig, option: str) -> bool:
    section = config.get_name()
    if option not in config.get_prefix_options(option):
        return False
    return not (autosave_fileconfig.has_section(section) and autosave_fileconfig.has_option(section, option))


@dataclass
class PlateProfile:
    name: str
    section: str
    fmaps: Dict[int, ProbeEddyFrequencyMap] = field(default_factory=dict)
    reg_drive_current: int = 0
    tap_drive_current: int = 0
    hand_written: bool = False


@dataclass
class TapProfile:
    name: str
    section: str
    # ProbeEddyParams overrides
    params: Dict[str, Any] = field(default_factory=dict)
    # drive current overrides; 0 uses the plate's
    reg_drive_current: int = 0
    tap_drive_current: int = 0
    # the default tap_adjust_z, and the ones for specific materials
    tap_adjust_z: float = 0.0
    material_adjust_z: Dict[str, float] = field(default_factory=dict)
    hand_written: bool = False
    # tap_threshold is set in the config files (see is_hand_written_option)
    hand_written_threshold: bool = False

    def drive_currents(self) -> List[int]:
        return [dc for dc in (self.reg_drive_current, self.tap_drive_current) if dc]

    # The config option holding tap_adjust_z for a material (or the default)
    @staticmethod
    def adjust_z_option(material: Optional[str]) -> str:
        return "tap_adjust_z" if material is None else ADJUST_Z_PREFIX + material.lower()

    def adjust_z_for(self, material: Optional[str]) -> float:
        if material is None:
            return self.tap_adjust_z
        return self.material_adjust_z.get(material, self.tap_adjust_z)

    def set_adjust_z(self, material: Optional[str], value: float):
        if material is None:
            self.tap_adjust_z = value
        else:
            self.material_adjust_z[material] = value

    # The options to write for this profile when saving it
    def options(self) -> Dict[str, str]:
        opts = {k: str(v) for k, v in self.params.items()}
        if self.reg_drive_current:
            opts["reg_drive_current"] = str(self.reg_drive_current)
        if self.tap_drive_current:
            opts["tap_drive_current"] = str(self.tap_drive_current)
        opts["tap_adjust_z"] = f"{self.tap_adjust_z:.3f}"
        for material, value in self.material_adjust_z.items():
            opts[self.adjust_z_option(material)] = f"{value:.3f}"
        return opts


def read_material_adjust_z(config: ConfigWrapper) -> Dict[str, float]:
    result = {}
    for option in config.get_prefix_options(ADJUST_Z_PREFIX):
        result[material_code(option[len(ADJUST_Z_PREFIX) :], config.error)] = config.getfloat(option)
    return result


def read_tap_profile(config: ConfigWrapper, name: str) -> TapProfile:
    params = {}
    for key, (getter, limits) in ProbeEddyParams.TAP_PROFILE_PARAMS.items():
        if key in config.get_prefix_options(key):
            params[key] = getattr(config, getter)(key, **limits)

    return TapProfile(
        name=name,
        section=config.get_name(),
        params=params,
        reg_drive_current=config.getint("reg_drive_current", 0, minval=0, maxval=31),
        tap_drive_current=config.getint("tap_drive_current", 0, minval=0, maxval=31),
        tap_adjust_z=config.getfloat("tap_adjust_z"),
        material_adjust_z=read_material_adjust_z(config),
    )
