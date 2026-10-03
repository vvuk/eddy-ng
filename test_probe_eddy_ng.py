import os
import pytest
import sys
import importlib.util
from unittest.mock import MagicMock, patch, mock_open
import numpy as np

# Mock all Klipper and local imports to allow standalone importing
mock_modules = [
    'klippy',
    'klippy.mcu',
    'klippy.pins',
    'klippy.chelper',
    'klippy.printer',
    'klippy.configfile',
    'klippy.gcode',
    'klippy.toolhead',
    'klippy.extras',
    'klippy.extras.homing',
    'ldc1612_ng',
    'mcu',
    'pins',
    'chelper',
    'printer',
    'configfile',
    'gcode',
    'toolhead',
    'probe',
    'manual_probe',
    'bed_mesh',
    'homing'
]
for mod in mock_modules:
    sys.modules[mod] = MagicMock()

# Setup parent package and attributes
class KlipperConfigError(Exception):
    pass

sys.modules["klippy.configfile"].error = KlipperConfigError
sys.modules["configfile"].error = KlipperConfigError

parent_pkg = MagicMock()
sys.modules["probe_eddy_ng"] = parent_pkg
ldc_module = MagicMock()
sys.modules["probe_eddy_ng.ldc1612_ng"] = ldc_module
setattr(parent_pkg, 'ldc1612_ng', ldc_module)

# Also mock manual_probe inside the package
mock_manual_probe = MagicMock()
sys.modules["probe_eddy_ng.manual_probe"] = mock_manual_probe
setattr(parent_pkg, 'manual_probe', mock_manual_probe)

# Load probe_eddy_ng using importlib
spec = importlib.util.spec_from_file_location(
    "probe_eddy_ng.probe_eddy_ng",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_eddy_ng.py")
)
probe_eddy_ng = importlib.util.module_from_spec(spec)
sys.modules["probe_eddy_ng.probe_eddy_ng"] = probe_eddy_ng
probe_eddy_ng.__package__ = "probe_eddy_ng"
spec.loader.exec_module(probe_eddy_ng)


class EddyEnv:
    def __init__(self, eddy, config, printer, configfile, bed_mesh_config):
        self.eddy = eddy
        self.config = config
        self.printer = printer
        self.configfile = configfile
        self.bed_mesh_config = bed_mesh_config


@pytest.fixture
def eddy_env():
    # Create a mock config wrapper
    config = MagicMock()
    printer = MagicMock()
    config.get_printer.return_value = printer
    config.get_name.return_value = "probe_eddy_ng fly_eddy_probe"
    config.getchoice.return_value = "ldc1612"

    # Configure config.get to return None for default queries
    def mock_config_get(name, default=None, **kwargs):
        if name == "calibration_3d":
            return None
        return default
    config.get.side_effect = mock_config_get

    # Mock autosave and configfile
    configfile = MagicMock()
    configfile.autosave.fileconfig.getint.return_value = None
    printer.lookup_object.return_value = configfile

    # Mock bed_mesh config block lookup
    bed_mesh_config = MagicMock()
    bed_mesh_config.getintlist.return_value = [5, 5]
    bed_mesh_config.getfloatlist.side_effect = lambda name, **kwargs: {
        "mesh_min": [10.0, 10.0],
        "mesh_max": [110.0, 110.0]
    }.get(name, [0.0, 0.0])
    bed_mesh_config.getfloat.side_effect = lambda name, default=0.0, **kwargs: {
        "speed": 100.0,
        "horizontal_move_z": 2.0
    }.get(name, default)
    config.getsection.return_value = bed_mesh_config

    # Patch the ProbeEddy constructor dependencies
    with patch.object(probe_eddy_ng.ProbeEddyParams, 'load_from_config'):
        eddy = probe_eddy_ng.ProbeEddy(config)

    # Initialize some default parameters
    eddy.params.calibration_z_max = 5.0
    eddy.params.probe_speed = 5.0
    eddy.params.lift_speed = 5.0
    eddy.params.move_speed = 5.0
    eddy.params.x_offset = 0.0
    eddy.params.y_offset = 0.0
    eddy.offset = {"x": 0.0, "y": 0.0}

    eddy._toolhead = MagicMock()
    eddy._toolhead.set_position = MagicMock()
    eddy._toolhead.set_position.__defaults__ = ("xyz",)

    return EddyEnv(eddy, config, printer, configfile, bed_mesh_config)


def test_cmd_calibrate_missing_temperature(eddy_env):
    # cmd_CALIBRATE must raise error if TEMPERATURE parameter is missing
    gcmd = MagicMock()
    gcmd.get_float.return_value = None

    with pytest.raises(Exception):
        eddy_env.eddy.cmd_CALIBRATE(gcmd)

    gcmd.error.assert_called_with("TEMPERATURE parameter is required for PROBE_EDDY_NG_CALIBRATE")


