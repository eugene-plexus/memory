"""LocalSqliteBackend — v0.2's default memory backend.

SQLite on disk with two tables:

  - `conversations(id, created_at, last_activity)` — minimal metadata
    so eviction can drop the longest-quiet conversation first.
  - `entries(entry_id, conversation_id, person_id, role, content,
    timestamp, nt_state_snapshot, hemisphere_attribution)` — one row
    per MemoryEntry, indexed by `(person_id, timestamp)` for the
    `/v1/memory/persons/{personId}/recent` path.

Writes serialize through a single `threading.Lock` because
`sqlite3.Connection.commit` isn't thread-safe by default and reads
are short. v0.2's personal-install scale (single operator + at most
a handful of connectors) makes this trivially fast; the contention
ceiling we'd hit is far above the bicameral loop's per-turn write
rate (2-6 entries/turn). When that ceiling matters we'll move to a
real connection pool / WAL mode tuning, not a lock-free rewrite.

NTState and hemisphere attribution are serialized as JSON in their
own columns rather than relational tables — they're decorations, not
things we query by. JSON storage keeps the schema simple and avoids
NT-shape migrations when we add the v0.3 12-NT shape.

Search is unimplemented in v0.2 — `search()` raises
`BackendError(status_code=503, ...)`. The storage side (entries +
person-keyed retrieval) is the load-bearing v0.2 work; embedding
integration (sentence-transformers / API) is a follow-on phase.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from .._generated.models import (
    Conversation,
    MemoryEntry,
    MemorySearchHit,
    Message,
    NTState,
    Role,
)
from .base import Backend, BackendError, PersonRecentResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id            TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    last_activity TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entries (
    entry_id              TEXT PRIMARY KEY,
    conversation_id       TEXT NOT NULL,
    person_id             TEXT NOT NULL,
    role                  TEXT NOT NULL,
    content               TEXT NOT NULL,
    timestamp             TEXT NOT NULL,
    nt_state_snapshot     TEXT,
    hemisphere_attribution TEXT,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_entries_conversation ON entries(conversation_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_entries_person ON entries(person_id, timestamp DESC);
"""


