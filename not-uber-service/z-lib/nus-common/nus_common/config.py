"""Reading settings from the environment.

Every component is configured the same way: environment variables set in its
own .env file. This module makes the two normal cases short and, more
importantly, makes a missing required setting fail immediately with a message
that says which one - not five minutes later inside a connection error.
"""

import os


class ConfigError(RuntimeError):
    """A setting is missing or cannot be understood."""


def required(name: str) -> str:
    """Return a setting that the component cannot run without."""
    value = os.environ.get(name)
    if value is None or value == "":
        raise ConfigError(
            f"{name} is not set. It has no sensible default, so the service "
            f"cannot start without it. Add it to the component's .env file."
        )
    return value


def optional(name: str, default: str) -> str:
    """Return a setting, or the default when it is not set."""
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def integer(name: str, default: int) -> int:
    """Return a whole-number setting."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as err:
        raise ConfigError(f"{name} should be a whole number, got {raw!r}") from err


def number(name: str, default: float) -> float:
    """Return a decimal setting."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as err:
        raise ConfigError(f"{name} should be a number, got {raw!r}") from err


def flag(name: str, default: bool = False) -> bool:
    """Return a yes/no setting.

    Accepts the spellings people actually type: 1, true, yes, on.
    """
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
