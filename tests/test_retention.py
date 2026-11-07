"""Findings 1-4 of the ADR-010 review (docs/SECURITY.md). Synthetic images only."""
import io
import os
import stat
import time
from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from photobox_server import admin
from photobox_server import app as app_module
from photobox_server.app import app
from photobox_server.config import ConfigError
from photobox_server.core import ORPHAN_GRACE_SECONDS, cleanup_expired, db_connect, storage_root
from photobox_server.pool import import_seed


def png(width=120, height=160):
    out = io.BytesIO()
    Image.new("RGB", (width, height), "#878787").save(out, format="PNG", compress_level=1)
    return out.getvalue()


def new_session(client, pool=False):
    sid = client.post("/api/v1/sessions").json()["id"]
    client.post(f"/api/v1/sessions/{sid}/consent", json={"processing": True, "pool": pool, "documented_externally": True})
    return sid


def upload(client, sid, data=None):
    return client.post(f"/api/v1/sessions/{sid}/portrait", files={"image": ("x.png", data or png(), "image/png")})


def add_seed(color="#444444"):
    out = io.BytesIO()
    Image.new("RGB", (120, 160), color).save(out, format="PNG")
    with db_connect() as db:
        return import_seed(db, out.getvalue(), "SYNTHETIC")


def completed_session(client):
    add_seed()
    sid = new_session(client)
    assert upload(client, sid).status_code == 200
    assert client.post(f"/api/v1/sessions/{sid}/process").status_code == 200
    return sid


def expire_portraits(portrait_id=None):
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with db_connect() as db:
        if portrait_id:
            db.execute("UPDATE portraits SET expires_at=? WHERE id=?", (past, portrait_id))
        else:
            db.execute("UPDATE portraits SET expires_at=?", (past,))


def files_in(folder):
    return list((storage_root() / folder).iterdir())


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


# Finding 1: strips and automatic cleanup

def test_cleanup_deletes_the_strip_of_an_expired_portrait(client):
    sid = completed_session(client)
    assert len(files_in("generated")) == 1
    expire_portraits()
    with db_connect() as db:
        result = cleanup_expired(db)
    assert (result.portraits, result.strips) == (1, 1)
    assert files_in("generated") == [] and files_in("portraits") == []
    assert client.get(f"/api/v1/sessions/{sid}/result").status_code == 404
    assert client.get(f"/api/v1/sessions/{sid}").json()["has_result"] is False


def test_expired_result_is_refused_before_cleanup_runs(client):
    sid = completed_session(client)
    expire_portraits()
    assert client.get(f"/api/v1/sessions/{sid}/result").status_code == 404
    assert len(files_in("generated")) == 1


def test_strip_is_deleted_when_its_pool_match_expires(client):
    donor = new_session(client, pool=True)
    upload(client, donor)
    with db_connect() as db:
        pool_id = db.execute("SELECT id FROM portraits WHERE session_id=? AND pool_eligible=1", (donor,)).fetchone()[0]
    sid = new_session(client)
    upload(client, sid)
    assert client.post(f"/api/v1/sessions/{sid}/process").status_code == 200
    with db_connect() as db:
        assert db.execute("SELECT match_portrait_id FROM sessions WHERE id=?", (sid,)).fetchone()[0] == pool_id
    expire_portraits(pool_id)
    assert client.get(f"/api/v1/sessions/{sid}/result").status_code == 404
    with db_connect() as db:
        assert cleanup_expired(db).strips == 1
    assert files_in("generated") == [] and files_in("pool_portraits") == []
    assert len(files_in("portraits")) == 2


def test_cleanup_keeps_a_strip_whose_portraits_are_valid(client):
    sid = completed_session(client)
    with db_connect() as db:
        assert cleanup_expired(db) == (0, 0, 0)
    assert client.get(f"/api/v1/sessions/{sid}/result").status_code == 200


def test_cleanup_removes_a_strip_left_by_an_earlier_version(client):
    sid = completed_session(client)
    with db_connect() as db:
        db.execute("DELETE FROM portraits")
        assert cleanup_expired(db).strips == 1
    assert files_in("generated") == []
    assert client.get(f"/api/v1/sessions/{sid}").json()["has_result"] is False


def test_server_start_runs_cleanup(client, tmp_path, monkeypatch):
    completed_session(client)
    expire_portraits()
    monkeypatch.chdir(tmp_path)
    with TestClient(app):
        for _ in range(50):
            if not files_in("generated"):
                break
            time.sleep(0.05)
    assert files_in("generated") == [] and files_in("portraits") == []


