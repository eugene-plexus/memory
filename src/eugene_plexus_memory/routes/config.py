"""Config protocol routes: GET, PATCH, schema, test."""

from __future__ import annotations

import time

from fastapi import APIRouter, Request

from .._generated.models import (
    ConfigDocument,
    ConfigSchema,
    ConfigTestRequest,
    ConfigTestResult,
    ConfigUpdateRequest,
    ConfigUpdateResult,
)
from ..config import ConfigStore, as_schema

router = APIRouter(tags=["config"])


@router.get("/v1/config", response_model=ConfigDocument)
async def get_config(request: Request) -> ConfigDocument:
    store: ConfigStore = request.app.state.config_store
    return store.as_document()


@router.get("/v1/config/schema", response_model=ConfigSchema)
async def get_config_schema() -> ConfigSchema:
    return as_schema()


@router.patch("/v1/config", response_model=ConfigUpdateResult)
async def patch_config(
    request: Request,
    body: ConfigUpdateRequest,
) -> ConfigUpdateResult:
    store: ConfigStore = request.app.state.config_store
    return store.apply_patch(body)


@router.post("/v1/config/test", response_model=ConfigTestResult)
async def test_config(
    request: Request,
    body: ConfigTestRequest | None = None,
) -> ConfigTestResult:
    """v0.1's in-process store has no external dependency to verify, so this
    endpoint always succeeds quickly. It exists for protocol uniformity:
    the UI can hit `/v1/config/test` on every component without
    per-component conditionals. When v0.2+ adds a real backend (DB,
    vector store, etc.), this is where its connection check goes."""
    start = time.perf_counter()
    # Body intentionally unused in v0.1 — there's nothing to override that
    # would change the success outcome. Future durable backends will read
    # `body.overrides` and try the new connection string here.
    _ = body
    store: ConfigStore = request.app.state.store
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    if store is None:
        return ConfigTestResult(
            ok=False,
            component="memory",
            latencyMs=elapsed_ms,
            error="conversation store not initialized (degraded mode)",
        )
    return ConfigTestResult(
        ok=True,
        component="memory",
        latencyMs=elapsed_ms,
        summary="In-process store; v0.1 has no external dependency to verify.",
    )
