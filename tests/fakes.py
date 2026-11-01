# Stand-ins for the Klipper objects eddy-ng's code uses
from __future__ import annotations

import types


class ConfigError(Exception):
    pass


class CommandError(Exception):
    pass


_SENTINEL = object()


# A config section, behaving like Klipper's ConfigWrapper for the getters
# eddy-ng uses. Values are given as the strings a config file would have.
class FakeConfig:
    error = ConfigError

    def __init__(self, options=None, name="probe_eddy_ng test"):
        self._options = {k.lower(): str(v) for k, v in (options or {}).items()}
        self._name = name
        self._printer = types.SimpleNamespace(config_error=ConfigError, command_error=CommandError)

    def get_name(self):
        return self._name

    def get_printer(self):
        return self._printer

    def get_prefix_options(self, prefix):
        return [o for o in self._options if o.startswith(prefix)]

    def _get(self, option, default, parse, minval=None, maxval=None, above=None, below=None):
        if option not in self._options:
            if default is _SENTINEL:
                raise ConfigError(f"Option '{option}' in section '{self._name}' must be specified")
            return default
        try:
            v = parse(self._options[option])
        except ValueError:
            raise ConfigError(f"Unable to parse option '{option}' in section '{self._name}'")
        if minval is not None and v < minval:
            raise ConfigError(f"Option '{option}' in section '{self._name}' must have minimum of {minval}")
        if maxval is not None and v > maxval:
            raise ConfigError(f"Option '{option}' in section '{self._name}' must have maximum of {maxval}")
        if above is not None and v <= above:
            raise ConfigError(f"Option '{option}' in section '{self._name}' must be above {above}")
        if below is not None and v >= below:
            raise ConfigError(f"Option '{option}' in section '{self._name}' must be below {below}")
        return v

    def get(self, option, default=_SENTINEL):
        return self._get(option, default, str)

    def getint(self, option, default=_SENTINEL, minval=None, maxval=None):
        return self._get(option, default, int, minval, maxval)

    def getfloat(self, option, default=_SENTINEL, minval=None, maxval=None, above=None, below=None):
        return self._get(option, default, float, minval, maxval, above, below)

    def getboolean(self, option, default=_SENTINEL):
        def parse(s):
            if s.lower() in ("1", "true", "yes", "on"):
                return True
            if s.lower() in ("0", "false", "no", "off"):
                return False
            raise ValueError(s)

        return self._get(option, default, parse)

    def getchoice(self, option, choices, default=_SENTINEL):
        if option not in self._options and default is not _SENTINEL:
            # Klipper looks the default up in the choices too
            option_value = default
        else:
            option_value = self.get(option)
        if option_value not in choices:
            raise ConfigError(f"Choice '{option_value}' for option '{option}' in section '{self._name}' is not a valid choice")
        return choices[option_value] if isinstance(choices, dict) else option_value

    def getfloatlist(self, option, default=_SENTINEL, sep=",", count=None):
        def parse(s):
            vals = [float(p) for p in s.split(sep)]
            if count is not None and len(vals) != count:
                raise ValueError(s)
            return vals

        return self._get(option, default, parse)


# A G-code command's parameters
class FakeGcmd:
    def __init__(self, **params):
        self._params = {k.upper(): str(v) for k, v in params.items()}

    def get(self, name, default=_SENTINEL):
        if name in self._params:
            return self._params[name]
        if default is _SENTINEL:
            raise CommandError(f"Error on '{name}': missing")
        return default

    def get_float(self, name, default=_SENTINEL, **_limits):
        v = self.get(name, default)
        return None if v is None else float(v)

    def get_int(self, name, default=_SENTINEL, **_limits):
        v = self.get(name, default)
        return None if v is None else int(v)


# Records what's sent to the console
class FakeGcode:
    def __init__(self):
        self.messages = []

    def respond_info(self, msg, log=True):
        self.messages.append(msg)

    def respond_raw(self, msg):
        self.messages.append(msg)


# Records options staged for SAVE_CONFIG
class FakeConfigfile:
    def __init__(self):
        self.saved = {}

    def set(self, section, option, value):
        self.saved.setdefault(section, {})[option] = value


class FakePrinter:
    command_error = CommandError
    config_error = ConfigError

    def __init__(self, **objects):
        self.objects = {"configfile": FakeConfigfile(), **objects}

    def lookup_object(self, name, default=_SENTINEL):
        if name in self.objects or default is _SENTINEL:
            return self.objects[name]
        return default

    def is_shutdown(self):
        return False
