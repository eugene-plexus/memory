"""In-process memory backend.

The v0.1 store, ported to the v0.2 `Backend` Protocol. Useful for:

  - tests (no SQLite file to clean up)
  - safe-mode boots (we never touch the on-disk SQLite file when the
    operator's persisted config might be the cause of the failure)
  - standalone dev runs where persistence isn't wanted

Search isn't supported here — `BackendError(503)` directs the caller
to switch to a backend with embeddings (just `local_sqlite` in v0.2,
and even then once embeddings actually land).
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

from .._generated.models import Conversation, MemoryEntry, MemorySearchHit, Message
from .base import NIL_PERSON_ID, Backend, BackendError, PersonRecentResult


class InProcessBackend:
    """Thread-safe in-memory store of MemoryEntry rows keyed by conversation.

    `limits` is a callable rather than a snapshot so tuning maxConversations /
    maxMessagesPerConversation through `PATCH /v1/config` takes effect on
    the next operation without requiring the routes to re-inject the store.
    """

    def __init__(self, *, limits: Callable[[], tuple[int, int]]) -> None:
        self._lock = threading.Lock()
        self._conversations: OrderedDict[UUID, list[MemoryEntry]] = OrderedDict()
        self._limits = limits

    @classmethod
    def from_config(cls, get: Callable[[str], Any]) -> InProcessBackend:
        def limits() -> tuple[int, int]:
            return (
                int(get("maxConversations") or 1000),
                int(get("maxMessagesPerConversation") or 10000),
            )

        return cls(limits=limits)

    def create_conversation(self) -> Conversation:
        cid = uuid4()
        with self._lock:
            self._conversations[cid] = []
            self._evict_locked()
            return Conversation(id=cid, messages=[])

    def get_conversation(self, conversation_id: UUID) -> Conversation | None:
        with self._lock:
            entries = self._conversations.get(conversation_id)
            if entries is None:
                return None
            return Conversation(id=conversation_id, messages=[_to_message(e) for e in entries])

    def delete_conversation(self, conversation_id: UUID) -> bool:
        with self._lock:
            return self._conversations.pop(conversation_id, None) is not None

    def append(self, *, conversation_id: UUID, entry: MemoryEntry) -> MemoryEntry | None:
        with self._lock:
            entries = self._conversations.get(conversation_id)
            if entries is None:
                return None

            stored = _ensure_entry_fields(entry, conversation_id=conversation_id)
            entries.append(stored)

            # Trim oldest entries if we exceed the per-conversation cap.
            _, max_messages = self._limits()
            if len(entries) > max_messages:
                del entries[: len(entries) - max_messages]

            # LRU-style: appended-to conversations move to the end so eviction
            # always drops the conversation that's been quiet the longest.
            self._conversations.move_to_end(conversation_id)
            return stored

    def person_recent(
        self,
        *,
        person_id: UUID,
        limit: int,
        conversation_id: UUID | None = None,
    ) -> PersonRecentResult:
        with self._lock:
            matches: list[MemoryEntry] = []
            for cid, entries in self._conversations.items():
                if conversation_id is not None and cid != conversation_id:
                    continue
                for e in entries:
                    if e.personId == person_id:
                        matches.append(e)

        matches.sort(key=lambda e: e.timestamp, reverse=True)
        turn_count = len(matches)
        return PersonRecentResult(entries=matches[:limit], turn_count=turn_count)

    def search(
        self,
        *,
        query: str,
        person_id: UUID | None,
        conversation_id: UUID | None,
        limit: int,
        min_score: float | None,
    ) -> list[MemorySearchHit]:
        raise BackendError(
            status_code=503,
            detail=(
                "The `in_process` backend does not support search. Set "
                "`backend: local_sqlite` (and configure embeddings) to "
                "enable reactive memory search."
            ),
        )

    def close(self) -> None:
        # In-process store has nothing to flush.
        return None

    def _evict_locked(self) -> None:
        max_conversations, _ = self._limits()
        while len(self._conversations) > max_conversations:
            self._conversations.popitem(last=False)


# Satisfy the structural Protocol check at module load (catches drift early).
_: Backend = InProcessBackend(limits=lambda: (1, 1))


def _to_message(entry: MemoryEntry) -> Message:
    """Project a MemoryEntry back to the v0.1 Message shape for
    `GET /v1/conversations/{id}`. Conversation reads keep the legacy
    shape; the richer MemoryEntry surfaces via the new endpoints."""
    return Message(
        role=entry.role,
        content=entry.content,
        timestamp=entry.timestamp,
    )


def _ensure_entry_fields(entry: MemoryEntry, *, conversation_id: UUID) -> MemoryEntry:
    """Stamp the URL-supplied conversation_id onto the entry.

    `entryId` / `personId` / `timestamp` are required on MemoryEntry
    so the route layer fills them in for the bare-Message path before
    the entry reaches this backend. ConversationId, however, comes from
    the URL — overriding any caller-supplied value avoids client/server
    drift if a caller posts an entry with a mismatched conversationId.
    """
    return entry.model_copy(update={"conversationId": conversation_id})


__all__ = ["NIL_PERSON_ID", "InProcessBackend"]
