# The tap plot's filter is a plain-Python copy of the firmware's (scipy.signal
# is too slow to import mid-tap); check it against scipy.
import types

import numpy as np
import pytest

from probe_eddy_ng import plots
from probe_eddy_ng.params import ProbeEddyParams

scipy_signal = pytest.importorskip("scipy.signal")


@pytest.mark.parametrize("rate", [250, 500])
def test_butter_tap_matches_scipy(rate):
    params = ProbeEddyParams()
    sos = scipy_signal.butter(
        params.tap_butter_order,
        [params.tap_butter_lowcut, params.tap_butter_highcut],
        btype="bandpass",
        fs=rate,
        output="sos",
    )
    rng = np.random.default_rng(0)
    freqs = 3e6 + np.cumsum(rng.normal(size=1000)) * 50
    sampler = types.SimpleNamespace(times=list(np.arange(1000) / rate), freqs=list(freqs), memos={"tap_safe_freq": freqs.min()})
    eddy = types.SimpleNamespace(_tap_sos=sos.tolist())

    _, filtered, first = plots.compute_butter_tap(eddy, sampler)

    f = freqs[first:]
    np.testing.assert_allclose(filtered, scipy_signal.sosfilt(sos, f - f[0]), rtol=0, atol=1e-6)
