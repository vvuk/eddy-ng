# AUTO_SETUP's search for the bed: stepping down until a step stops raising
# the frequency, on a simulated printer
import math

import numpy as np
import pytest

from fakes import CommandError, FakePrinter
from probe_eddy_ng.autosetup import FreqReading, ProbeEddyZeroFinder


def eddy_freq(gap):
    return 3.0e6 + 9e4 / (gap + 1.5)


# A printer whose bed is at bed_z in the toolhead's (unhomed) coordinates.
# Below it the nozzle can't go further, but the sensor still gets closer by
# `compliance` of how far the toolhead moves (the toolhead mount flexing).
class SimulatedPrinter:
    def __init__(self, bed_z, start_z=30.0, compliance=0.0, noise=1.0, sensor_range=8.0, resolution=0.0, seed=0):
        self.bed_z = bed_z
        self.z = start_z
        self.lowest_z = start_z
        self.compliance = compliance
        self.noise = noise
        self.sensor_range = sensor_range
        self.resolution = resolution
        self.rng = np.random.default_rng(seed)

    def get_position(self):
        return [0.0, 0.0, self.z, 0.0]

    def move_z(self, z, speed):
        self.z = z
        self.lowest_z = min(self.lowest_z, z)

    def read(self):
        h = self.z - self.bed_z
        gap = h if h > 0 else self.compliance * h
        if gap > self.sensor_range:
            return None
        samples = eddy_freq(gap) + self.rng.normal(0, self.noise, 25)
        if self.resolution:
            samples = np.round(samples / self.resolution) * self.resolution
        return FreqReading(float(np.mean(samples)), float(np.std(samples) / math.sqrt(len(samples))))


class FakeEddy:
    def __init__(self):
        self._printer = FakePrinter()
        self.messages = []

    def _log_msg(self, msg):
        self.messages.append(msg)

    _log_debug = _log_msg


def finder(sim, fine_step=0.2, coarse_step=2.0, contact_ratio=0.5, max_fine_travel=15.0):
    f = object.__new__(ProbeEddyZeroFinder)
    f.eddy = FakeEddy()
    f._printer = f.eddy._printer
    f._th = sim
    f.fine_step = fine_step
    f.coarse_step = coarse_step
    f.contact_ratio = contact_ratio
    f.max_fine_travel = max_fine_travel
    f.speed = f.lift_speed = 5.0
    f.freq_resolution = sim.resolution
    f._read = sim.read
    f._move_z = sim.move_z
    return f


@pytest.mark.parametrize("bed_z", [10.0, 10.05, 10.137, 10.19])
@pytest.mark.parametrize("compliance", [0.0, 0.1, 0.3])
def test_finds_rough_contact(bed_z, compliance):
    for seed in range(5):
        sim = SimulatedPrinter(bed_z, compliance=compliance, seed=seed)
        f = finder(sim)
        rough = f._find_rough_contact(floor_z=0.0)
        # within a fine step of the bed
        assert abs(rough - bed_z) <= f.fine_step + 1e-9, (seed, rough)
        # and it didn't push far into the bed
        assert sim.lowest_z >= bed_z - 2 * f.fine_step - 1e-9
        # ends up lifted off the bed
        assert sim.z > bed_z


def test_quantized_sensor():
    # samples quantized to a step larger than their noise
    sim = SimulatedPrinter(10.137, noise=0.2, resolution=4.0)
    f = finder(sim)
    rough = f._find_rough_contact(floor_z=0.0)
    assert abs(rough - 10.137) <= 0.2


def test_no_bed():
    sim = SimulatedPrinter(bed_z=-100.0, start_z=30.0)
    with pytest.raises(CommandError, match="bottom of the Z axis"):
        finder(sim)._find_rough_contact(floor_z=0.0)


def test_never_touches():
    # the sensor sees the bed, but the toolhead keeps getting closer forever
    sim = SimulatedPrinter(bed_z=10.0, compliance=1.0)
    with pytest.raises(CommandError, match="no bed contact detected"):
        finder(sim, max_fine_travel=5.0)._find_rough_contact(floor_z=-100.0)


@pytest.mark.parametrize(
    "rises, contact",
    [
        ([100, 120, 140], False),
        ([100, 120, 140, 160], False),
        # far below the largest rise
        ([100, 120, 140, 30], True),
        ([100, 120, 140, 160, 70], True),
        # short of what the last few rises predict, though above half of them
        ([100, 140, 200, 280, 200], True),
        # rises too small to tell from noise
        ([2, 3, 3.5, 0.5], False),
    ],
)
def test_is_contact(rises, contact):
    f = finder(SimulatedPrinter(0.0))
    assert f._is_contact([float(r) for r in rises], noise=1.0) == contact


def test_rise_noise():
    f = finder(SimulatedPrinter(0.0, resolution=4.0))
    a, b = FreqReading(3.0e6, 1.0), FreqReading(3.0e6, 1.0)
    # at least the frequency's own jitter, 2ppm
    assert f._rise_noise(a, b) == pytest.approx(6.0)
    # quantization
    f.freq_resolution = 10.0
    assert f._rise_noise(a, b) == pytest.approx(15.0)
    # the readings' noise
    assert f._rise_noise(FreqReading(3.0e6, 10.0), FreqReading(3.0e6, 10.0)) == pytest.approx(3 * math.hypot(10, 10))
