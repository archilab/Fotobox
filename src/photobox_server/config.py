"""Server configuration: optional .env loading and startup validation."""
from __future__ import annotations
import os
from pathlib import Path

# Providers are added here only once a real adapter exists.
IMPLEMENTED_PROVIDERS = frozenset({"mock", "mflux-flux2"})


class ConfigError(RuntimeError):
    """The server cannot start safely with the current configuration."""


def load_env_file(path: Path = Path(".env")) -> None:
    """Load simple KEY=VALUE lines; variables already set in the environment win."""
    if not path.is_file():
        return
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key:
            # Report the position only, because the line could contain a secret.
            raise ConfigError(f"{path}: line {line_number} is not KEY=VALUE")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def int_setting(name: str, default: int, minimum: int, maximum: int) -> int:
    """Read a whole-number setting, refusing a value outside its allowed range."""
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        value = minimum - 1
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name}={raw!r} must be a whole number from {minimum} to {maximum}")
    return value


def get_frame_count() -> int:
    frames = int_setting("PHOTOBOX_NUM_FRAMES", 4, 3, 4)
    return frames


def developer_mode() -> bool:
    return os.getenv("PHOTOBOX_DEVELOPER_MODE", "").strip().lower() in {"1", "true", "yes"}


def get_cleanup_interval() -> int:
    """Return the seconds between automatic cleanup runs."""
    return int_setting("PHOTOBOX_CLEANUP_INTERVAL_SECONDS", 300, 1, 86400)


def get_provider() -> str:
    """Return the configured image provider, rejecting any that is not implemented."""
    provider = os.getenv("PHOTOBOX_PROVIDER", "mock")
    if provider not in IMPLEMENTED_PROVIDERS:
        raise ConfigError(f"PHOTOBOX_PROVIDER={provider!r} is not implemented; available: {sorted(IMPLEMENTED_PROVIDERS)}")
    if provider == "mock" and not developer_mode():
        raise ConfigError("PHOTOBOX_PROVIDER=mock is not AI and needs PHOTOBOX_DEVELOPER_MODE=true")
    return provider
