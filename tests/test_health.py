"""Tests for /healthz."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_healthz_ok_when_store_is_up(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["component"] == "memory"
    assert "version" in body
