# The whole module loads with the Klipper stand-ins (catches import errors)
def test_import():
    import probe_eddy_ng
    from probe_eddy_ng import compat

    assert probe_eddy_ng.ProbeEddy is not None
    assert not compat.IS_KALICO
    assert compat.HAS_PROBE_RESULT_TYPE