def test_cmd_calibrate_with_temperature(eddy_env):
    # cmd_CALIBRATE should delegate to manual_probe.ManualProbeHelper if TEMPERATURE is specified
    gcmd = MagicMock()
    # Mock get_float so that it returns 25.0 when queried for TEMPERATURE
    def mock_get_float(param, default=None, **kwargs):
        if param == "TEMPERATURE":
            return 25.0
        return default
    gcmd.get_float.side_effect = mock_get_float

    # Mock _xy_homed to True
    eddy_env.eddy._xy_homed = MagicMock(return_value=True)
    eddy_env.eddy._z_homed = MagicMock(return_value=False)
    eddy_env.eddy._set_toolhead_position = MagicMock()

    # Mock manual_probe.ManualProbeHelper
    with patch.object(probe_eddy_ng.manual_probe, 'ManualProbeHelper') as mock_helper:
        eddy_env.eddy.cmd_CALIBRATE(gcmd)
        mock_helper.assert_called_once()

        # Extract lambda/callback passed to ManualProbeHelper
        args, kwargs = mock_helper.call_args
        callback = args[2]

        # Now mock cmd_CALIBRATE_next and test if callback calls it with temperature
        eddy_env.eddy.cmd_CALIBRATE_next = MagicMock()
        callback([0.0, 0.0, 0.0])
        eddy_env.eddy.cmd_CALIBRATE_next.assert_called_once_with(gcmd, [0.0, 0.0, 0.0], 25.0)


def test_cmd_calibrate_next_updates_3d_matrix(eddy_env):
    gcmd = MagicMock()
    gcmd.get_int.return_value = 16
    gcmd.get_float.return_value = 5.0

    # Mock self._create_mapping
    mock_map = MagicMock()
    eddy_env.eddy._create_mapping = MagicMock(return_value=(mock_map, 0.1, 0.2))
    eddy_env.eddy.save_config = MagicMock()
    eddy_env.eddy._z_not_homed = MagicMock()
    eddy_env.eddy._set_toolhead_position = MagicMock()

    # Initialize self._dc_to_temp_fmaps
    eddy_env.eddy._dc_to_temp_fmaps = {}

    # Execute cmd_CALIBRATE_next for temp 25.0
    eddy_env.eddy.cmd_CALIBRATE_next(gcmd, [0.0, 0.0, 0.0], 25.0)

    # Check that it updated the 3D map
    assert 16 in eddy_env.eddy._dc_to_temp_fmaps
    assert len(eddy_env.eddy._dc_to_temp_fmaps[16]) == 1
    assert eddy_env.eddy._dc_to_temp_fmaps[16][0] == (25.0, mock_map)

    # Execute again for temp 30.0
    mock_map_2 = MagicMock()
    eddy_env.eddy._create_mapping.return_value = (mock_map_2, 0.1, 0.2)
    eddy_env.eddy.cmd_CALIBRATE_next(gcmd, [0.0, 0.0, 0.0], 30.0)
    assert len(eddy_env.eddy._dc_to_temp_fmaps[16]) == 2
    assert eddy_env.eddy._dc_to_temp_fmaps[16][1] == (30.0, mock_map_2)

    # Execute for temp 20.0 (should be inserted at the beginning because of sorting)
    mock_map_3 = MagicMock()
    eddy_env.eddy._create_mapping.return_value = (mock_map_3, 0.1, 0.2)
    eddy_env.eddy.cmd_CALIBRATE_next(gcmd, [0.0, 0.0, 0.0], 20.0)
    assert len(eddy_env.eddy._dc_to_temp_fmaps[16]) == 3
    assert eddy_env.eddy._dc_to_temp_fmaps[16][0] == (20.0, mock_map_3)
    assert eddy_env.eddy._dc_to_temp_fmaps[16][1] == (25.0, mock_map)
    assert eddy_env.eddy._dc_to_temp_fmaps[16][2] == (30.0, mock_map_2)

    # Execute for temp 25.0 again (should replace existing)
    mock_map_4 = MagicMock()
    eddy_env.eddy._create_mapping.return_value = (mock_map_4, 0.1, 0.2)
    eddy_env.eddy.cmd_CALIBRATE_next(gcmd, [0.0, 0.0, 0.0], 25.0)
    assert len(eddy_env.eddy._dc_to_temp_fmaps[16]) == 3
    assert eddy_env.eddy._dc_to_temp_fmaps[16][1] == (25.0, mock_map_4)


def test_map_for_drive_current_fallback(eddy_env):
    # Test that map_for_drive_current falls back to 3D map if 2D map is empty
    eddy_env.eddy._dc_to_fmap = {}
    eddy_env.eddy._dc_to_temp_fmaps = {}

    # Should raise command error if neither is populated
    with pytest.raises(Exception):
        eddy_env.eddy.map_for_drive_current(16)

    # If 3D map is populated, it should return the first mapping
    mock_map = MagicMock()
    eddy_env.eddy._dc_to_temp_fmaps[16] = [(25.0, mock_map)]

    res = eddy_env.eddy.map_for_drive_current(16)
    assert res == mock_map


