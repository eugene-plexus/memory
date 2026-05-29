"""Route-level tests for the v0.2 endpoints:

- GET /v1/memory/persons/{personId}/recent
- POST /v1/memory/search
- POST /v1/conversations/{conversationId}/messages with MemoryEntry vs bare Message
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient


def _create_conversation(client: TestClient) -> UUID:
    response = client.post("/v1/conversations")
    assert response.status_code == 201
    return UUID(response.json()["id"])


def test_append_accepts_bare_message_for_backcompat(client: TestClient) -> None:
    """v0.1 callers post bare Message; server upgrades to MemoryEntry with
    personId = NIL. New callers post MemoryEntry directly."""
    cid = _create_conversation(client)
    response = client.post(
        f"/v1/conversations/{cid}/messages",
        json={"role": "user", "content": "hi"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["role"] == "user"
    assert body["content"] == "hi"
    assert body["personId"] == "00000000-0000-0000-0000-000000000000"
    assert UUID(body["entryId"])
    assert body["conversationId"] == str(cid)


def test_append_accepts_full_memory_entry(client: TestClient) -> None:
    cid = _create_conversation(client)
    person_id = uuid4()
    entry_id = uuid4()
    response = client.post(
        f"/v1/conversations/{cid}/messages",
        json={
            "entryId": str(entry_id),
            "personId": str(person_id),
            "conversationId": str(cid),
            "role": "user",
            "content": "richer payload",
            "timestamp": datetime.now(UTC).isoformat(),
            "hemisphereAttribution": "blended",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["entryId"] == str(entry_id)
    assert body["personId"] == str(person_id)
    assert body["hemisphereAttribution"] == "blended"


def test_get_person_recent_returns_only_that_persons_entries(client: TestClient) -> None:
    cid = _create_conversation(client)
    alice = uuid4()
    bob = uuid4()

    def _post(person_id: UUID, content: str) -> None:
        response = client.post(
            f"/v1/conversations/{cid}/messages",
            json={
                "entryId": str(uuid4()),
                "personId": str(person_id),
                "conversationId": str(cid),
                "role": "user",
                "content": content,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )
        assert response.status_code == 201, response.text

    _post(alice, "a1")
    _post(bob, "b1")
    _post(alice, "a2")

    response = client.get(f"/v1/memory/persons/{alice}/recent")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["personId"] == str(alice)
    assert body["turnCount"] == 2
    contents = [e["content"] for e in body["entries"]]
    assert contents == ["a2", "a1"]


def test_get_person_recent_respects_limit_query_param(client: TestClient) -> None:
    cid = _create_conversation(client)
    alice = uuid4()
    for i in range(5):
        client.post(
            f"/v1/conversations/{cid}/messages",
            json={
                "entryId": str(uuid4()),
                "personId": str(alice),
                "conversationId": str(cid),
                "role": "user",
                "content": f"a{i}",
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )

    response = client.get(f"/v1/memory/persons/{alice}/recent?limit=2")
    assert response.status_code == 200
    body = response.json()
    assert body["turnCount"] == 5
    assert len(body["entries"]) == 2


def test_get_person_recent_unknown_person_returns_empty_not_404(
    client: TestClient,
) -> None:
    """Unknown personId returns an empty result, not 404 — Eugene meeting
    a new person shouldn't be an error condition."""
    response = client.get(f"/v1/memory/persons/{uuid4()}/recent")
    assert response.status_code == 200
    body = response.json()
    assert body["turnCount"] == 0
    assert body["entries"] == []


def test_search_returns_503_in_v02(client: TestClient) -> None:
    """v0.2 ships the wire shape; backends without embedding capability
    return 503. The orchestrator's topic-shift detector (v0.3) is what
    will actually exercise this endpoint."""
    response = client.post("/v1/memory/search", json={"query": "anything"})
    assert response.status_code == 503, response.text
    detail = response.json()["detail"]
    assert detail["title"] == "memory search unavailable"
    assert "not wired" in detail["detail"]


def test_appended_entries_show_up_in_conversation_read(client: TestClient) -> None:
    """The Conversation read shape stays `list[Message]` (no personId etc).
    The richer MemoryEntry surfaces through the new endpoints; the
    legacy read keeps working unchanged for v0.1 callers."""
    cid = _create_conversation(client)
    person_id = uuid4()
    client.post(
        f"/v1/conversations/{cid}/messages",
        json={
            "entryId": str(uuid4()),
            "personId": str(person_id),
            "conversationId": str(cid),
            "role": "user",
            "content": "see this in the conversation read",
            "timestamp": datetime.now(UTC).isoformat(),
        },
    )
    response = client.get(f"/v1/conversations/{cid}")
    assert response.status_code == 200
    messages = response.json()["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "see this in the conversation read"
    # No personId leakage on the Message-shaped read.
    assert "personId" not in messages[0]
