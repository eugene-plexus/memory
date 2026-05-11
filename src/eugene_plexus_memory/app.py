"""FastAPI app factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from . import __version__
from .auth_state import load_auth_state
from .config import ConfigStore
from .dependencies import require_authorized, require_operator
from .routes import config as config_routes
from .routes import conversations as conversations_routes
from .routes import health as health_routes
from .settings import Settings, load_settings
from .store import InProcessStore

log = logging.getLogger(__name__)


def build_store(config: ConfigStore) -> InProcessStore:
    """Construct the conversation store from the runtime config."""

    def limits() -> tuple[int, int]:
        return (
            int(config.get("maxConversations") or 1000),
            int(config.get("maxMessagesPerConversation") or 10000),
        )

    return InProcessStore(limits=limits)


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

    # The in-process store can't actually fail to construct in v0.1, but the
    # try/except matches the project-wide pattern (see
    # `feedback_degraded_mode_required.md`): a future durable backend (DB,
    # Redis, etc.) MUST come up far enough to serve config endpoints even
    # when its storage is unreachable so the operator can fix the config
    # through the UI rather than SSH-and-edit. Routes check `app.state.store`
    # and return 503 with an actionable message when it's None.
    try:
        app.state.store = build_store(config_store)
        app.state.store_error = None
        log.info("conversation store ready (in-process)")
    except Exception as e:
        app.state.store = None
        app.state.store_error = str(e)
        log.error(
            "store initialization failed (%s); memory running in degraded "
            "mode — fix config via /v1/config and restart",
            e,
        )

    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a FastAPI app with all routers mounted."""
    settings = settings or load_settings()

    app = FastAPI(
        title="Eugene Plexus — memory",
        description="Conversation history storage. v0.1 ships an in-process stub.",
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

    # Conversations: the orchestrator (service:orchestrator) writes
    # turns; operators may read them through the UI for debugging.
    app.include_router(
        conversations_routes.router, dependencies=[Depends(require_authorized)]
    )

    return app
