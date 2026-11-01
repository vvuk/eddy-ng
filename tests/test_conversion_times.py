# Recovering when the LDC1612 took each sample from when the MCU read it
import numpy as np
import pytest

from probe_eddy_ng.ldc1612_ng import ConversionTimeFit

# The sensor's clock is off from the MCU's by 80ppm
CLOCK_ERROR = 80e-6


# Reads of conversions completing every `period` (in sensor time): the MCU
# polls every period / 2.3 (in its own time, like start_streaming) and reads
# a conversion at the first poll after it completes, plus a little jitter.
# A fraction `late` of the reads are delayed by task latency. Returns (read
# times, conversion times).
def simulated_reads(sps=250, n=3000, t0=12.3456, late=0.0, seed=0):
    rng = np.random.default_rng(seed)
    period = (1.0 / sps) * (1 + CLOCK_ERROR)
    conv = t0 + np.arange(n) * period
    poll = (1.0 / sps) / 2.3
    reads = 12.0 + np.ceil((conv - 12.0) / poll) * poll + rng.uniform(0, 2e-6, n)
    delayed = rng.random(n) < late
    reads[delayed] += rng.uniform(0.3, 0.5, np.count_nonzero(delayed)) * period
    return reads, conv


# Feed the reads in batches the size the default 0.5s data interval gives
def run(fit, reads, sps=250):
    batch = sps // 2
    out = []
    for i in range(0, len(reads), batch):
        out.extend(fit.add_reads(list(reads[i : i + batch])))
    return np.array(out)


@pytest.mark.parametrize("sps", [250, 500])
@pytest.mark.parametrize("late", [0.0, 0.01])
def test_sample_times(sps, late):
    period = 1.0 / sps
    center = period / 2
    for seed in range(5):
        reads, conv = simulated_reads(sps=sps, late=late, seed=seed)
        times = run(ConversionTimeFit(center), reads, sps)
        err = (times - (conv - center)) / period
        # every sample is placed within a few percent of a period of the
        # middle of its conversion, including the late reads (the read
        # times themselves are up to ~half a period late), after the first
        # batch, which is a little less accurate
        first = sps // 2
        assert np.abs(err[:first]).max() < 0.1
        assert np.abs(err[first:]).max() < 0.03
        assert np.abs(err[first:]).mean() < 0.01


def test_before_enough_reads():
    fit = ConversionTimeFit(0.002)
    reads, _ = simulated_reads(n=10)
    # not fitted yet: the read times pass through
    assert fit.add_reads(list(reads)) == list(reads)


def test_phase_change():
    # the chip is reconfigured, and its conversions restart at another phase
    reads1, conv1 = simulated_reads(n=1000, seed=1)
    reads2, conv2 = simulated_reads(n=1000, t0=conv1[-1] + 0.1 + 0.0015, seed=2)
    fit = ConversionTimeFit(0.002)
    run(fit, reads1)
    times = run(fit, reads2)
    err = times - (conv2 - 0.002)
    assert np.abs(err).max() < 0.03 * 0.004


def test_single_sample_time():
    reads, conv = simulated_reads(n=500)
    fit = ConversionTimeFit(0.002)
    run(fit, reads)
    assert fit.to_sample_time(conv[-1] + 0.001) == pytest.approx(conv[-1] - 0.002, abs=0.03 * 0.004)
