import pytest
from fastapi.testclient import TestClient
from photobox_server import throttle
from photobox_server.app import app
from photobox_server.auth import create_device
from photobox_server.core import db_connect


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