def test_log_calibration_sweep_data(eddy_env):
    # Create a mock mapping
    mock_map = MagicMock()
    mock_map.height_range = [0.0, 5.0]
    mock_map.height_to_freq.side_effect = lambda h: h * 1000000.0 + 12000000.0

    # Mock ftoh and htof polynomials
    mock_map._ftoh = MagicMock()
    mock_map._ftoh.coef = MagicMock()
    mock_map._ftoh.coef.tolist.return_value = [1.0, 2.0, 3.0]

    mock_map._htof = MagicMock()
    mock_map._htof.coef = MagicMock()
    mock_map._htof.coef.tolist.return_value = [4.0, 5.0, 6.0]

    eddy_env.eddy._log_msg = MagicMock()
    eddy_env.eddy._log_calibration_sweep_data(25.0, 16, mock_map)

    # Verify it logged the sweep coefficients and sweep grid
    eddy_env.eddy._log_msg.assert_any_call(
        "3D Sweep Log: temp=25.00C, dc=16, ftoh_coefs=[1.0, 2.0, 3.0], htof_coefs=[4.0, 5.0, 6.0]"
    )


@pytest.mark.parametrize("room_temp, expected_commands", [
    (30.0, ["M190 S35", "M190 S40", "M190 S45", "M190 S50", "M190 S55", "M190 S60"]),
    (33.4, ["M190 S35", "M190 S40", "M190 S45", "M190 S50", "M190 S55", "M190 S60"]),
    (34.9, ["M190 S35", "M190 S40", "M190 S45", "M190 S50", "M190 S55", "M190 S60"]),
    (35.0, ["M190 S40", "M190 S45", "M190 S50", "M190 S55", "M190 S60"]),
    (35.1, ["M190 S40", "M190 S45", "M190 S50", "M190 S55", "M190 S60"]),
])
def test_cmd_setup_next_step_by_step_heating(eddy_env, room_temp, expected_commands):
    gcmd = MagicMock()
    eddy_env.eddy._setup_target_temp = 60.0

    # Mock valid currents and other dependencies
    eddy_env.eddy._temp_sensor_obj = MagicMock()
    eddy_env.eddy.get_sensor_temp = MagicMock(return_value=room_temp)

    mock_map = MagicMock()
    mock_map.height_range = [0.0, 10.0]
    mock_map.freq_spread.return_value = 1.0
    mock_map._htof = lambda h: 7.5e-7
    mock_map._ftoh = lambda invf: 3.5

    eddy_env.eddy._create_mapping = MagicMock(return_value=(mock_map, 0.1, 0.2))
    eddy_env.eddy._log_calibration_sweep_data = MagicMock()
    eddy_env.eddy._log_msg = MagicMock()

    # Mock sensor read_one_value return value to pass validity checks
    val = MagicMock()
    val.freq = 10000000.0
    val.status = 0
    eddy_env.eddy._sensor.read_one_value.return_value = val

    # Patch lookup_object
    eddy_env.printer.lookup_object.side_effect = lambda name, default=None: {
        "configfile": eddy_env.configfile,
    }.get(name, MagicMock())

    # Track M190 commands run
    run_commands = []
    eddy_env.eddy._gcode.run_script_from_command.side_effect = lambda cmd: run_commands.append(cmd)

    # Mock other trailing setup logic we want to bypass or mock
    eddy_env.eddy.save_config = MagicMock()
    eddy_env.eddy._z_not_homed = MagicMock()
    eddy_env.eddy._reg_drive_current = 14
    eddy_env.eddy._tap_drive_current = 15

    # Call cmd_SETUP_next with some mock inputs
    with patch.object(eddy_env.eddy, '_xy_homed', return_value=True), \
         patch.object(eddy_env.eddy, '_z_homed', return_value=True), \
         patch.object(eddy_env.eddy, 'do_one_tap') as mock_tap:

        # Setup a mock tap result
        mock_tap_res = MagicMock()
        mock_tap_res.error = None
        mock_tap_res.probe_z = 1.0
        mock_tap_res.overshoot = 0.0
        mock_tap.return_value = mock_tap_res

        # Setup valid currents near room temp and far Z
        eddy_env.eddy._dc_to_temp_fmaps = {}

        eddy_env.eddy.cmd_SETUP_next(gcmd, [0.0, 0.0, 0.0])

    # We expect the M190 commands to match the expected list exactly
    for cmd in expected_commands:
        assert cmd in run_commands
    assert len(run_commands) == len(expected_commands)


@patch("builtins.open", new_callable=mock_open)
def test_save_calibration_3d(mock_file_open, eddy_env):
    mock_map = MagicMock()
    mock_map._ftoh = MagicMock()
    mock_map._ftoh.p_inf = 0.75e-6
    mock_map._ftoh.coef = [1.0, 2.0, 3.0, 4.0]
    mock_map._htof = MagicMock()
    mock_map._htof.coef = np.asarray([0.1]*10)
    mock_map.height_range = [0.1, 5.0]
    mock_map.freq_range = [1000000.0, 2000000.0]
    mock_map.raw_data = ([0.0], [1500000.0], [0.5], [0.0])

    eddy_env.eddy._dc_to_temp_fmaps = {
        12: [(30.0, mock_map)]
    }
    eddy_env.eddy._dc_to_drift_coefs = {
        12: (30.0, 1e-4, 2e-3, 5e-2)
    }

    eddy_env.eddy.save_calibration_3d()

    mock_file_open.assert_called_once()
    import os
    expected_path = os.path.expanduser("~/printer_data/config/eddy_calibration_3d.csv")
    assert mock_file_open.call_args[0][0] == expected_path
    assert mock_file_open.call_args[0][1] == "w"

    eddy_env.configfile.set.assert_any_call(
        "probe_eddy_ng fly_eddy_probe",
        "calibration_3d_baseline_12",
        "7.500000000000e-07,1.000000000000e+00,2.000000000000e+00,3.000000000000e+00,4.000000000000e+00,0.1000,5.0000,1000000.00,2000000.00"
    )
    eddy_env.configfile.set.assert_any_call(
        "probe_eddy_ng fly_eddy_probe",
        "calibration_3d_drift_12",
        "30.0000,1.000000000000e-04,2.000000000000e-03,5.000000000000e-02"
    )


