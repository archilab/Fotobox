import os
import pytest
from fastapi.testclient import TestClient
from photobox_server.app import app

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv("PHOTOBOX_STORAGE_DIR",str(tmp_path/"data"))
    monkeypatch.setenv("PHOTOBOX_DB_PATH",str(tmp_path/"db.sqlite3"))
    return TestClient(app)
