#!/usr/bin/python

""" Tunable parameters for the IMN station scripts.

    Values live in ~/.config/IMN/config.toml (override with $IMN_CONFIG),
    alongside the credentials in youtube.json. The file is optional: every key
    has a default here, so a station without one behaves exactly as before.

        [bolides]
        n              = 3      # how many of a night's bolides to publish
        mag_threshold  = -1.0   # keep meteors at or brighter than this
        slowdown       = 2.0    # play the clip this many times slower
        hold_seconds   = 1.5    # freeze on the last frame for this long

        [trackstack]
        timeout     = 7200      # seconds before a stack is abandoned
        min_meteors = 4         # associations needed to call a shower active

    Deliberately NOT the place for credentials -- those stay in youtube.json,
    which is written with 0600 and handled separately.

    Reading is best effort: a missing file, unparsable TOML or an unknown key
    falls back to the default and logs, because a typo in a config file must
    never take down a night's processing.

    The module is named imnconfig rather than config because RMS inserts this
    directory at the front of sys.path when it imports the external script, so
    a module called config.py here would shadow any other top-level config
    module for the whole process.
"""

import os
import logging

log = logging.getLogger("IMN.config")

_CONFIG_PATH = os.environ.get(
    "IMN_CONFIG", os.path.join(os.path.expanduser("~/.config/IMN"), "config.toml"))

# Every tunable and its default. A key absent from this table is not a valid
# setting -- see _warn_unknown() below.
DEFAULTS = {
    "bolides": {
        "n": 3,
        "mag_threshold": -1.0,
        "slowdown": 2.0,
        "hold_seconds": 1.5,
    },
    "trackstack": {
        "timeout": 7200,
        "min_meteors": 4,
    },
}

_loaded = None


def _read_toml(path):
    """ Parse a TOML file with whatever parser this Python has.

        tomllib is 3.11+, tomli 2.x needs 3.8+, and the stations are on 3.7 --
        hence the chain. Returns None if nothing can parse it.
    """
    try:
        import tomllib                                  # Python 3.11+
        with open(path, "rb") as f:
            return tomllib.load(f)
    except ImportError:
        pass

    try:
        import tomli                                    # backport, 3.7 via 1.2.x
        with open(path, "rb") as f:
            return tomli.load(f)
    except ImportError:
        pass

    try:
        import toml                                     # older third party
        with open(path) as f:
            return toml.load(f)
    except ImportError:
        log.error("no TOML parser available; %s ignored, using defaults", path)
        return None


def _warn_unknown(parsed):
    """ Point out sections and keys that nothing reads.

        A misspelled key would otherwise be silently ignored and the operator
        would be left wondering why their setting had no effect.
    """
    for section, values in parsed.items():
        if section not in DEFAULTS:
            log.warning("%s: unknown section [%s]", _CONFIG_PATH, section)
            continue
        if not isinstance(values, dict):
            log.warning("%s: [%s] is not a table", _CONFIG_PATH, section)
            continue
        for key in values:
            if key not in DEFAULTS[section]:
                log.warning("%s: unknown key %s.%s", _CONFIG_PATH, section, key)


def _load():
    global _loaded

    if _loaded is not None:
        return _loaded

    _loaded = {}

    if not os.path.isfile(_CONFIG_PATH):
        log.info("no %s; using built-in defaults", _CONFIG_PATH)
        return _loaded

    try:
        parsed = _read_toml(_CONFIG_PATH)
    except Exception as e:
        log.error("%s is not valid TOML (%r); using built-in defaults",
                  _CONFIG_PATH, e)
        return _loaded

    if parsed:
        _warn_unknown(parsed)
        _loaded = parsed

    return _loaded


def get(section, key):
    """ Return one setting, falling back to its default.

        A value of the wrong type is refused rather than passed on: a string
        where a number belongs would otherwise fail much later, inside ffmpeg
        or a subprocess timeout.
    """
    default = DEFAULTS[section][key]
    value = _load().get(section, {}).get(key, default)

    if isinstance(default, bool) != isinstance(value, bool) \
            or not isinstance(value, type(default)) \
            and not (isinstance(default, float) and isinstance(value, int)):
        log.error("%s: %s.%s should be %s, got %r; using default %r",
                  _CONFIG_PATH, section, key, type(default).__name__, value, default)
        return default

    return value
