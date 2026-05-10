"""Entrypoint: `python -m eugene_plexus_memory`."""

from __future__ import annotations

import os

import uvicorn

from .app import create_app
from .config import ConfigStore
from .settings import load_settings

# Default bind port for standalone launch. The watchdog overrides via
# EUGENE_PLEXUS_MEM_BIND_PORT.
_DEFAULT_PORT = 8083


def main() -> None:
    settings = load_settings()

    bootstrap_store = ConfigStore(settings.config_file)
    if not settings.safe_mode:
        bootstrap_store.load()

    env_port = os.environ.get("EUGENE_PLEXUS_MEM_BIND_PORT")
    port = int(env_port) if env_port else _DEFAULT_PORT

    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.bind_host,
        port=port,
        log_level=str(bootstrap_store.get("logLevel") or "INFO").lower(),
    )


if __name__ == "__main__":
    main()