def test_load_calibration_3d_from_config_success(eddy_env):
    eddy_env.configfile.autosave.fileconfig.getint.return_value = None
    eddy_env.config.getintlist.return_value = [12]

    baseline_val = "7.500000000000e-07,1.000000000000e+00,2.000000000000e+00,3.000000000000e+00,4.000000000000e+00,0.1000,5.0000,1000000.00,2000000.00"
    htof_val = ",".join(["1.0"]*10)
    drift_val = "30.0000,1.000000000000e-04,2.000000000000e-03,5.000000000000e-02"

    def mock_config_get(name, default=None, **kwargs):
        if name == "calibration_3d_baseline_12":
            return baseline_val
        elif name == "calibration_3d_htof_12":
            return htof_val
        elif name == "calibration_3d_drift_12":
            return drift_val
        return default
    eddy_env.config.get.side_effect = mock_config_get

    with patch.object(probe_eddy_ng.ProbeEddyParams, 'load_from_config'):
        eddy_instance = probe_eddy_ng.ProbeEddy(eddy_env.config)

    assert 12 in eddy_instance._dc_to_fmap
    fmap = eddy_instance._dc_to_fmap[12]
    assert isinstance(fmap._ftoh, probe_eddy_ng.ProbeEddyRationalFit)
    assert fmap._ftoh.p_inf == 0.75e-6
    assert fmap._ftoh.coef.tolist() == [1.0, 2.0, 3.0, 4.0]
    assert fmap.height_range == [0.1, 5.0]
    assert fmap.freq_range == [1e6, 2e6]

    assert 12 in eddy_instance._dc_to_drift_coefs
    assert eddy_instance._dc_to_drift_coefs[12] == (30.0, 1e-4, 2e-3, 5e-2)


def test_fallback_to_2d_calibration_warning(eddy_env):
    eddy_env.configfile.autosave.fileconfig.getint.return_value = None
    eddy_env.config.getintlist.return_value = [12]

    # 3D configuration values do not exist
    eddy_env.config.get.side_effect = lambda name, default=None, **kwargs: default
    eddy_env.config.getint.return_value = 5

    with patch.object(probe_eddy_ng.ProbeEddyParams, 'load_from_config'):
        with patch.object(probe_eddy_ng.ProbeEddyFrequencyMap, 'load_from_config') as mock_load:
            mock_load.return_value = True
            eddy_instance = probe_eddy_ng.ProbeEddy(eddy_env.config)

            assert 12 in eddy_instance._dc_to_fmap
            assert mock_load.called

            # Verify the Z-drift warning is logged
            warning_msg = (
                "EDDYng: Using legacy 2D Z-calibration. 3D Z-drift temperature "
                "calibration is missing. We recommend running PROBE_EDDY_NG_SETUP."
            )
            assert any(warning_msg in msg for msg in eddy_instance.params._warning_msgs)


