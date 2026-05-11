"""Tests for the config protocol endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_get_config_schema_lists_v02_fields(client: TestClient) -> None:
    response = client.get("/v1/config/schema")
    assert response.status_code == 200
    body = response.json()
    assert body["component"] == "memory"
    keys = {f["key"] for f in body["fields"]}
    # `port` is no longer here — owned by the watchdog topology via
    # EUGENE_PLEXUS_MEM_BIND_PORT. v0.2 adds backend / localSqlitePath /
    # embeddingSource.
    assert keys == {
        "backend",
        "localSqlitePath",
        "embeddingSource",
        "logLevel",
        "maxConversations",
        "maxMessagesPerConversation",
    }


def test_get_config_returns_defaults_on_first_run(client: TestClient) -> None:
    response = client.get("/v1/config")
    assert response.status_code == 200
    body = response.json()
    assert "port" not in body
    assert body["backend"] == "local_sqlite"
    assert body["embeddingSource"] == "local"
    assert body["logLevel"] == "INFO"
    assert body["maxConversations"] == 1000
    assert body["maxMessagesPerConversation"] == 10000


def test_patch_config_validates_per_field(client: TestClient) -> None:
    response = client.patch(
        "/v1/config",
        json={
            "maxConversations": 500,  # valid, hot-swappable
            "maxMessagesPerConversation": 50,  # valid, hot-swappable
            "port": 70000,  # `port` is no longer a config field; rejected as unknown
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body["applied"]) == {"maxConversations", "maxMessagesPerConversation"}
    rejected = {r["key"] for r in body["rejected"]}
    assert rejected == {"port"}
    assert body["requiresRestart"] is False


def test_patch_config_rejects_port_as_unknown_field(client: TestClient) -> None:
    """Confirms `port` is gone from the config schema — it's the watchdog's
    job now. Any operator still sending it gets a clean rejection."""
    response = client.patch("/v1/config", json={"port": 9000})
    assert response.status_code == 200
    body = response.json()
    assert body["applied"] == []
    assert body["rejected"][0]["key"] == "port"
    assert "unknown field" in body["rejected"][0]["message"]


def test_patch_config_rejects_unknown_field(client: TestClient) -> None:
    response = client.patch("/v1/config", json={"madeUpKey": "anything"})
    assert response.status_code == 200
    body = response.json()
    assert body["applied"] == []
    assert body["rejected"][0]["key"] == "madeUpKey"
    assert "unknown field" in body["rejected"][0]["message"]
