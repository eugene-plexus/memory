"""Conversation routes: create / fetch / delete / append-message.

The append path accepts either a v0.1 bare `Message` (backward compat)
or a v0.2 `MemoryEntry` carrying personId + entryId + optional NT
snapshot. Bare-Message posts are upgraded server-side to MemoryEntry
with `personId = NIL_PERSON_ID` (the caller is responsible for
supplying personId in v0.2+; the NIL sentinel makes the "we lost track
of who this is" case obvious in queries rather than silent).

Reads of the conversation still return `Conversation{messages:[Message]}`
— that's the long-term contract. The richer MemoryEntry shape is
available via the new per-person endpoints.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request, Response, status

from .._generated.models import Conversation, MemoryEntry, Message, Problem
from ..backends import NIL_PERSON_ID, Backend

router = APIRouter(tags=["conversations"])


def _store_or_503(request: Request) -> Backend:
    store: Backend | None = getattr(request.app.state, "store", None)
    if store is None:
        err = getattr(request.app.state, "store_error", None) or "unknown error"
        # 503 with an actionable Problem body matches the project-wide
        # degraded-mode contract (see feedback_degraded_mode_required.md).
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


@router.post(
    "/v1/conversations",
    response_model=Conversation,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(request: Request) -> Conversation:
    store = _store_or_503(request)
    return store.create_conversation()


@router.get("/v1/conversations/{conversation_id}", response_model=Conversation)
async def get_conversation(request: Request, conversation_id: UUID) -> Conversation:
    store = _store_or_503(request)
    conversation = store.get_conversation(conversation_id)
    if conversation is None:
        problem = Problem(
            type="about:blank",
            title="conversation not found",
            status=404,
            detail=f"No conversation with id {conversation_id}.",
        )
        raise HTTPException(status_code=404, detail=problem.model_dump(exclude_none=True))
    return conversation


@router.delete("/v1/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(request: Request, conversation_id: UUID) -> Response:
    store = _store_or_503(request)
    if not store.delete_conversation(conversation_id):
        problem = Problem(
            type="about:blank",
            title="conversation not found",
            status=404,
            detail=f"No conversation with id {conversation_id}.",
        )
        raise HTTPException(status_code=404, detail=problem.model_dump(exclude_none=True))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/v1/conversations/{conversation_id}/messages",
    response_model=MemoryEntry,
    status_code=status.HTTP_201_CREATED,
)
async def append_message(
    request: Request,
    conversation_id: UUID,
    body: dict[str, Any],
) -> MemoryEntry:
    """Append either a bare Message (v0.1 compat) or a full MemoryEntry (v0.2).

    The OpenAPI spec declares `oneOf: [Message, MemoryEntry]`. We
    discriminate by presence of `entryId` / `personId` — required on
    MemoryEntry, absent on Message. Bare-Message posts are upgraded
    to MemoryEntry server-side with personId = NIL_PERSON_ID; the
    response is always a MemoryEntry so callers can adopt the richer
    shape incrementally.
    """
    store = _store_or_503(request)

    is_memory_entry = "entryId" in body or "personId" in body
    if is_memory_entry:
        try:
            entry = MemoryEntry.model_validate(body)
        except Exception as e:
            problem = Problem(
                type="about:blank",
                title="invalid memory entry",
                status=400,
                detail=str(e),
            )
            raise HTTPException(
                status_code=400, detail=problem.model_dump(exclude_none=True)
            ) from e
    else:
        try:
            message = Message.model_validate(body)
        except Exception as e:
            problem = Problem(
                type="about:blank",
                title="invalid message",
                status=400,
                detail=str(e),
            )
            raise HTTPException(
                status_code=400, detail=problem.model_dump(exclude_none=True)
            ) from e
        entry = MemoryEntry(
            entryId=uuid4(),
            personId=NIL_PERSON_ID,
            conversationId=conversation_id,
            role=message.role,
            content=message.content,
            timestamp=message.timestamp or datetime.now(UTC),
        )

    stored = store.append(conversation_id=conversation_id, entry=entry)
    if stored is None:
        problem = Problem(
            type="about:blank",
            title="conversation not found",
            status=404,
            detail=f"No conversation with id {conversation_id}.",
        )
        raise HTTPException(status_code=404, detail=problem.model_dump(exclude_none=True))
    return stored
