"""FastAPI app factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import __version__
from .config import ConfigStore
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
    config_store.load()
    app.state.config_store = config_store

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

    app.include_router(health_routes.router)
    app.include_router(config_routes.router)
    app.include_router(conversations_routes.router)

    return app
