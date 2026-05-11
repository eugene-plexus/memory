"""Pytest fixtures shared across the test suite."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from eugene_plexus_memory.app import create_app
from eugene_plexus_memory.settings import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # Pre-seed config so localSqlitePath lands inside tmp_path — otherwise
    # the default `memory.sqlite3` would be created relative to CWD and
    # leak between test runs.
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump({"localSqlitePath": str(tmp_path / "memory.sqlite3")})
    )
    return Settings(config_file=config_path)


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings=settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c
