from klippy.configfile import ConfigWrapper
from .probe_eddy_ng import ProbeEddy

def load_config_prefix(config: ConfigWrapper):
    return ProbeEddy(config)
