"""Per-person retrieval (v0.2)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from .._generated.models import MemoryEntry, Problem
from ..backends import Backend

router = APIRouter(tags=["persons"])


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


class PersonRecentResponse(MemoryEntry):
    """Placeholder so the response model has a name in OpenAPI; the
    real shape is defined inline below."""


@router.get("/v1/memory/persons/{person_id}/recent")
async def person_recent(
    request: Request,
    person_id: UUID,
    limit: int = Query(default=50, ge=1, le=200),
    conversation_id: UUID | None = Query(default=None, alias="conversationId"),
) -> dict[str, object]:
    """Recent memory entries involving the given person, newest first.

    `turnCount` reflects total entries for the person (not capped by
    `limit`) so the UI can render "you've talked N times" without
    fetching the full history.
    """
    store = _store_or_503(request)
    result = store.person_recent(person_id=person_id, limit=limit, conversation_id=conversation_id)
    return {
        "personId": str(person_id),
        "turnCount": result.turn_count,
        "entries": [e.model_dump(mode="json", exclude_none=True) for e in result.entries],
    }
