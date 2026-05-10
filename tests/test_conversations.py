"""Tests for the conversation CRUD routes."""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient


def test_create_get_append_delete_roundtrip(client: TestClient) -> None:
    # Create
    create = client.post("/v1/conversations")
    assert create.status_code == 201
    conversation = create.json()
    assert conversation["messages"] == []
    cid = conversation["id"]

    # Append
    append = client.post(
        f"/v1/conversations/{cid}/messages",
        json={"role": "user", "content": "hello"},
    )
    assert append.status_code == 201
    appended = append.json()
    assert appended["role"] == "user"
    assert appended["content"] == "hello"
    assert appended["timestamp"] is not None

    # Get
    fetched = client.get(f"/v1/conversations/{cid}")
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["id"] == cid
    assert len(body["messages"]) == 1
    assert body["messages"][0]["content"] == "hello"

    # Delete
    deleted = client.delete(f"/v1/conversations/{cid}")
    assert deleted.status_code == 204

    # Subsequent get is 404
    missing = client.get(f"/v1/conversations/{cid}")
    assert missing.status_code == 404


def test_get_unknown_conversation_returns_404(client: TestClient) -> None:
    response = client.get(f"/v1/conversations/{uuid4()}")
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["status"] == 404
    assert detail["title"] == "conversation not found"


def test_append_to_unknown_conversation_returns_404(client: TestClient) -> None:
    response = client.post(
        f"/v1/conversations/{uuid4()}/messages",
        json={"role": "user", "content": "ghost"},
    )
    assert response.status_code == 404


def test_delete_unknown_conversation_returns_404(client: TestClient) -> None:
    response = client.delete(f"/v1/conversations/{uuid4()}")
    assert response.status_code == 404


def test_message_cap_evicts_oldest(client: TestClient) -> None:
    # Tighten the per-conversation cap so the test can exercise eviction.
    patch = client.patch("/v1/config", json={"maxMessagesPerConversation": 3})
    assert patch.status_code == 200

    cid = client.post("/v1/conversations").json()["id"]
    for i in range(5):
        r = client.post(
            f"/v1/conversations/{cid}/messages",
            json={"role": "user", "content": f"msg{i}"},
        )
        assert r.status_code == 201

    fetched = client.get(f"/v1/conversations/{cid}").json()
    assert [m["content"] for m in fetched["messages"]] == ["msg2", "msg3", "msg4"]


def test_conversation_cap_evicts_oldest(client: TestClient) -> None:
    patch = client.patch("/v1/config", json={"maxConversations": 2})
    assert patch.status_code == 200

    first = client.post("/v1/conversations").json()["id"]
    second = client.post("/v1/conversations").json()["id"]
    third = client.post("/v1/conversations").json()["id"]

    # Oldest (first) should be gone, second + third should remain.
    assert client.get(f"/v1/conversations/{first}").status_code == 404
    assert client.get(f"/v1/conversations/{second}").status_code == 200
    assert client.get(f"/v1/conversations/{third}").status_code == 200
