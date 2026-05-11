"""FastAPI app factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from . import __version__
from .auth_state import load_auth_state
from .backends import Backend, build_backend
from .config import ConfigStore
from .dependencies import require_authorized, require_operator
from .routes import config as config_routes
from .routes import conversations as conversations_routes
from .routes import health as health_routes
from .routes import persons as persons_routes
from .routes import search as search_routes
from .settings import Settings, load_settings

log = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    config_store = ConfigStore(settings.config_file)
    if settings.safe_mode:
        log.warning(
            "starting in SAFE MODE (EUGENE_PLEXUS_MEM_SAFE_MODE=1); ignoring "
            "%s and running on defaults. Fix config via /v1/config, then "
            "restart without the env var.",
            settings.config_file,
        )
    else:
        config_store.load()
    app.state.config_store = config_store
    app.state.safe_mode = settings.safe_mode

    # v0.2 auth state. Tests can pre-populate `app.state.auth_state` to
    # exercise authed paths; the default lifespan build reads env vars
    # via Settings and produces an auth-disabled state when the watchdog
    # didn't supply AUTH_SIGNING_KEY.
    if not hasattr(app.state, "auth_state"):
        app.state.auth_state = load_auth_state(
            signing_key_b64=settings.auth_signing_key,
            service_token=settings.service_token,
            master_key_b64=settings.master_key,
        )

    # Build the storage backend selected in config. Safe mode forces
    # `in_process` regardless of config to avoid touching disk —
    # honors the recovery-flow contract (operator can fix a broken
    # localSqlitePath via /v1/config without the corrupt path blocking
    # startup).
    #
    # Routes check `app.state.store` and return 503 with an actionable
    # message when it's None — the standard degraded-mode shape from
    # `feedback_degraded_mode_required.md`.
    backend: Backend | None = None
    backend_error: str | None = None
    if settings.safe_mode:
        # Force in_process; do not consult the persisted config.
        from .backends.in_process import InProcessBackend

        backend = InProcessBackend.from_config(lambda key: None)
        log.info("safe mode active; using in_process backend (no on-disk reads)")
    else:
        try:
            backend = build_backend(config_store)
            log.info("memory backend ready (%s)", config_store.get("backend") or "local_sqlite")
        except Exception as e:
            backend_error = str(e)
            log.error(
                "backend initialization failed (%s); memory running in degraded "
                "mode — fix config via /v1/config and restart",
                e,
            )

    if not hasattr(app.state, "store"):
        app.state.store = backend
        app.state.store_error = backend_error
        owns_store = backend is not None
    else:
        owns_store = False

    try:
        yield
    finally:
        if owns_store and app.state.store is not None:
            app.state.store.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a FastAPI app with all routers mounted."""
    settings = settings or load_settings()

    app = FastAPI(
        title="Eugene Plexus — memory",
        description=(
            "Conversation history + per-person retrieval. v0.2 ships the "
            "`local_sqlite` backend; `in_process` available for tests."
        ),
        version=__version__,
        lifespan=_lifespan,
    )
    app.state.settings = settings

    # Health stays unauthenticated — supervisors and load balancers need
    # to probe it without holding credentials.
    app.include_router(health_routes.router)

    # Config edits are operator-only — service tokens are rejected so a
    # compromised peer can't reconfigure the memory component.
    app.include_router(config_routes.router, dependencies=[Depends(require_operator)])

    # Conversations + per-person retrieval + search: the orchestrator
    # (service:orchestrator) writes/reads; operators may read through the
    # UI for debugging.
    authorized = [Depends(require_authorized)]
    app.include_router(conversations_routes.router, dependencies=authorized)
    app.include_router(persons_routes.router, dependencies=authorized)
    app.include_router(search_routes.router, dependencies=authorized)

    return app
