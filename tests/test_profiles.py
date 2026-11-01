# Plate and tap profile names, and reading and writing tap profiles
import configparser

import pytest

from fakes import ConfigError, FakeConfig
from probe_eddy_ng.profiles import (
    TapProfile,
    is_hand_written,
    is_hand_written_option,
    material_code,
    profile_name,
    read_material_adjust_z,
    read_tap_profile,
)


@pytest.mark.parametrize(
    "name, expected",
    [
        ("textured", "textured"),
        ("Textured PEI Plate", "textured_pei_plate"),
        ("  smooth\tpei ", "smooth_pei"),
        ("cool-plate_2", "cool-plate_2"),
    ],
)
def test_profile_name(name, expected):
    assert profile_name(name, ConfigError) == expected


@pytest.mark.parametrize("name", ["", "a/b", "plate!", "p.e.i"])
def test_invalid_profile_name(name):
    with pytest.raises(ConfigError, match="Invalid profile name"):
        profile_name(name, ConfigError)


def test_material_code():
    assert material_code("pla", ConfigError) == "PLA"
    assert material_code("PLA Silk", ConfigError) == "PLA_SILK"
    assert material_code("asa-gf", ConfigError) == "ASA-GF"
    assert material_code("PA6+CF", ConfigError) == "PA6+CF"
    with pytest.raises(ConfigError, match="Invalid material"):
        material_code("PLA/PETG", ConfigError)


def textured():
    return FakeConfig(
        {
            "tap_threshold": "220",
            "tap_position": "liftoff",
            "tap_drive_current": "17",
            "tap_adjust_z": "0.010",
            "tap_adjust_z_petg": "0.040",
            "tap_adjust_z_asa-gf": "0.030",
            "unrelated": "x",
        },
        name="probe_eddy_ng_tap textured",
    )


def test_read_tap_profile():
    tp = read_tap_profile(textured(), "textured")
    assert tp.section == "probe_eddy_ng_tap textured"
    # only the settings the profile sets
    assert tp.params == {"tap_threshold": 220.0, "tap_position": "liftoff"}
    assert tp.tap_drive_current == 17 and tp.reg_drive_current == 0
    assert tp.drive_currents() == [17]
    assert tp.material_adjust_z == {"PETG": 0.04, "ASA-GF": 0.03}


def test_tap_profile_requires_adjust_z():
    with pytest.raises(ConfigError, match="tap_adjust_z"):
        read_tap_profile(FakeConfig({"tap_threshold": "100"}), "x")


def test_tap_profile_rejects_bad_values():
    with pytest.raises(ConfigError, match="not a valid choice"):
        read_tap_profile(FakeConfig({"tap_adjust_z": "0", "tap_position": "up"}), "x")
    with pytest.raises(ConfigError):
        read_tap_profile(FakeConfig({"tap_adjust_z": "0", "tap_threshold": "0"}), "x")


def test_adjust_z_per_material():
    tp = read_tap_profile(textured(), "textured")
    assert tp.adjust_z_for(None) == 0.01
    assert tp.adjust_z_for("PETG") == 0.04
    # materials without their own value use the profile's
    assert tp.adjust_z_for("PLA") == 0.01

    tp.set_adjust_z("PLA", 0.02)
    tp.set_adjust_z(None, 0.005)
    assert tp.adjust_z_for("PLA") == 0.02
    assert tp.adjust_z_for("NYLON") == 0.005
    assert TapProfile.adjust_z_option(None) == "tap_adjust_z"
    assert TapProfile.adjust_z_option("ASA-GF") == "tap_adjust_z_asa-gf"


def test_options_round_trip():
    tp = read_tap_profile(textured(), "textured")
    tp.set_adjust_z("PLA", 0.02)
    again = read_tap_profile(FakeConfig(tp.options(), name=tp.section), "textured")
    assert again == tp


def test_read_material_adjust_z():
    config = FakeConfig({"tap_adjust_z": "0.1", "tap_adjust_z_pla": "0.02"})
    assert read_material_adjust_z(config) == {"PLA": 0.02}


def autosave(section, **options):
    fc = configparser.RawConfigParser()
    fc.add_section(section)
    for k, v in options.items():
        fc.set(section, k, v)
    return fc


def test_hand_written():
    section = "probe_eddy_ng_tap saved"
    # every option came from SAVE_CONFIG
    config = FakeConfig({"tap_adjust_z": "0.001", "tap_threshold": "100"}, name=section)
    saved = autosave(section, tap_adjust_z="0.001", tap_threshold="100")
    assert not is_hand_written(config, saved)
    assert not is_hand_written_option(config, saved, "tap_threshold")

    # tap_threshold is in the config files
    saved = autosave(section, tap_adjust_z="0.001")
    assert is_hand_written(config, saved)
    assert is_hand_written_option(config, saved, "tap_threshold")
    assert not is_hand_written_option(config, saved, "tap_adjust_z")
    # not set at all
    assert not is_hand_written_option(config, saved, "tap_speed")

    # nothing saved for the section
    assert is_hand_written(config, configparser.RawConfigParser())
