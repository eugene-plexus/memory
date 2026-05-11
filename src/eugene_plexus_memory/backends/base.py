"""Backend Protocol — uniform contract every storage backend honors.

Backends are constructed once at process startup by `build_backend()`
based on the operator's `backend` config choice. Routes type against
this Protocol so swapping the backend in config (then restarting)
doesn't require route changes.

The v0.1 contract was a simple `(conversationId → list[Message])` map.
v0.2's contract is `MemoryEntry`-shaped — every entry carries
`personId`, `conversationId`, `entryId`, `timestamp`, plus optional
`ntStateSnapshot` and `hemisphereAttribution`. The Conversation read
shape stays `list[Message]` for backward compatibility.

Search is intentionally optional: the protocol declares it but
returning a `BackendError(status_code=503, ...)` is a valid response
for backends that don't yet have embeddings wired (v0.2's
`local_sqlite` does this — the storage half is done, the embedding
half waits for sentence-transformers integration).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from .._generated.models import (
    Conversation,
    MemoryEntry,
    MemorySearchHit,
)

if TYPE_CHECKING:
    from ..config import ConfigStore

# When a bare `Message` is appended via the legacy `/v1/conversations/{id}/messages`
# path WITHOUT a personId in scope (no prior entries to inherit from, no caller-
# supplied personId), entries are stored with this sentinel UUID. The orchestrator
# always supplies personId in v0.2+, so this only fires for the explicit-bare-Message
# backward-compat path.
NIL_PERSON_ID: UUID = UUID("00000000-0000-0000-0000-000000000000")


class BackendError(Exception):
    """Raised by backends to signal a typed failure mode.

    `status_code` maps to the HTTP status the route should emit;
    `detail` is the operator-facing message. Used today for the
    "embeddings not wired" 503 path; future durable backends will
    surface connection-failure 503s through the same mechanism.
    """

    def __init__(self, *, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class PersonRecentResult:
    """Return shape of `Backend.person_recent()`.

    Mirrors the spec's `GET /v1/memory/persons/{personId}/recent`
    response: `entries` are newest-first, capped by the caller's
    limit; `turn_count` is the total turns ever recorded for the
    person (so the UI can render "you've talked N times" without
    re-fetching).
    """

    entries: list[MemoryEntry]
    turn_count: int


class Backend(Protocol):
    """The interface every memory backend implements.

    Methods are sync for v0.2 — every shipped backend is local (in-
    process or SQLite-on-disk). When a network-backed backend lands
    (e.g. mem0 cloud), it'll either run a background thread + sync
    cache or this Protocol grows async siblings. Sync today keeps
    routes simple.
    """

    def create_conversation(self) -> Conversation: ...

    def get_conversation(self, conversation_id: UUID) -> Conversation | None: ...

    def delete_conversation(self, conversation_id: UUID) -> bool: ...

    def append(
        self,
        *,
        conversation_id: UUID,
        entry: MemoryEntry,
    ) -> MemoryEntry | None:
        """Append `entry` to the conversation. Returns the stored
        entry (timestamp / entryId filled in if absent) or `None`
        when the conversation does not exist."""
        ...

    def person_recent(
        self,
        *,
        person_id: UUID,
        limit: int,
        conversation_id: UUID | None = None,
    ) -> PersonRecentResult: ...

    def search(
        self,
        *,
        query: str,
        person_id: UUID | None,
        conversation_id: UUID | None,
        limit: int,
        min_score: float | None,
    ) -> list[MemorySearchHit]:
        """Similarity-based memory search.

        Backends without embedding capability raise
        `BackendError(status_code=503, ...)` — the route maps that to
        the documented 503 response.
        """
        ...

    def close(self) -> None: ...


def build_backend(config: ConfigStore) -> Backend:
    """Construct the configured backend from the live `ConfigStore`.

    Each backend's constructor accepts a `get: key -> Any` callable
    that transparently merges in-memory runtime overrides on top of
    the persisted config (matches the hemisphere-driver pattern). v0.2
    has no overrides; the getter is `config.get` directly.
    """
    # Lazy imports so a missing optional backend (e.g. mem0_adapter
    # not installed) doesn't break the module import.
    from .in_process import InProcessBackend
    from .local_sqlite import LocalSqliteBackend

    backend_kind = str(config.get("backend") or "local_sqlite")
    get: Callable[[str], Any] = config.get

    if backend_kind == "local_sqlite":
        return LocalSqliteBackend.from_config(get)
    if backend_kind == "in_process":
        return InProcessBackend.from_config(get)

    raise BackendError(
        status_code=500,
        detail=(
            f"Unknown memory backend {backend_kind!r}. Valid options: "
            "local_sqlite (default), in_process."
        ),
    )
