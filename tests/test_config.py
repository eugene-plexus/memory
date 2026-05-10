"""Tests for the config protocol endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_get_config_schema_lists_v01_fields(client: TestClient) -> None:
    response = client.get("/v1/config/schema")
    assert response.status_code == 200
    body = response.json()
    assert body["component"] == "memory"
    keys = {f["key"] for f in body["fields"]}
    assert keys == {"port", "logLevel", "maxConversations", "maxMessagesPerConversation"}


def test_get_config_returns_defaults_on_first_run(client: TestClient) -> None:
    response = client.get("/v1/config")
    assert response.status_code == 200
    body = response.json()
    assert body["port"] == 8083
    assert body["logLevel"] == "INFO"
    assert body["maxConversations"] == 1000
    assert body["maxMessagesPerConversation"] == 10000


def test_patch_config_validates_per_field(client: TestClient) -> None:
    response = client.patch(
        "/v1/config",
        json={
            "logLevel": "DEBUG",  # valid
            "maxConversations": 500,  # valid
            "port": 70000,  # invalid: > 65535
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body["applied"]) == {"logLevel", "maxConversations"}
    rejected = {r["key"] for r in body["rejected"]}
    assert rejected == {"port"}
    assert body["requiresRestart"] is False


def test_patch_config_marks_restart_required_on_port(client: TestClient) -> None:
    response = client.patch("/v1/config", json={"port": 9000})
    assert response.status_code == 200
    body = response.json()
    assert body["applied"] == ["port"]
    assert body["requiresRestart"] is True
    assert body["pendingRestart"] == ["port"]


def test_patch_config_rejects_unknown_field(client: TestClient) -> None:
    response = client.patch("/v1/config", json={"madeUpKey": "anything"})
    assert response.status_code == 200
    body = response.json()
    assert body["applied"] == []
    assert body["rejected"][0]["key"] == "madeUpKey"
    assert "unknown field" in body["rejected"][0]["message"]
