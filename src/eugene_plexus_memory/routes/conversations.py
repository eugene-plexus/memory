"""Conversation routes: create / fetch / delete / append-message."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response, status

from .._generated.models import Conversation, Message, Problem
from ..store import InProcessStore

router = APIRouter(tags=["conversations"])


def _store_or_503(request: Request) -> InProcessStore:
    store: InProcessStore | None = getattr(request.app.state, "store", None)
    if store is None:
        err = getattr(request.app.state, "store_error", None) or "unknown error"
        # 503 with an actionable Problem body matches the project-wide
        # degraded-mode contract (see feedback_degraded_mode_required.md).
        problem = Problem(
            type="about:blank",
            title="memory store unavailable",
            status=503,
            detail=(
                f"Conversation store failed to initialize: {err}. "
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
    return store.create()


@router.get("/v1/conversations/{conversation_id}", response_model=Conversation)
async def get_conversation(request: Request, conversation_id: UUID) -> Conversation:
    store = _store_or_503(request)
    conversation = store.get(conversation_id)
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
    if not store.delete(conversation_id):
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
    response_model=Message,
    status_code=status.HTTP_201_CREATED,
)
async def append_message(
    request: Request,
    conversation_id: UUID,
    message: Message,
) -> Message:
    store = _store_or_503(request)
    stored = store.append(conversation_id, message)
    if stored is None:
        problem = Problem(
            type="about:blank",
            title="conversation not found",
            status=404,
            detail=f"No conversation with id {conversation_id}.",
        )
        raise HTTPException(status_code=404, detail=problem.model_dump(exclude_none=True))
    return stored
