from klippy.configfile import ConfigWrapper
try:
    from .probe_eddy_ng import ProbeEddy
except (ImportError, ValueError):
    from probe_eddy_ng import ProbeEddy

def load_config_prefix(config: ConfigWrapper):
    return ProbeEddy(config)