def test_cleanup_repeats_while_the_server_runs(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PHOTOBOX_CLEANUP_INTERVAL_SECONDS", "1")
    with TestClient(app):
        completed_session(client)
        expire_portraits()
        for _ in range(60):
            if not files_in("generated"):
                break
            time.sleep(0.05)
    assert files_in("generated") == []


@pytest.mark.parametrize("value", ["0", "-5", "86401", "soon"])
def test_invalid_cleanup_interval_stops_startup(anonymous_client, tmp_path, monkeypatch, value):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PHOTOBOX_CLEANUP_INTERVAL_SECONDS", value)
    with pytest.raises(ConfigError):
        with TestClient(app):
            pass


def test_failed_cleanup_is_logged_without_details_and_retried(monkeypatch, caplog):
    calls = []

    def fail():
        calls.append(1)
        raise RuntimeError("/secret/path/portrait.jpg")

    monkeypatch.setattr(app_module, "run_cleanup", fail)

    async def run():
        import asyncio
        task = asyncio.create_task(app_module.cleanup_loop(0))
        while len(calls) < 2:
            await asyncio.sleep(0.01)
        task.cancel()

    import asyncio
    asyncio.run(run())
    assert len(calls) >= 2
    assert "RuntimeError" in caplog.text and "/secret/path" not in caplog.text


def test_admin_cleanup_reports_each_kind(client, capsys, tmp_path, monkeypatch):
    completed_session(client)
    expire_portraits()
    monkeypatch.chdir(tmp_path)
    assert admin.main(["cleanup"]) == 0
    out = capsys.readouterr().out
    assert "Expired portraits deleted: 1" in out and "Strips deleted: 1" in out and "Untracked files deleted: 0" in out


# Finding 2: no untracked portraits

def test_invalid_retention_setting_saves_nothing(client, monkeypatch):
    sid = new_session(client)
    monkeypatch.setenv("PHOTOBOX_POOL_HOURS", "0")
    assert upload(client, sid).status_code == 500
    assert files_in("portraits") == []


def test_database_failure_removes_the_saved_portrait(client, monkeypatch):
    sid = new_session(client)

    def fail():
        raise RuntimeError("simulated")

    monkeypatch.setattr(app_module, "now_iso", fail)
    with pytest.raises(RuntimeError):
        upload(client, sid)
    assert files_in("portraits") == []


@pytest.mark.parametrize("folder", ["portraits", "generated"])
def test_cleanup_removes_old_untracked_files_only(anonymous_client, folder):
    old = storage_root() / folder / "old.bin"
    recent = storage_root() / folder / "recent.bin"
    old.write_bytes(b"x")
    recent.write_bytes(b"x")
    stamp = time.time() - ORPHAN_GRACE_SECONDS - 60
    os.utime(old, (stamp, stamp))
    with db_connect() as db:
        assert cleanup_expired(db).untracked_files == 1
    assert not old.exists() and recent.exists()


def test_cleanup_never_removes_seed_images(anonymous_client):
    add_seed()
    seed = files_in("seed_portraits")[0]
    stamp = time.time() - ORPHAN_GRACE_SECONDS - 60
    os.utime(seed, (stamp, stamp))
    with db_connect() as db:
        cleanup_expired(db)
    assert seed.exists()


def test_cleanup_keeps_old_tracked_files(client):
    sid = completed_session(client)
    stamp = time.time() - ORPHAN_GRACE_SECONDS - 60
    for path in files_in("portraits") + files_in("generated"):
        os.utime(path, (stamp, stamp))
    with db_connect() as db:
        assert cleanup_expired(db).untracked_files == 0
    assert client.get(f"/api/v1/sessions/{sid}/result").status_code == 200


# Finding 3: file permissions

@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_stored_data_is_private(client, monkeypatch):
    # A permissive umask must not widen anything the server creates.
    old_umask = os.umask(0)
    try:
        completed_session(client)
    finally:
        os.umask(old_umask)
    root = storage_root()
    for folder in (root, root / "portraits", root / "generated", root / "seed_portraits"):
        assert mode(folder) == 0o700
    assert mode(files_in("portraits")[0]) == 0o600
    assert mode(files_in("generated")[0]) == 0o600
    database = os.environ["PHOTOBOX_DB_PATH"]
    with db_connect():
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(database + suffix):
                assert mode(database + suffix) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_existing_open_permissions_are_corrected(anonymous_client):
    root = storage_root()
    database = os.environ["PHOTOBOX_DB_PATH"]
    with db_connect():
        pass
    root.chmod(0o755)
    (root / "portraits").chmod(0o755)
    os.chmod(database, 0o644)
    with db_connect():
        pass
    assert mode(root) == 0o700 and mode(root / "portraits") == 0o700 and mode(database) == 0o600


# Finding 4: pixel limit

def test_image_over_the_pixel_limit_is_rejected(client):
    response = upload(client, new_session(client), png(6000, 5000))
    assert response.status_code == 422 and "pixel limit" in response.text
    assert files_in("portraits") == []


def test_image_at_the_pixel_limit_is_accepted(client):
    assert upload(client, new_session(client), png(5000, 4000)).status_code == 200
