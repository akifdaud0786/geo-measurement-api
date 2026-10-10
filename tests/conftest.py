from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    # The context manager runs the lifespan (table creation). TestClient executes
    # BackgroundTasks before returning the response, so uploads are fully processed
    # by the time post() returns.
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def upload(client):
    def _upload(filename: str, content: bytes):
        return client.post("/api/files/", files={"file": (filename, content, "application/octet-stream")})

    return _upload
