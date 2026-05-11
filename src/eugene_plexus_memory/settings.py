"""Startup-time settings, sourced from environment variables.

Distinct from the runtime *config* (see `config.py`), which is editable via
`PATCH /v1/config` at runtime. These settings only control bootstrap:
where to find the config file, which interface to bind. Once the config
file is loaded, runtime config takes precedence for everything it covers.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EUGENE_PLEXUS_MEM_",
        env_file=None,
        case_sensitive=False,
    )

    config_file: Path = Path("config.yaml")
    """Where the runtime config is persisted. PATCH /v1/config writes here."""

    bind_host: str = "127.0.0.1"
    """Network interface to bind. Override to 0.0.0.0 for tailnet exposure."""

    safe_mode: bool = False
    """If true, skip loading the persisted config file at startup and run on
    built-in defaults. Set by the watchdog via EUGENE_PLEXUS_MEM_SAFE_MODE=1
    when a previous boot failed. PATCH /v1/config still writes to
    `config_file` normally so the operator's repair survives the next
    non-safe-mode boot. Per the safe-mode contract in
    specs/openapi/memory.yaml. v0.1's in-process store doesn't actually
    care about config since the only fields are limits with safe defaults,
    but the contract is honored uniformly so the operator's mental model
    of safe mode is the same across every component."""

    auth_signing_key: str | None = None
    """Base64-encoded 32-byte HMAC signing key, supplied by the watchdog at
    spawn time (EUGENE_PLEXUS_MEM_AUTH_SIGNING_KEY). When absent the
    component runs unauthenticated — dev / standalone path only."""

    service_token: str | None = None
    """Long-lived service JWT (EUGENE_PLEXUS_MEM_SERVICE_TOKEN). Not
    consumed by v0.1's in-process store; captured for symmetry."""

    master_key: str | None = None
    """Base64-encoded 32-byte secretbox key (EUGENE_PLEXUS_MEM_MASTER_KEY).
    Not used in v0.1 — conversation contents aren't encrypted at rest."""


def load_settings() -> Settings:
    return Settings()
