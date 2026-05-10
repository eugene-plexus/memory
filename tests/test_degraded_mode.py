"""Tests for degraded-mode startup.

v0.1's in-process store can't realistically fail at construction, but the
project-wide rule (`feedback_degraded_mode_required.md`) is that a future
durable backend MUST come up far enough to serve config endpoints when
storage is unavailable. We exercise the contract by monkeypatching
`build_store` to raise — the same shape any future failure would take.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from eugene_plexus_memory import app as app_module
from eugene_plexus_memory.app import create_app
from eugene_plexus_memory.settings import Settings


@pytest.fixture
def degraded_client(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    def _explode(*_: object, **__: object) -> object:
        raise RuntimeError("storage backend unreachable")

    monkeypatch.setattr(app_module, "build_store", _explode)
    app = create_app(settings=settings)
    return TestClient(app)


def test_healthz_reports_degraded(degraded_client: TestClient) -> None:
    with degraded_client as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "degraded"
        assert "storage backend unreachable" in body["details"]["store_error"]


def test_config_endpoints_work_in_degraded_mode(degraded_client: TestClient) -> None:
    """The whole point: config endpoints must stay live so the operator can fix
    the broken config via PATCH."""
    with degraded_client as client:
        schema = client.get("/v1/config/schema")
        assert schema.status_code == 200
        assert schema.json()["component"] == "memory"

        doc = client.get("/v1/config")
        assert doc.status_code == 200

        patch = client.patch("/v1/config", json={"logLevel": "DEBUG"})
        assert patch.status_code == 200
        assert "logLevel" in patch.json()["applied"]


def test_conversation_routes_return_503(degraded_client: TestClient) -> None:
    with degraded_client as client:
        create = client.post("/v1/conversations")
        assert create.status_code == 503
        detail = create.json()["detail"]
        assert detail["title"] == "memory store unavailable"
        assert "Update config" in detail["detail"]
