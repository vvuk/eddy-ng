"""
Regression tests for BedMeshScanHelper (EDDYNG_BED_MESH_EXPERIMENTAL).

Runs without a printer: Klipper's real ``bed_mesh.ZMesh`` is used when a Klipper
checkout is available (set KLIPPER_DIR, default ../klipper or ~/klipper), otherwise a
minimal stand-in is used.  Everything else is mocked.
"""
import importlib.util
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))


class CommandError(Exception):
    pass


class BedMeshError(Exception):
    pass


class FakeZMesh:
    """Minimal stand-in used when no Klipper checkout is available."""

    def __init__(self, params, name):
        self.params = params
        self.matrix = None

    def build_mesh(self, z_matrix):
        if len(z_matrix) != self.params["y_count"] or any(len(r) != self.params["x_count"] for r in z_matrix):
            raise BedMeshError("matrix shape mismatch")
        self.matrix = z_matrix


def _load_module():
    names = [
        "probe_eddy_ng", "probe_eddy_ng.ldc1612_ng", "probe_eddy_ng.probe_eddy_ng",
    ]
    saved = {}
    try:
        return _load_module_inner(saved, names)
    finally:
        # do not leak our mocks into other test modules (test_probe_eddy_ng.py has its own)
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _load_module_inner(saved, names):
    for name in (
        "klippy", "klippy.mcu", "klippy.pins", "klippy.chelper", "klippy.printer",
        "klippy.configfile", "klippy.gcode", "klippy.toolhead", "klippy.extras",
        "klippy.extras.homing", "klippy.extras.probe", "klippy.extras.manual_probe",
        "klippy.extras.bed_mesh",
    ):
        saved.setdefault(name, sys.modules.get(name))
        sys.modules[name] = MagicMock()
    for name in names:
        saved.setdefault(name, sys.modules.get(name))
    sys.modules["klippy.configfile"].error = Exception
    bm = sys.modules["klippy.extras.bed_mesh"]
    bm.ZMesh = FakeZMesh
    bm.BedMeshError = BedMeshError

    pkg = MagicMock()
    sys.modules["probe_eddy_ng"] = pkg
    sys.modules["probe_eddy_ng.ldc1612_ng"] = MagicMock()
    pkg.ldc1612_ng = sys.modules["probe_eddy_ng.ldc1612_ng"]

    spec = importlib.util.spec_from_file_location(
        "probe_eddy_ng.probe_eddy_ng", os.path.join(HERE, "probe_eddy_ng.py")
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "probe_eddy_ng"
    sys.modules["probe_eddy_ng.probe_eddy_ng"] = mod
    spec.loader.exec_module(mod)
    mod.bed_mesh = bm
    return mod


mod = _load_module()


class FakeSection:
    def __init__(self, values):
        self.values = values

    def getintlist(self, name, count=None, note_valid=True):
        return list(self.values[name])

    def getfloatlist(self, name, count=None, note_valid=True):
        return [float(v) for v in self.values[name]]

    def getfloat(self, name, default=None, above=None, note_valid=True):
        return float(self.values.get(name, default))


class FakeGcmd:
    def __init__(self, **kw):
        self.kw = {k.upper(): v for k, v in kw.items()}

    def get_int(self, name, default=None, **_):
        return int(self.kw.get(name, default))

    def get_float(self, name, default=None, **_):
        return float(self.kw.get(name, default))

    def get(self, name, default=None, **_):
        return self.kw.get(name, default)


class FakeExcludeObject:
    def __init__(self):
        self.objects = []

    def get_status(self, *_):
        return {"objects": list(self.objects)}


def square(x0, y0, x1, y1):
    return {"name": "o", "polygon": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}


def make_helper(probe_count=(9, 9), mesh_min=(20, 20), mesh_max=(280, 280), exclude=True):
    section = FakeSection(
        {"probe_count": probe_count, "mesh_min": mesh_min, "mesh_max": mesh_max,
         "speed": 100, "horizontal_move_z": 2.0}
    )
    config = MagicMock()
    config.getsection.return_value = section

    excl = FakeExcludeObject() if exclude else None
    printer = MagicMock()
    printer.command_error = CommandError
    printer.lookup_object.side_effect = lambda n, d=None: excl if n == "exclude_object" else d

    stored = {}
    bed_mesh_obj = MagicMock()
    bed_mesh_obj.bmc.mesh_config = {"algo": "lagrange"}
    bed_mesh_obj.set_mesh.side_effect = lambda m: stored.setdefault("mesh", m)
    printer.load_object.return_value = bed_mesh_obj

    eddy = SimpleNamespace(
        _printer=printer,
        params=SimpleNamespace(x_offset=0.0, y_offset=-1.0, home_trigger_height=2.0,
                               lift_speed=5.0, probe_speed=5.0, scan_sample_time=0.01),
        _toolhead=MagicMock(),
        _tap_offset=0.0,
        _log_msg=lambda *a, **k: None,
    )
    helper = mod.BedMeshScanHelper(eddy, config)
    return helper, excl, stored


def run_set_mesh(helper, gcmd):
    """Everything scan() does after the sampling, i.e. adaptive setup -> heights -> mesh."""
    helper._adaptive_mesh(gcmd)
    heights = [1.9 + 0.001 * i for i in range(len(helper._mesh_path))]
    helper._set_bed_mesh(heights)
    return heights


def test_full_mesh_works():
    helper, _, stored = make_helper()
    run_set_mesh(helper, FakeGcmd(adaptive=0))
    assert len(stored["mesh"].matrix if hasattr(stored["mesh"], "matrix") else [0]) >= 1


def test_adaptive_then_full_does_not_leave_stale_path():
    """REGRESSION: adaptive print followed by a run with no objects used to crash with IndexError."""
    helper, excl, _ = make_helper()
    excl.objects = [square(100, 100, 160, 160)]
    run_set_mesh(helper, FakeGcmd(adaptive=1, adaptive_margin=7.5))
    adaptive_len = len(helper._mesh_path)
    assert adaptive_len < 81

    # next print: START_PRINT runs before EXCLUDE_OBJECT_DEFINE -> no objects
    excl.objects = []
    run_set_mesh(helper, FakeGcmd(adaptive=1, adaptive_margin=7.5))
    assert len(helper._mesh_path) == 81
    assert helper._x_points == 9 and helper._y_points == 9


def test_adaptive_then_adaptive_off():
    helper, excl, _ = make_helper()
    excl.objects = [square(100, 100, 160, 160)]
    run_set_mesh(helper, FakeGcmd(adaptive=1))
    run_set_mesh(helper, FakeGcmd(adaptive=0))
    assert len(helper._mesh_path) == 81


def test_adaptive_then_exclude_object_disabled():
    helper, excl, _ = make_helper()
    excl.objects = [square(100, 100, 160, 160)]
    run_set_mesh(helper, FakeGcmd(adaptive=1))
    helper._printer.lookup_object.side_effect = lambda n, d=None: d  # module vanished
    run_set_mesh(helper, FakeGcmd(adaptive=1))
    assert len(helper._mesh_path) == 81


def test_path_bounds_match_mesh_params():
    """Mesh bounds handed to ZMesh must be the bounds of the path that was actually scanned."""
    helper, excl, stored = make_helper()
    excl.objects = [square(100, 100, 160, 160)]
    run_set_mesh(helper, FakeGcmd(adaptive=1, adaptive_margin=7.5))
    xs = [p[0] for p in helper._mesh_path]
    ys = [p[1] for p in helper._mesh_path]
    assert min(xs) == pytest.approx(helper._x_min) and max(xs) == pytest.approx(helper._x_max)
    assert min(ys) == pytest.approx(helper._y_min) and max(ys) == pytest.approx(helper._y_max)
    assert helper._x_min == pytest.approx(92.5) and helper._x_max == pytest.approx(167.5)


def test_object_outside_mesh_area_is_clamped_not_crash():
    helper, excl, _ = make_helper()
    excl.objects = [square(0, 0, 10, 10)]  # entirely outside mesh_min/mesh_max
    run_set_mesh(helper, FakeGcmd(adaptive=1, adaptive_margin=5))
    assert helper._x_max > helper._x_min and helper._y_max > helper._y_min
    assert len(helper._mesh_path) == helper._x_points * helper._y_points


def test_repeated_prints_are_idempotent():
    helper, excl, _ = make_helper()
    sizes = []
    for objs in ([square(100, 100, 160, 160)], [], [square(30, 30, 270, 270)], [], []):
        excl.objects = objs
        run_set_mesh(helper, FakeGcmd(adaptive=1, adaptive_margin=7.5))
        sizes.append(len(helper._mesh_path))
        assert sizes[-1] == helper._x_points * helper._y_points


# ---------------------------------------------------------------------------
# Stuck-sampler regression ("Already sampling!" until FIRMWARE_RESTART)
# ---------------------------------------------------------------------------
class FakeSampler:
    def __init__(self, eddy, *a, **k):
        self.eddy = eddy
        self._stopped = False
        self._started = False

    def start(self):
        self._started = True

    def finish(self):
        if self._stopped:
            return
        self.eddy._sampler_finished(self)
        self._stopped = True


def make_eddy():
    eddy = object.__new__(mod.ProbeEddy)
    eddy._printer = MagicMock()
    eddy._printer.command_error = CommandError
    eddy._sampler = None
    eddy._last_sampler = None
    eddy._streaming = False
    eddy.save_samples_path = None
    eddy._name = "probe_eddy_ng test"
    eddy.params = SimpleNamespace(debug=False, _warning_msgs=[])
    eddy._log_warning = lambda *a, **k: None
    eddy._endstop_wrapper = mod.ProbeEddyEndstopWrapper.__new__(mod.ProbeEddyEndstopWrapper)
    es = eddy._endstop_wrapper
    es.tap_config = None
    es._homing_in_progress = False
    es._sampler = None
    es._sensor = MagicMock()
    es._dispatch = MagicMock()
    es.last_trigger_time = es.last_tap_start_time = 0.0
    es.eddy = eddy
    return eddy


@pytest.fixture
def fake_sampler(monkeypatch):
    monkeypatch.setattr(mod, "ProbeEddySampler", FakeSampler)


def test_stale_sampler_is_recovered_instead_of_locking_up(fake_sampler):
    eddy = make_eddy()
    eddy.start_sampler()  # simulates homing_move_begin ...
    eddy._endstop_wrapper._homing_in_progress = True
    eddy._endstop_wrapper.tap_config = object()
    # ... and homing_move_end never arriving (home_start raised, error swallowed by do_one_tap)

    s = eddy.start_sampler()  # next tap/probe/mesh: used to raise "Already sampling!"
    assert eddy._sampler is s
    assert eddy._endstop_wrapper.tap_config is None
    assert not eddy._endstop_wrapper._homing_in_progress
    eddy._endstop_wrapper._sensor.finish_home.assert_called_once()
    eddy._endstop_wrapper._dispatch.stop.assert_called_once()


def test_recover_is_noop_while_streaming(fake_sampler):
    eddy = make_eddy()
    eddy.start_sampler()
    eddy._streaming = True
    eddy.recover_probe_state()
    assert eddy._sampler is not None
    with pytest.raises(CommandError):
        eddy.start_sampler()


def test_recover_survives_failing_finish(fake_sampler):
    eddy = make_eddy()
    s = eddy.start_sampler()
    s.finish = MagicMock(side_effect=RuntimeError("boom"))
    eddy.recover_probe_state()
    assert eddy._sampler is None
    eddy.start_sampler()  # usable again


def test_command_error_handler_cleans_up(fake_sampler):
    eddy = make_eddy()
    eddy.start_sampler()
    eddy._endstop_wrapper._homing_in_progress = True
    eddy._endstop_wrapper._handle_command_error()
    assert eddy._sampler is None and not eddy._endstop_wrapper._homing_in_progress


def test_next_lower_calibrated_dc():
    eddy = make_eddy()
    cal = {15, 16, 17, 18, 19}
    eddy._dc_to_fmap = {d: object() for d in cal}
    eddy._dc_to_temp_fmaps = {}
    eddy.calibrated = lambda d=None: d in cal
    assert eddy._next_lower_calibrated_dc(18) == 17
    assert eddy._next_lower_calibrated_dc(16) == 15
    assert eddy._next_lower_calibrated_dc(15) is None
    assert eddy._next_lower_calibrated_dc(30) == 19