def test_setup_next_calculates_non_trivial_cubic_drift(eddy_env):
    gcmd = MagicMock()
    eddy_env.eddy._setup_target_temp = 45.0

    eddy_env.eddy._temp_sensor_obj = MagicMock()
    eddy_env.eddy.get_sensor_temp = MagicMock()
    eddy_env.eddy.get_sensor_temp.side_effect = [30.0, 35.0, 40.0, 45.0]

    mock_baseline_map = MagicMock()
    mock_baseline_map.height_range = [0.0, 10.0]
    mock_baseline_map._htof = lambda h: h
    mock_baseline_map._ftoh = lambda p: p
    mock_baseline_map.freq_spread.return_value = 1.0

    mock_map_35 = MagicMock()
    mock_map_35.height_range = [0.0, 10.0]
    mock_map_35._htof = lambda h: h + 0.3125
    mock_map_35._ftoh = lambda p: p
    mock_map_35.freq_spread.return_value = 1.0

    mock_map_40 = MagicMock()
    mock_map_40.height_range = [0.0, 10.0]
    mock_map_40._htof = lambda h: h + 0.8
    mock_map_40._ftoh = lambda p: p
    mock_map_40.freq_spread.return_value = 1.0

    mock_map_45 = MagicMock()
    mock_map_45.height_range = [0.0, 10.0]
    mock_map_45._htof = lambda h: h + 1.5375
    mock_map_45._ftoh = lambda p: p
    mock_map_45.freq_spread.return_value = 1.0

    eddy_env.eddy._create_mapping = MagicMock()
    eddy_env.eddy._create_mapping.side_effect = [
        (mock_baseline_map, 0.1, 0.2),
        (mock_map_35, 0.1, 0.2),
        (mock_map_40, 0.1, 0.2),
        (mock_map_45, 0.1, 0.2),
    ]

    eddy_env.eddy._log_calibration_sweep_data = MagicMock()
    eddy_env.eddy._log_msg = MagicMock()

    current_dc = [None]
    def mock_set_drive_current(dc):
        current_dc[0] = dc
    eddy_env.eddy._sensor.set_drive_current.side_effect = mock_set_drive_current

    def mock_read_one_value():
        val = MagicMock()
        if current_dc[0] == 12:
            val.freq = 10000000.0
            val.status = 0
        else:
            val.freq = 0.0
            val.status = 1
        return val
    eddy_env.eddy._sensor.read_one_value.side_effect = mock_read_one_value

    eddy_env.printer.lookup_object.side_effect = lambda name, default=None: {
        "configfile": eddy_env.configfile,
    }.get(name, MagicMock())

    eddy_env.eddy._gcode.run_script_from_command = MagicMock()
    eddy_env.eddy.save_config = MagicMock()
    eddy_env.eddy._z_not_homed = MagicMock()

    eddy_env.eddy.params.min_drive_current = 0
    eddy_env.eddy.params.max_drive_current = 31

    with patch.object(eddy_env.eddy, '_xy_homed', return_value=True), \
         patch.object(eddy_env.eddy, '_z_homed', return_value=True), \
         patch.object(eddy_env.eddy, 'do_one_tap') as mock_tap:

        mock_tap_res = MagicMock()
        mock_tap_res.error = None
        mock_tap_res.probe_z = 1.0
        mock_tap_res.overshoot = 0.0
        mock_tap.return_value = mock_tap_res

        eddy_env.eddy.cmd_SETUP_next(gcmd, [0.0, 0.0, 0.0])

    logged_msgs = [call[0][0] for call in eddy_env.eddy._log_msg.call_args_list]
    coef_msg = [msg for msg in logged_msgs if "Thermal Z-drift cubic coefficients for DC 12" in msg]
    assert len(coef_msg) == 1
    assert "c3 = 1.0000e-04" in coef_msg[0]
    assert "c2 = 2.0000e-03" in coef_msg[0]
    assert "c1 = 5.0000e-02" in coef_msg[0]


def test_rational_fit_evaluation():
    # p_inf = 0.77 us, coefs = [1.0, 2.0, 3.0, 4.0]
    fit = probe_eddy_ng.ProbeEddyRationalFit(0.77e-6, [1.0, 2.0, 3.0, 4.0], [0.70e-6, 0.77e-6])

    # Test nominal negative diff evaluation (realistic case where p < p_inf): p = 0.76 us
    p_eval = 0.76e-6
    # Expected: diff_us = 0.76 - 0.77 = -0.01. x = -100.
    # h = 1.0 + 2.0*-100 + 3.0*10000 + 4.0*-1000000 = -3970199.0
    assert abs(fit(p_eval) - -3970199.0) < 1e-5

    # Test numpy array input with negative differences
    p_eval_arr = np.array([0.76e-6, 0.75e-6])
    # For 0.75 us: diff_us = -0.02. x = -50.
    # h = 1.0 + 2.0*-50 + 3.0*2500 + 4.0*-125000 = -492599.0
    res = fit(p_eval_arr)
    assert res.shape == (2,)
    assert abs(res[0] - -3970199.0) < 1e-5
    assert abs(res[1] - -492599.0) < 1e-5

    # Test boundary case: p = p_inf (divide by zero guard, diff -> 0)
    # Should evaluate safely using diff_us = -1e-9 (x = -1e9)
    expected_pole = 1.0 + 2.0*-1e9 + 3.0*1e18 + 4.0*-1e27
    assert abs(fit(0.77e-6) / 1e27 - expected_pole / 1e27) < 1e-5

    # Test positive difference boundary guard (p > p_inf, diff > 0)
    p_above = 0.78e-6
    # Expected: diff_us = 0.01. x = 100.
    # h = 1.0 + 2.0*100 + 3.0*10000 + 4.0*1000000 = 4030201.0
    assert abs(fit(p_above) - 4030201.0) < 1e-5


def test_calibrate_from_values_fits_rational(eddy_env):
    fmap = probe_eddy_ng.ProbeEddyFrequencyMap(eddy_env.eddy)

    # Generate some synthetic frequency vs height data
    # We model f = 1.33 MHz (period = 0.751 us) to 1.32 MHz (period = 0.757 us)
    h_vals = np.linspace(0.1, 4.0, 100)
    # Linear shift in period
    p_vals = 0.751e-6 + (0.757e-6 - 0.751e-6) * (h_vals / 4.0)
    freq_vals = 1.0 / p_vals

    fth_fit, htf_fit = fmap.calibrate_from_values(
        drive_current=12,
        raw_times=np.arange(len(h_vals)).tolist(),
        raw_freqs_list=freq_vals.tolist(),
        raw_heights_list=h_vals.tolist(),
        raw_vels_list=None,
        report_errors=False,
        write_debug_files=False
    )

    assert fth_fit is not None
    assert isinstance(fmap._ftoh, probe_eddy_ng.ProbeEddyRationalFit)
    assert fmap._ftoh.coef.shape == (4,)
    # Check domain corresponds to min/max period
    assert abs(fmap._ftoh.domain[0] - p_vals.min()) < 1e-9
    assert abs(fmap._ftoh.domain[1] - p_vals.max()) < 1e-9

    # Verify fit error is extremely small (sub-micron / < 1e-4)
    assert fth_fit < 0.001
    h_hat = fmap._ftoh(p_vals)
    rmse = np.sqrt(np.mean((h_vals - h_hat) ** 2))
    assert rmse < 0.001


