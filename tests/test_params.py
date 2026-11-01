# Reading and checking the [probe_eddy_ng] settings
import pytest

from fakes import ConfigError, FakeConfig
from probe_eddy_ng.params import ProbeEddyParams


def load(**options):
    params = ProbeEddyParams()
    params.load_from_config(FakeConfig({"x_offset": 0, "y_offset": 20, **options}))
    return params


def test_defaults():
    p = load()
    assert p.tap_threshold == 250.0
    assert p.tap_position == "descent"
    assert p.tap_samples == 3 and p.tap_max_samples == 5
    assert p.y_offset == 20.0
    # half of home_trigger_height unless set
    assert p.tap_trigger_safe_start_height == 1.0
    assert load(home_trigger_height=3.0).tap_trigger_safe_start_height == 1.5
    assert p.is_default_butter_config()


def test_values():
    p = load(tap_threshold=180, tap_position="liftoff", tap_use_median="true", tap_adjust_z=0.04, reg_drive_current=15)
    assert p.tap_threshold == 180.0
    assert p.tap_position == "liftoff"
    assert p.tap_use_median is True
    assert p.tap_adjust_z == 0.04
    assert p.reg_drive_current == 15


@pytest.mark.parametrize(
    "options, message",
    [
        ({"tap_position": "sideways"}, "not a valid choice"),
        ({"tap_samples": 6}, "tap_max_samples must be at least"),  # more than the default tap_max_samples
        ({"tap_samples": 4, "tap_max_samples": 3}, "minimum"),
        ({"tap_time_position": 1.5}, "maximum"),
        ({"reg_drive_current": 32}, "maximum"),
        ({"tap_butter_lowcut": 30}, "highcut must be above"),  # not below the default highcut
        ({"tap_butter_lowcut": 10, "tap_butter_highcut": 8}, "above"),
        ({"tap_butter_order": 5}, "maximum"),
        ({"tap_mode": "wma"}, "only tap_mode 'butter'"),
        ({"calibration_z_max": 3.5}, "calibration_z_max must be at least"),
        ({"y_offset": 0}, "is the sensor really mounted at the nozzle"),
        ({"tap_trigger_safe_start_height": 2.5}, "home_trigger_height must be greater"),
    ],
)
def test_invalid(options, message):
    with pytest.raises(ConfigError, match=message):
        load(**options)


def test_allow_unsafe_offsets():
    assert load(y_offset=0, allow_unsafe="true").allow_unsafe


def test_bigfoot_skips_the_rest():
    p = load(bigfoot="true", bigfoot_scan_position="1,2,3", tap_position="nonsense")
    assert p.bigfoot and not p.primary
    assert p.bigfoot_scan_position == [1.0, 2.0, 3.0]


def test_tap_overrides():
    base = load()
    p = base.with_tap_overrides({"tap_threshold": 100.0, "tap_position": "liftoff"}, ConfigError)
    assert p.tap_threshold == 100.0 and p.tap_position == "liftoff"
    # a copy: the base is unchanged
    assert base.tap_threshold == 250.0 and base.tap_position == "descent"
    assert p.y_offset == 20.0

    with pytest.raises(ConfigError, match="tap_max_samples"):
        base.with_tap_overrides({"tap_samples": 9}, ConfigError)
    with pytest.raises(ConfigError, match="highcut"):
        base.with_tap_overrides({"tap_butter_highcut": 4.0}, ConfigError)
    assert not base.with_tap_overrides({"tap_butter_order": 3}, ConfigError).is_default_butter_config()