class LocalSqliteBackend:
    """SQLite-backed memory store.

    `limits` is a callable rather than a snapshot so PATCH /v1/config
    edits take effect on the next operation without restart.
    """

    def __init__(
        self,
        *,
        db_path: Path,
        limits: Callable[[], tuple[int, int]],
    ) -> None:
        self._db_path = db_path
        self._limits = limits
        self._lock = threading.Lock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False because FastAPI's worker threads share
        # this connection through the `_lock`. The lock funnels all
        # operations through one effective writer.
        self._conn = sqlite3.connect(
            str(self._db_path), check_same_thread=False, isolation_level=None
        )
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(_SCHEMA)

    @classmethod
    def from_config(cls, get: Callable[[str], Any]) -> LocalSqliteBackend:
        raw_path = get("localSqlitePath") or "memory.sqlite3"
        db_path = Path(str(raw_path)).expanduser()

        def limits() -> tuple[int, int]:
            return (
                int(get("maxConversations") or 1000),
                int(get("maxMessagesPerConversation") or 10000),
            )

        return cls(db_path=db_path, limits=limits)

    def create_conversation(self) -> Conversation:
        cid = uuid4()
        now = datetime.now(UTC).isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO conversations(id, created_at, last_activity) VALUES (?, ?, ?)",
                (str(cid), now, now),
            )
            self._evict_locked()
        return Conversation(id=cid, messages=[])

    def get_conversation(self, conversation_id: UUID) -> Conversation | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM conversations WHERE id = ?",
                (str(conversation_id),),
            ).fetchone()
            if row is None:
                return None
            rows = self._conn.execute(
                "SELECT role, content, timestamp "
                "FROM entries WHERE conversation_id = ? ORDER BY timestamp",
                (str(conversation_id),),
            ).fetchall()
        messages = [
            Message(
                role=Role(r[0]),
                content=r[1],
                timestamp=datetime.fromisoformat(r[2]),
            )
            for r in rows
        ]
        return Conversation(id=conversation_id, messages=messages)

    def delete_conversation(self, conversation_id: UUID) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM conversations WHERE id = ?", (str(conversation_id),)
            )
            return cur.rowcount > 0

    def append(self, *, conversation_id: UUID, entry: MemoryEntry) -> MemoryEntry | None:
        stored = entry.model_copy(update={"conversationId": conversation_id})
        now_iso = datetime.now(UTC).isoformat()

        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM conversations WHERE id = ?",
                (str(conversation_id),),
            ).fetchone()
            if row is None:
                return None

            nt_json = (
                stored.ntStateSnapshot.model_dump_json(exclude_none=True)
                if stored.ntStateSnapshot is not None
                else None
            )
            self._conn.execute(
                "INSERT INTO entries("
                "entry_id, conversation_id, person_id, role, content, "
                "timestamp, nt_state_snapshot, hemisphere_attribution"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(stored.entryId),
                    str(stored.conversationId),
                    str(stored.personId),
                    stored.role.value,
                    stored.content,
                    stored.timestamp.isoformat(),
                    nt_json,
                    stored.hemisphereAttribution,
                ),
            )

            # Trim oldest entries if we exceed the per-conversation cap.
            _, max_messages = self._limits()
            count = self._conn.execute(
                "SELECT COUNT(*) FROM entries WHERE conversation_id = ?",
                (str(conversation_id),),
            ).fetchone()[0]
            if count > max_messages:
                to_drop = count - max_messages
                # SQLite doesn't support DELETE ... LIMIT directly; subquery instead.
                self._conn.execute(
                    "DELETE FROM entries WHERE entry_id IN ("
                    "SELECT entry_id FROM entries WHERE conversation_id = ? "
                    "ORDER BY timestamp LIMIT ?)",
                    (str(conversation_id), to_drop),
                )

            # Touch last_activity for eviction ordering.
            self._conn.execute(
                "UPDATE conversations SET last_activity = ? WHERE id = ?",
                (now_iso, str(conversation_id)),
            )

        return stored

    def person_recent(
        self,
        *,
        person_id: UUID,
        limit: int,
        conversation_id: UUID | None = None,
    ) -> PersonRecentResult:
        with self._lock:
            params: tuple[Any, ...] = (str(person_id),)
            where = "person_id = ?"
            if conversation_id is not None:
                where += " AND conversation_id = ?"
                params = (*params, str(conversation_id))

            count = self._conn.execute(
                f"SELECT COUNT(*) FROM entries WHERE {where}",
                params,
            ).fetchone()[0]
            rows = self._conn.execute(
                f"SELECT entry_id, conversation_id, person_id, role, content, "
                f"timestamp, nt_state_snapshot, hemisphere_attribution "
                f"FROM entries WHERE {where} ORDER BY timestamp DESC LIMIT ?",
                (*params, limit),
            ).fetchall()

        entries = [_row_to_entry(r) for r in rows]
        return PersonRecentResult(entries=entries, turn_count=count)

    def search(
        self,
        *,
        query: str,
        person_id: UUID | None,
        conversation_id: UUID | None,
        limit: int,
        min_score: float | None,
    ) -> list[MemorySearchHit]:
        # v0.2 ships the wire shape; the embedding half waits for
        # sentence-transformers integration in a v0.2.x follow-on.
        raise BackendError(
            status_code=503,
            detail=(
                "Memory search is not wired in this build. The endpoint "
                "exists for protocol completeness; embeddings land in a "
                "v0.2.x follow-on. Per-person recent-turn retrieval "
                "(GET /v1/memory/persons/{personId}/recent) is the v0.2 "
                "retrieval path used by the orchestrator."
            ),
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _evict_locked(self) -> None:
        max_conversations, _ = self._limits()
        count = self._conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        if count <= max_conversations:
            return
        to_drop = count - max_conversations
        self._conn.execute(
            "DELETE FROM conversations WHERE id IN ("
            "SELECT id FROM conversations ORDER BY last_activity LIMIT ?)",
            (to_drop,),
        )


def _row_to_entry(row: tuple[Any, ...]) -> MemoryEntry:
    (
        entry_id,
        conv_id,
        person_id,
        role,
        content,
        timestamp,
        nt_json,
        hemisphere_attr,
    ) = row
    nt_state = NTState.model_validate(json.loads(nt_json)) if nt_json else None
    return MemoryEntry(
        entryId=UUID(entry_id),
        conversationId=UUID(conv_id),
        personId=UUID(person_id),
        role=Role(role),
        content=content,
        timestamp=datetime.fromisoformat(timestamp),
        ntStateSnapshot=nt_state,
        hemisphereAttribution=hemisphere_attr,
    )


# Structural Protocol check at module load.
def _check_protocol() -> Backend:
    import tempfile

    return LocalSqliteBackend(
        db_path=Path(tempfile.gettempdir()) / "_protocol_check.sqlite3",
        limits=lambda: (1, 1),
    )


# Don't actually execute the protocol check at import — it would create
# a stray file on every import. The annotation alone catches drift
# during type checking.
_check_protocol_typecheck_only: Callable[[], Backend] = _check_protocol


__all__ = ["LocalSqliteBackend"]