def test_temperature_interpolation_zero_division_guard(eddy_env):
    # Setup self._dc_to_temp_fmaps with a duplicate temperature or t_low == t_high case
    mock_map_1 = MagicMock()
    mock_map_1.height_to_freq.return_value = 12000000.0
    mock_map_1.freq_to_height.return_value = 1.0
    mock_map_1.freqs_to_heights_np.return_value = np.array([1.0])

    mock_map_2 = MagicMock()
    mock_map_2.height_to_freq.return_value = 13000000.0
    mock_map_2.freq_to_height.return_value = 2.0
    mock_map_2.freqs_to_heights_np.return_value = np.array([2.0])

    eddy_env.eddy._dc_to_temp_fmaps[16] = [
        (25.0, mock_map_1),
        (25.0, mock_map_2)
    ]

    eddy_env.eddy.get_sensor_temp = MagicMock(return_value=25.0)
    eddy_env.eddy._sensor.get_drive_current.return_value = 16

    # Should evaluate safely without ZeroDivisionError
    f = eddy_env.eddy.height_to_freq(1.0, 16)
    h = eddy_env.eddy.freq_to_height(12500000.0, 16)
    h_arr = eddy_env.eddy.freqs_to_heights_np(np.array([12500000.0]), 16)

    assert f == 12000000.0
    assert h == 1.0
    assert h_arr[0] == 1.0


def test_probe_eddy_probe_result():
    # Test valid property
    empty_res = probe_eddy_ng.ProbeEddyProbeResult(samples=[], errors=0)
    assert not empty_res.valid

    # Create synthetic result
    res = probe_eddy_ng.ProbeEddyProbeResult.make([0.0, 1.0, 2.0], [1.0, 2.0, 3.0], errors=1)
    assert res.valid
    assert res.mean == 2.0
    assert res.median == 2.0
    assert res.min_value == 1.0
    assert res.max_value == 3.0
    assert res.tstart == 0.0
    assert res.tend == 2.0
    assert res.errors == 1

    # Test value property with default mode (USE_MEAN_FOR_VALUE = False, uses median)
    probe_eddy_ng.ProbeEddyProbeResult.USE_MEAN_FOR_VALUE = False
    assert res.value == 2.0

    # Test value property with mean mode
    probe_eddy_ng.ProbeEddyProbeResult.USE_MEAN_FOR_VALUE = True
    assert res.value == 2.0

    # Test stddev calculation
    # samples: 1.0, 2.0, 3.0. value = 2.0
    # stddev_sum = (1-2)^2 + (2-2)^2 + (3-2)^2 = 1 + 0 + 1 = 2
    # stddev = sqrt(2 / 3)
    assert abs(res.stddev - (2.0/3.0)**0.5) < 1e-5

    # Reset USE_MEAN_FOR_VALUE for safety
    probe_eddy_ng.ProbeEddyProbeResult.USE_MEAN_FOR_VALUE = False

    # Test __format__
    formatted_v = format(res, "v")
    assert formatted_v == "2.000"

    formatted_default = format(res, "")
    assert "avg=2.000" in formatted_default
    assert "1.000 to 3.000" in formatted_default


def test_probe_eddy_params_validation():
    # Test str_to_floatlist static method
    assert probe_eddy_ng.ProbeEddyParams.str_to_floatlist(None) is None
    assert probe_eddy_ng.ProbeEddyParams.str_to_floatlist("1.0, 2.0 3.0") == [1.0, 2.0, 3.0]
    with pytest.raises(Exception):
        probe_eddy_ng.ProbeEddyParams.str_to_floatlist("abc, def")

    # Test is_default_butter_config
    params = probe_eddy_ng.ProbeEddyParams()
    assert params.is_default_butter_config()
    params.tap_butter_lowcut = 10.0
    assert not params.is_default_butter_config()

    # Test validation exception: calibration_z_max must be at least home_trigger_safe_start_offset+home_trigger_height+1.0
    mock_config = MagicMock()
    mock_printer = MagicMock()
    mock_config.get_printer.return_value = mock_printer
    mock_printer.config_error.side_effect = Exception("ConfigError")

    # Setup valid params first
    params = probe_eddy_ng.ProbeEddyParams()
    params.calibration_z_max = 5.0
    params.home_trigger_safe_start_offset = 1.0
    params.home_trigger_height = 2.0
    params.x_offset = 1.0
    params.y_offset = 1.0

    # Trigger Z-max validation failure
    params.calibration_z_max = 2.0
    with pytest.raises(Exception, match="ConfigError"):
        params.validate(mock_config)

    # Reset Z-max, trigger nozzle offset validation failure (allow_unsafe is False and x_offset = y_offset = 0.0)
    params.calibration_z_max = 5.0
    params.x_offset = 0.0
    params.y_offset = 0.0
    params.allow_unsafe = False
    with pytest.raises(Exception, match="ConfigError"):
        params.validate(mock_config)

    # Check that allow_unsafe = True bypasses nozzle offset validation
    params.allow_unsafe = True
    params.validate(mock_config)  # Should pass

    # Trigger home_trigger_height <= tap_trigger_safe_start_height validation failure
    params.home_trigger_height = 1.5
    params.tap_trigger_safe_start_height = 2.0
    with pytest.raises(Exception, match="ConfigError"):
        params.validate(mock_config)


