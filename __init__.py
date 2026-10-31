# Kalico installs from before the python code moved into probe_eddy_ng/
# link the whole repository as the plugin (and tell users there's no need to
# re-run install.sh after updating), so forward to the real package.
from .probe_eddy_ng import load_config_prefix  # noqa: F401
