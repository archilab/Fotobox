import sqlite3
import pytest
from photobox_server.core import db_connect


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOBOX_STORAGE_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PHOTOBOX_DB_PATH", str(tmp_path / "db.sqlite3"))


def insert_session(db, session_id):
    db.execute("INSERT INTO sessions(id,box_id,status,created_at) VALUES(?,?,?,?)", (session_id, "pi-01", "test", "2026-01-01T00:00:00+00:00"))


def session_count():
    with db_connect() as db:
        return db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]


def test_connection_is_closed_after_success(database):
    with db_connect() as db:
        db.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError):
        db.execute("SELECT 1")


def test_connection_is_closed_and_rolled_back_after_error(database):
    with pytest.raises(RuntimeError):
        with db_connect() as db:
            insert_session(db, "rolled-back")
            raise RuntimeError("simulated failure")
    with pytest.raises(sqlite3.ProgrammingError):
        db.execute("SELECT 1")
    assert session_count() == 0


def test_changes_are_committed_on_success(database):
    with db_connect() as db:
        insert_session(db, "kept")
    assert session_count() == 1
