import io
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from photobox_server import admin
from photobox_server.app import app
from photobox_server.auth import create_device, hash_token, revoke_device
from photobox_server.core import db_connect, storage_root


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def synthetic_jpeg():
    out = io.BytesIO()
    Image.new("RGB", (120, 160), "#878787").save(out, format="JPEG")
    return out.getvalue()


def add_device(device_id):
    with db_connect() as db:
        return create_device(db, device_id)


def completed_session(client):
    seed = storage_root() / "seed_portraits" / "seed.png"
    Image.new("RGB", (120, 160), "#444444").save(seed)
    sid = client.post("/api/v1/sessions").json()["id"]
    client.post(f"/api/v1/sessions/{sid}/consent", json={"processing": True, "documented_externally": True})
    client.post(f"/api/v1/sessions/{sid}/portrait", files={"image": ("t.jpg", synthetic_jpeg(), "image/jpeg")})
    assert client.post(f"/api/v1/sessions/{sid}/process").status_code == 200
    return sid


def test_health_is_public(anonymous_client):
    assert anonymous_client.get("/api/v1/health").status_code == 200


@pytest.mark.parametrize("method,path", [
    ("post", "/api/v1/sessions"),
    ("get", "/api/v1/sessions/any"),
    ("get", "/api/v1/sessions/any/result"),
    ("post", "/api/v1/sessions/any/process"),
    ("post", "/api/v1/sessions/any/portrait"),
    ("get", "/docs"),
    ("get", "/openapi.json"),
    ("get", "/unknown"),
])
def test_everything_else_requires_a_token(anonymous_client, method, path):
    response = getattr(anonymous_client, method)(path)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("header", ["Bearer wrong", "Bearer ", "Basic abc", "wrong"])
def test_invalid_credentials_are_rejected(anonymous_client, header):
    add_device("pi-01")
    assert anonymous_client.post("/api/v1/sessions", headers={"Authorization": header}).status_code == 401


def test_unauthenticated_upload_is_rejected_before_the_body_is_parsed(anonymous_client):
    response = anonymous_client.post("/api/v1/sessions/any/portrait", content=b"not multipart", headers={"Content-Type": "multipart/form-data; boundary=x"})
    assert response.status_code == 401


def test_session_belongs_to_the_authenticated_device_not_the_request_body(client):
    created = client.post("/api/v1/sessions", json={"box_id": "someone-else"}).json()
    assert client.get(f"/api/v1/sessions/{created['id']}").json()["box_id"] == "pi-01"


def test_other_device_cannot_read_or_change_a_session(client):
    sid = completed_session(client)
    other = bearer(add_device("pi-02"))
    assert client.get(f"/api/v1/sessions/{sid}/result", headers=other).status_code == 404
    assert client.get(f"/api/v1/sessions/{sid}", headers=other).status_code == 404
    assert client.post(f"/api/v1/sessions/{sid}/consent", headers=other, json={"processing": True, "documented_externally": True}).status_code == 404
    assert client.post(f"/api/v1/sessions/{sid}/process", headers=other).status_code == 404
    assert client.get(f"/api/v1/sessions/{sid}/result").status_code == 200


def test_revoked_token_stops_working(anonymous_client):
    token = add_device("pi-01")
    assert anonymous_client.post("/api/v1/sessions", headers=bearer(token)).status_code == 201
    with db_connect() as db:
        assert revoke_device(db, "pi-01") is True
        assert revoke_device(db, "pi-01") is False
    assert anonymous_client.post("/api/v1/sessions", headers=bearer(token)).status_code == 401


def test_only_a_hash_of_the_token_is_stored(anonymous_client):
    token = add_device("pi-01")
    with db_connect() as db:
        stored = db.execute("SELECT token_hash FROM devices").fetchone()["token_hash"]
    assert stored == hash_token(token) and token not in stored


def test_duplicate_or_invalid_device_ids_are_rejected(anonymous_client):
    add_device("pi-01")
    for bad in ("pi-01", "", "../etc", "has space", "x" * 65):
        with pytest.raises(ValueError):
            add_device(bad)


def test_cleanup_is_not_available_over_http(client):
    assert client.post("/api/v1/admin/cleanup").status_code == 404


def test_admin_device_add_prints_a_working_token(anonymous_client, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert admin.main(["device", "add", "pi-07"]) == 0
    token = capsys.readouterr().out.strip().splitlines()[-1]
    assert anonymous_client.post("/api/v1/sessions", headers=bearer(token)).status_code == 201
    assert admin.main(["device", "add", "pi-07"]) == 1
    assert admin.main(["device", "list"]) == 0
    listing = capsys.readouterr().out
    assert "pi-07" in listing and token not in listing
    assert admin.main(["device", "revoke", "pi-07"]) == 0
    assert anonymous_client.post("/api/v1/sessions", headers=bearer(token)).status_code == 401


def test_admin_cleanup_runs(anonymous_client, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert admin.main(["cleanup"]) == 0
    assert "Expired portraits deleted: 0" in capsys.readouterr().out


def test_startup_keeps_working_with_auth(anonymous_client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 200