def test_probe_eddy_frequency_map_config_operations(eddy_env):
    import base64
    import pickle
    fmap = probe_eddy_ng.ProbeEddyFrequencyMap(eddy_env.eddy)
    assert fmap.drive_current == 0

    # Mock empty calibration in config
    eddy_env.config.get.side_effect = lambda name, default=None, **kwargs: default
    fmap.load_from_config(eddy_env.config, 12)
    assert fmap._ftoh is None
    assert fmap.drive_current == 0

    # Mock invalid/old calibration version
    old_data = {"v": 1, "ftoh": "dummy"}
    old_calibstr = base64.b64encode(pickle.dumps(old_data)).decode()

    eddy_env.config.get.side_effect = lambda name, default=None, **kwargs: old_calibstr if name == "calibration_12" else default

    res = fmap.load_from_config(eddy_env.config, 12)
    assert res is False

    # Mock drive current mismatch
    mismatch_data = {"v": 5, "ftoh": "dummy", "dc": 14}
    mismatch_calibstr = base64.b64encode(pickle.dumps(mismatch_data)).decode()
    eddy_env.config.get.side_effect = lambda name, default=None, **kwargs: mismatch_calibstr if name == "calibration_12" else default

    with pytest.raises(Exception, match="drive current mismatch"):
        fmap.load_from_config(eddy_env.config, 12)

    # Mock successful load
    mock_ftoh = "dummy_ftoh"
    mock_htof = "dummy_htof"
    valid_data = {
        "v": 5,
        "ftoh": mock_ftoh,
        "ftoh_high": None,
        "htof": mock_htof,
        "h_range": (0.1, 5.0),
        "f_range": (1e6, 2e6),
        "dc": 12
    }
    valid_calibstr = base64.b64encode(pickle.dumps(valid_data)).decode()
    eddy_env.config.get.side_effect = lambda name, default=None, **kwargs: valid_calibstr if name == "calibration_12" else default

    res = fmap.load_from_config(eddy_env.config, 12)
    assert res is True
    assert fmap.drive_current == 12
    assert fmap._ftoh == mock_ftoh
    assert fmap.height_range == (0.1, 5.0)

    # Test save_calibration
    fmap.save_calibration()
    eddy_env.configfile.set.assert_called_once()
    args = eddy_env.configfile.set.call_args[0]
    assert args[1] == "calibration_12"


def test_fmap_calibrate_from_values_errors(eddy_env):
    fmap = probe_eddy_ng.ProbeEddyFrequencyMap(eddy_env.eddy)

    # Mismatched length raises ValueError
    with pytest.raises(ValueError, match="freqs and heights must be the same length"):
        fmap.calibrate_from_values(12, [], [1.0], [], None, False, False)

    # Empty lists return (None, None)
    res = fmap.calibrate_from_values(12, [], [], [], None, False, False)
    assert res == (None, None)

    # Validations fail when max_height < 2.5 (under report_errors = True)
    h_vals = [0.1, 1.0, 2.0]
    f_vals = [1.2e6, 1.3e6, 1.4e6]
    t_vals = [0.0, 1.0, 2.0]

    eddy_env.eddy.params.allow_unsafe = False
    res = fmap.calibrate_from_values(12, t_vals, f_vals, h_vals, None, True, False)
    assert res == (None, None)


def test_probe_eddy_logging(eddy_env):
    eddy_env.eddy._gcode = MagicMock()

    with patch("probe_eddy_ng.probe_eddy_ng.logging") as mock_logging:
        eddy_env.eddy._log_error("test error")
        mock_logging.error.assert_called_with("fly_eddy_probe: test error")
        eddy_env.eddy._gcode.respond_raw.assert_called_with("!! EDDYng: test error\n")

        eddy_env.eddy._log_warning("test warning")
        mock_logging.warning.assert_called_with("fly_eddy_probe: test warning")

        eddy_env.eddy._log_msg("test msg")
        mock_logging.info.assert_called_with("fly_eddy_probe: test msg")
        eddy_env.eddy._gcode.respond_info.assert_called_with("test msg", log=False)

        eddy_env.eddy._log_info("test info")
        mock_logging.info.assert_called_with("fly_eddy_probe: test info")

        eddy_env.eddy.params.debug = True
        eddy_env.eddy._log_debug("test debug")
        mock_logging.info.assert_called_with("fly_eddy_probe: test debug")


def test_probe_eddy_get_default_butter_sos(eddy_env):
    # Test valid rates
    sos_250 = eddy_env.eddy.get_default_butter_sos(250)
    assert sos_250 is not None
    assert len(sos_250) == 2

    sos_500 = eddy_env.eddy.get_default_butter_sos(500)
    assert sos_500 is not None
    assert len(sos_500) == 2

    # Test invalid rate
    sos_invalid = eddy_env.eddy.get_default_butter_sos(100)
    assert sos_invalid is None


