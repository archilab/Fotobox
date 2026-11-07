import pytest
from fastapi.testclient import TestClient
from photobox_server import jobs, throttle
from photobox_server.app import app
from photobox_server.auth import create_device
from photobox_server.core import db_connect


@pytest.fixture(autouse=True)
def test_settings(monkeypatch, tmp_path):
    # Set rather than deleted, so values a test loads from a .env file are undone afterwards.
    monkeypatch.setenv("PHOTOBOX_DEVELOPER_MODE", "true")
    monkeypatch.setenv("PHOTOBOX_NUM_FRAMES", "4")
    monkeypatch.setenv("PHOTOBOX_STORAGE_DIR", str(tmp_path / "default-storage"))
    monkeypatch.delenv("PHOTOBOX_PROVIDER", raising=False)
    monkeypatch.setenv("PHOTOBOX_GENERATION_RETRY_DELAY_SECONDS", "0")


def process(client, session_id, provider=None):
    """Queue a session and run the worker's job now, so tests need not wait for its thread."""
    response = client.post(f"/api/v1/sessions/{session_id}/process")
    if response.status_code == 202:
        jobs.run_pending(provider)
    return response


@pytest.fixture
def anonymous_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOBOX_STORAGE_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PHOTOBOX_DB_PATH", str(tmp_path / "db.sqlite3"))
    throttle.reset()
    return TestClient(app)


@pytest.fixture
def client(anonymous_client):
    with db_connect() as db:
        token = create_device(db, "pi-01")
    anonymous_client.headers["Authorization"] = f"Bearer {token}"
    return anonymous_client
