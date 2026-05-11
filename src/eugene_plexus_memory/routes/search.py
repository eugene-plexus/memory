"""Reactive memory search (v0.2 wire shape; embeddings deferred)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .._generated.models import (
    MemorySearchRequest,
    MemorySearchResult,
    Problem,
)
from ..backends import Backend
from ..backends.base import BackendError

router = APIRouter(tags=["search"])


def _store_or_503(request: Request) -> Backend:
    store: Backend | None = getattr(request.app.state, "store", None)
    if store is None:
        err = getattr(request.app.state, "store_error", None) or "unknown error"
        problem = Problem(
            type="about:blank",
            title="memory store unavailable",
            status=503,
            detail=(
                f"Memory backend failed to initialize: {err}. "
                "Update config via PATCH /v1/config and restart this component."
            ),
        )
        raise HTTPException(status_code=503, detail=problem.model_dump(exclude_none=True))
    return store


@router.post("/v1/memory/search", response_model=MemorySearchResult)
async def search_memory(
    request: Request,
    body: MemorySearchRequest,
) -> MemorySearchResult:
    """Embed the query and return top-K most similar entries.

    v0.2 ships the wire shape; backends without embedding capability
    return 503 (`local_sqlite` does this until sentence-transformers
    integration lands). The orchestrator's topic-shift detector (v0.3)
    calls this reactively when the conversation references something
    outside recent history.
    """
    store = _store_or_503(request)
    try:
        hits = store.search(
            query=body.query,
            person_id=body.personId,
            conversation_id=body.conversationId,
            limit=body.limit if body.limit is not None else 10,
            min_score=body.minScore,
        )
    except BackendError as e:
        problem = Problem(
            type="about:blank",
            title="memory search unavailable",
            status=e.status_code,
            detail=e.detail,
        )
        raise HTTPException(
            status_code=e.status_code, detail=problem.model_dump(exclude_none=True)
        ) from e

    return MemorySearchResult(entries=hits)