def test_probe_eddy_drive_current_management(eddy_env):
    eddy_env.eddy._sensor.get_drive_current.return_value = 16
    assert eddy_env.eddy.current_drive_current() == 16

    # Test reset_drive_current with no drive current configured
    eddy_env.eddy._tap_drive_current = 0
    eddy_env.eddy._reg_drive_current = 0

    eddy_env.printer.command_error.side_effect = Exception("CommandError")
    with pytest.raises(Exception, match="CommandError"):
        eddy_env.eddy.reset_drive_current(tap=True)

    # Test reset_drive_current when drive current is configured
    eddy_env.eddy._tap_drive_current = 16
    eddy_env.eddy._reg_drive_current = 12

    eddy_env.eddy.reset_drive_current(tap=True)
    eddy_env.eddy._sensor.set_drive_current.assert_called_with(16)

    eddy_env.eddy.reset_drive_current(tap=False)
    eddy_env.eddy._sensor.set_drive_current.assert_called_with(12)


def test_probe_eddy_define_commands(eddy_env):
    gcode = MagicMock()
    eddy_env.eddy.define_commands(gcode)

    registered_commands = [call[0][0] for call in gcode.register_command.call_args_list]
    assert "PROBE_EDDY_NG_STATUS" in registered_commands
    assert "PROBE_EDDY_NG_CALIBRATE" in registered_commands
    assert "PROBE_EDDY_NG_SETUP" in registered_commands
    assert "PROBE_EDDY_NG_TAP" in registered_commands
    assert "PES" in registered_commands
    assert "EDDYNG_BED_MESH_EXPERIMENTAL" in registered_commands


def test_probe_eddy_offset_commands(eddy_env):
    eddy_env.eddy._tap_offset = 1.0
    eddy_env.eddy._tap_adjust_z = 0.5
    eddy_env.eddy.params.tap_adjust_z = 0.5

    # Test cmd_SET_TAP_OFFSET
    gcmd = MagicMock()
    gcmd.get_float.side_effect = lambda param, default=None: 2.0 if param == "VALUE" else default
    eddy_env.eddy.cmd_SET_TAP_OFFSET(gcmd)
    assert eddy_env.eddy._tap_offset == 2.0

    # With ADJUST
    gcmd = MagicMock()
    gcmd.get_float.side_effect = lambda param, default=None: -0.5 if param == "ADJUST" else default
    eddy_env.eddy.cmd_SET_TAP_OFFSET(gcmd)
    assert eddy_env.eddy._tap_offset == 1.5

    # Test cmd_SET_TAP_ADJUST_Z
    gcmd = MagicMock()
    gcmd.get_float.side_effect = lambda param, default=None: 0.75 if param == "VALUE" else default

    eddy_env.printer.lookup_object.side_effect = lambda name, default=None: {
        "configfile": eddy_env.configfile,
    }.get(name, MagicMock())

    eddy_env.eddy.cmd_SET_TAP_ADJUST_Z(gcmd)
    assert eddy_env.eddy._tap_adjust_z == 0.75
    eddy_env.configfile.set.assert_called_with(eddy_env.eddy._full_name, "tap_adjust_z", "0.75")


def test_cmd_z_offset_apply_probe(eddy_env):
    gcmd = MagicMock()
    gcode_move = MagicMock()
    gcode_move.get_status.return_value = {"homing_origin": MagicMock(z=0.1)}

    eddy_env.printer.lookup_object.side_effect = lambda name, default=None: {
        "gcode_move": gcode_move,
        "configfile": eddy_env.configfile
    }.get(name, MagicMock())

    eddy_env.eddy.params.tap_adjust_z = 0.5
    eddy_env.eddy._last_tap_gcode_adjustment = 0.05

    eddy_env.eddy.cmd_Z_OFFSET_APPLY_PROBE(gcmd)

    # Expected offset = 0.1 + 0.5 - 0.05 = 0.55
    eddy_env.configfile.set.assert_called_with(
        eddy_env.eddy._full_name,
        "tap_adjust_z",
        "0.550"
    )


def test_cmd_clear_calibration(eddy_env):
    gcmd = MagicMock()
    eddy_env.eddy._dc_to_fmap = {
        12: MagicMock(),
        16: MagicMock()
    }
    eddy_env.eddy.save_config = MagicMock()

    # Test clear single drive current (non-existent)
    gcmd.get_int.return_value = 14
    eddy_env.printer.command_error.side_effect = Exception("CommandError")
    with pytest.raises(Exception, match="CommandError"):
        eddy_env.eddy.cmd_CLEAR_CALIBRATION(gcmd)

    # Test clear single drive current (existent)
    gcmd.get_int.return_value = 12
    eddy_env.eddy.cmd_CLEAR_CALIBRATION(gcmd)
    assert 12 not in eddy_env.eddy._dc_to_fmap
    assert 16 in eddy_env.eddy._dc_to_fmap

    # Test clear all drive currents
    gcmd.get_int.return_value = -1
    eddy_env.eddy.cmd_CLEAR_CALIBRATION(gcmd)
    assert len(eddy_env.eddy._dc_to_fmap) == 0

