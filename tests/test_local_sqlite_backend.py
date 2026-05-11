"""Direct tests of the LocalSqliteBackend.

These bypass the route layer and exercise the storage primitives —
useful for catching SQL bugs without HTTP noise.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from eugene_plexus_memory._generated.models import MemoryEntry, Role
from eugene_plexus_memory.backends.base import BackendError
from eugene_plexus_memory.backends.local_sqlite import LocalSqliteBackend


def _backend(tmp_path: Path, *, max_messages: int = 100) -> LocalSqliteBackend:
    return LocalSqliteBackend(
        db_path=tmp_path / "memory.sqlite3",
        limits=lambda: (1000, max_messages),
    )


def _entry(person_id: UUID, conversation_id: UUID, content: str) -> MemoryEntry:
    return MemoryEntry(
        entryId=uuid4(),
        personId=person_id,
        conversationId=conversation_id,
        role=Role.user,
        content=content,
        timestamp=datetime.now(UTC),
    )


def test_create_get_delete_conversation_round_trip(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    convo = backend.create_conversation()
    assert convo.id is not None

    fetched = backend.get_conversation(convo.id)
    assert fetched is not None
    assert fetched.messages == []

    assert backend.delete_conversation(convo.id) is True
    assert backend.get_conversation(convo.id) is None
    assert backend.delete_conversation(convo.id) is False


def test_append_overrides_conversation_id_from_url(tmp_path: Path) -> None:
    """Server-supplied conversationId must win over caller-supplied — prevents
    cross-conversation entry pollution if a caller posts a mismatched id."""
    backend = _backend(tmp_path)
    convo = backend.create_conversation()
    assert convo.id is not None

    wrong_convo = uuid4()
    entry = _entry(uuid4(), wrong_convo, "hi")
    stored = backend.append(conversation_id=convo.id, entry=entry)
    assert stored is not None
    assert stored.conversationId == convo.id  # URL value wins.


def test_append_returns_none_for_unknown_conversation(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    entry = _entry(uuid4(), uuid4(), "hi")
    stored = backend.append(conversation_id=uuid4(), entry=entry)
    assert stored is None


def test_person_recent_filters_by_person(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    convo = backend.create_conversation()
    assert convo.id is not None

    alice = uuid4()
    bob = uuid4()
    backend.append(conversation_id=convo.id, entry=_entry(alice, convo.id, "a1"))
    backend.append(conversation_id=convo.id, entry=_entry(bob, convo.id, "b1"))
    backend.append(conversation_id=convo.id, entry=_entry(alice, convo.id, "a2"))

    result = backend.person_recent(person_id=alice, limit=10)
    contents = [e.content for e in result.entries]
    assert contents == ["a2", "a1"]  # newest first.
    assert result.turn_count == 2  # not the total in the DB; only alice's count.

    bob_result = backend.person_recent(person_id=bob, limit=10)
    assert bob_result.turn_count == 1
    assert bob_result.entries[0].content == "b1"


def test_person_recent_can_restrict_to_one_conversation(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    c1 = backend.create_conversation()
    c2 = backend.create_conversation()
    assert c1.id is not None and c2.id is not None

    alice = uuid4()
    backend.append(conversation_id=c1.id, entry=_entry(alice, c1.id, "c1-a1"))
    backend.append(conversation_id=c2.id, entry=_entry(alice, c2.id, "c2-a1"))

    result = backend.person_recent(person_id=alice, limit=10, conversation_id=c1.id)
    assert [e.content for e in result.entries] == ["c1-a1"]
    assert result.turn_count == 1


def test_person_recent_respects_limit(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    convo = backend.create_conversation()
    assert convo.id is not None
    alice = uuid4()
    for i in range(10):
        backend.append(conversation_id=convo.id, entry=_entry(alice, convo.id, f"a{i}"))

    result = backend.person_recent(person_id=alice, limit=3)
    assert len(result.entries) == 3
    assert result.turn_count == 10


def test_per_conversation_message_cap_evicts_oldest(tmp_path: Path) -> None:
    backend = _backend(tmp_path, max_messages=3)
    convo = backend.create_conversation()
    assert convo.id is not None
    alice = uuid4()
    for i in range(5):
        backend.append(conversation_id=convo.id, entry=_entry(alice, convo.id, f"a{i}"))

    fetched = backend.get_conversation(convo.id)
    assert fetched is not None
    contents = [m.content for m in fetched.messages]
    # Oldest two dropped; newest three retained.
    assert contents == ["a2", "a3", "a4"]


def test_search_returns_503_until_embeddings_wired(tmp_path: Path) -> None:
    """v0.2 ships the wire shape; search returns BackendError(503) until
    sentence-transformers integration lands."""
    backend = _backend(tmp_path)
    with pytest.raises(BackendError) as exc_info:
        backend.search(
            query="anything", person_id=None, conversation_id=None, limit=10, min_score=None
        )
    assert exc_info.value.status_code == 503
    assert "not wired" in exc_info.value.detail


def test_conversation_persists_across_backend_reopens(tmp_path: Path) -> None:
    """SQLite is on-disk — closing and reopening must preserve state. This
    is the single load-bearing property that makes local_sqlite worth
    having over in_process."""
    db_path = tmp_path / "memory.sqlite3"

    backend1 = LocalSqliteBackend(db_path=db_path, limits=lambda: (1000, 100))
    convo = backend1.create_conversation()
    assert convo.id is not None
    alice = uuid4()
    backend1.append(conversation_id=convo.id, entry=_entry(alice, convo.id, "remember me"))
    backend1.close()

    backend2 = LocalSqliteBackend(db_path=db_path, limits=lambda: (1000, 100))
    fetched = backend2.get_conversation(convo.id)
    assert fetched is not None
    assert [m.content for m in fetched.messages] == ["remember me"]
    backend2.close()
