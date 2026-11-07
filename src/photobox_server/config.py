"""Server configuration: optional .env loading and startup validation."""
from __future__ import annotations
import os
from pathlib import Path

# Providers are added here only once a real adapter exists (milestone M4).
IMPLEMENTED_PROVIDERS = frozenset({"mock"})


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


def get_provider() -> str:
    """Return the configured image provider, rejecting any that is not implemented."""
    provider = os.getenv("PHOTOBOX_PROVIDER", "mock")
    if provider not in IMPLEMENTED_PROVIDERS:
        raise ConfigError(f"PHOTOBOX_PROVIDER={provider!r} is not implemented; available: {sorted(IMPLEMENTED_PROVIDERS)}")
    return provider
