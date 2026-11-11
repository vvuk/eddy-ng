# Klipper installs from before the python code moved into probe_eddy_ng/
# symlinked klippy/extras/probe_eddy_ng.py to this file. After updating, those
# installs need ./install.sh to be run again; say so instead of failing to load.
# (The probe_eddy_ng/ package takes precedence over this file on import, so
# this is only loaded through such an old symlink.)

MESSAGE = (
    "eddy-ng: this update changed how eddy-ng is installed. "
    "Please run ./install.sh from the eddy-ng directory again, "
    "then rebuild and flash the sensor firmware."
)


def load_config(config):
    raise config.error(MESSAGE)


def load_config_prefix(config):
    raise config.error(MESSAGE)
