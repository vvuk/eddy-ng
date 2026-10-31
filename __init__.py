from .compat import ConfigWrapper
from .probe_eddy_ng import ProbeEddy


def load_config_prefix(config: ConfigWrapper):
    if config.getboolean("bigfoot", False):
        try:
            from .eddy_ng_bigfoot import BigfootProbe
        except ImportError as e:
            raise config.error(f"ProbeEddy: unable to load bigfoot support, which requires scipy: {e}")
        return BigfootProbe(config)
    return ProbeEddy(config)
