"""M2: session states, idempotency, atomic writes, upload limits, consent records, restart. Synthetic images only."""
import io
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from conftest import process
from photobox_client import cli
from photobox_server import app as app_module
from photobox_server import core, jobs
from photobox_server.app import app
from photobox_server.auth import create_device
from photobox_server.core import db_connect, storage_root
from photobox_server.pool import import_seed
from photobox_server.schema import MIGRATIONS, schema_version
from photobox_server.sessions import TRANSITIONS, transition


def png(color="#878787", size=(120, 160)):
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return out.getvalue()


def new_session(client, pool=False, **consent):
    sid = client.post("/api/v1/sessions").json()["id"]
    body = {"processing": True, "pool": pool, "documented_externally": True, **consent}
    assert client.post(f"/api/v1/sessions/{sid}/consent", json=body).status_code == 200
    return sid


def upload(client, sid, data=None, key=None):
    headers = {"Idempotency-Key": key} if key else {}
    return client.post(f"/api/v1/sessions/{sid}/portrait", files={"image": ("x.png", data or png(), "image/png")}, headers=headers)


def add_seed(color="#444444"):
    out = io.BytesIO()
    Image.new("RGB", (120, 160), color).save(out, format="PNG")
    with db_connect() as db:
        return import_seed(db, out.getvalue(), "SYNTHETIC")


def count(table):
    with db_connect() as db:
        return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def portrait_files():
    return list((storage_root() / "portraits").iterdir())


def status_of(client, sid):
    return client.get(f"/api/v1/sessions/{sid}").json()["status"]


# State table

def test_every_listed_transition_is_the_documented_one():
    assert TRANSITIONS == {
        ("awaiting_consent", "awaiting_portrait"),
        ("awaiting_portrait", "uploaded"),
        ("uploaded", "queued"),
        ("queued", "processing"),
        ("processing", "complete"),
        ("processing", "failed"),
        ("processing", "queued"),
        ("failed", "queued"),
        ("queued", "uploaded"),
        ("processing", "uploaded"),
    }


def test_transition_rejects_a_move_not_in_the_table(anonymous_client):
    with db_connect() as db:
        with pytest.raises(ValueError):
            transition(db, "any", "awaiting_consent", "complete")


def test_transition_only_applies_from_the_expected_state(client):
    sid = client.post("/api/v1/sessions").json()["id"]
    with db_connect() as db:
        assert transition(db, sid, "awaiting_portrait", "uploaded") is False
        assert transition(db, sid, "awaiting_consent", "awaiting_portrait") is True
    assert status_of(client, sid) == "awaiting_portrait"


@pytest.mark.parametrize("step", ["portrait", "process"])
def test_steps_out_of_order_are_refused(client, step):
    sid = client.post("/api/v1/sessions").json()["id"]
    response = upload(client, sid) if step == "portrait" else client.post(f"/api/v1/sessions/{sid}/process")
    assert response.status_code == 409
    assert status_of(client, sid) == "awaiting_consent"


# Concurrency

def test_concurrent_uploads_store_exactly_one_portrait(client):
    sid = new_session(client)
    data = png()
    with ThreadPoolExecutor(8) as pool:
        codes = list(pool.map(lambda _: upload(client, sid, data).status_code, range(8)))
    assert codes.count(200) == 1 and codes.count(409) == 7
    assert count("portraits") == 1 and len(portrait_files()) == 1


def test_concurrent_processing_queues_one_job(client):
    add_seed()
    sid = new_session(client)
    upload(client, sid)
    with ThreadPoolExecutor(6) as pool:
        responses = list(pool.map(lambda _: client.post(f"/api/v1/sessions/{sid}/process"), range(6)))
    assert {r.status_code for r in responses} == {202}
    assert len({r.json()["job_id"] for r in responses}) == 1 and count("generation_jobs") == 1
    assert jobs.run_pending() == 1
    assert status_of(client, sid) == "complete"


def test_concurrent_session_creation_with_one_key_creates_one(client):
    with ThreadPoolExecutor(8) as pool:
        responses = list(pool.map(lambda _: client.post("/api/v1/sessions", headers={"Idempotency-Key": "same"}), range(8)))
    assert len({r.json()["id"] for r in responses}) == 1 and count("sessions") == 1


# Idempotency

def test_repeated_session_creation_returns_the_same_session(client):
    first = client.post("/api/v1/sessions", headers={"Idempotency-Key": "k1"})
    again = client.post("/api/v1/sessions", headers={"Idempotency-Key": "k1"})
    assert first.status_code == 201 and again.status_code == 200
    assert first.json()["id"] == again.json()["id"] and count("sessions") == 1


def test_session_keys_belong_to_one_device(client):
    with db_connect() as db:
        other = {"Authorization": f"Bearer {create_device(db, 'pi-02')}"}
    mine = client.post("/api/v1/sessions", headers={"Idempotency-Key": "k1"}).json()["id"]
    theirs = client.post("/api/v1/sessions", headers={"Idempotency-Key": "k1", **other}).json()["id"]
    assert mine != theirs


def test_requests_without_a_key_stay_separate(client):
    assert client.post("/api/v1/sessions").json()["id"] != client.post("/api/v1/sessions").json()["id"]


@pytest.mark.parametrize("key", ["", "has space", "x" * 129, "bad/slash"])
def test_invalid_key_is_refused(client, key):
    assert client.post("/api/v1/sessions", headers={"Idempotency-Key": key}).status_code == 400
    assert count("sessions") == 0


def test_repeated_upload_returns_the_same_portrait(client):
    sid = new_session(client)
    data = png()
    first = upload(client, sid, data, key="u1")
    again = upload(client, sid, data, key="u1")
    assert first.status_code == again.status_code == 200
    assert first.json() == again.json()
    assert count("portraits") == 1 and len(portrait_files()) == 1


def test_key_reused_for_a_different_image_is_refused(client):
    sid = new_session(client)
    upload(client, sid, png("#111111"), key="u1")
    response = upload(client, sid, png("#222222"), key="u1")
    assert response.status_code == 409 and "different image" in response.text
    assert count("portraits") == 1


def test_second_upload_with_another_key_is_refused(client):
    sid = new_session(client)
    upload(client, sid, key="u1")
    assert upload(client, sid, key="u2").status_code == 409
    assert upload(client, sid).status_code == 409
    assert count("portraits") == 1


def test_upload_key_of_another_device_reveals_nothing(client):
    sid = new_session(client)
    data = png()
    upload(client, sid, data, key="u1")
    with db_connect() as db:
        token = create_device(db, "pi-02")
    response = client.post(f"/api/v1/sessions/{sid}/portrait", files={"image": ("x.png", data, "image/png")}, headers={"Idempotency-Key": "u1", "Authorization": f"Bearer {token}"})
    assert response.status_code == 404


def test_repeated_processing_does_not_generate_again(client):
    add_seed()
    sid = new_session(client)
    upload(client, sid)
    first = process(client, sid)
    again = client.post(f"/api/v1/sessions/{sid}/process")
    assert again.status_code == 200 and again.json()["job_id"] == first.json()["job_id"]
    assert jobs.run_pending() == 0


def test_repeated_consent_succeeds_and_records_once(client):
    sid = new_session(client, pool=True)
    body = {"processing": True, "pool": True, "documented_externally": True}
    assert client.post(f"/api/v1/sessions/{sid}/consent", json=body).status_code == 200
    assert count("consent_records") == 2


@pytest.mark.parametrize("changed", [
    {"processing": True, "pool": True, "documented_externally": True},
    {"processing": True, "pool": False, "documented_externally": False},
    {"processing": False, "pool": False, "documented_externally": True},
    {"processing": True, "pool": False, "documented_externally": True, "evidence_ref": "FORM-9"},
])
def test_consent_cannot_be_changed_by_a_later_request(client, changed):
    sid = new_session(client)
    assert client.post(f"/api/v1/sessions/{sid}/consent", json=changed).status_code == 409
    assert count("consent_records") == 1


# Atomic writes and database consistency

def test_failed_image_write_leaves_no_file_and_no_state_change(client, monkeypatch):
    sid = new_session(client)
    data = png()
    with monkeypatch.context() as patch:
        patch.setattr(Image.Image, "save", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(OSError):
            upload(client, sid, data)
    assert portrait_files() == [] and count("portraits") == 0
    assert status_of(client, sid) == "awaiting_portrait"


def test_interrupted_write_never_appears_under_the_final_name(tmp_path):
    dest = tmp_path / "result.jpg"

    class Broken:
        def save(self, handle, *_args, **_kwargs):
            handle.write(b"partial")
            raise OSError("disk full")

    with pytest.raises(OSError):
        core.write_private_image(Broken(), dest, "JPEG")
    assert list(tmp_path.iterdir()) == []


def test_database_failure_keeps_the_session_able_to_upload(client, monkeypatch):
    sid = new_session(client)

    def fail():
        raise RuntimeError("simulated")

    with monkeypatch.context() as patch:
        patch.setattr(app_module, "now_iso", fail)
        with pytest.raises(RuntimeError):
            upload(client, sid)
    assert status_of(client, sid) == "awaiting_portrait" and portrait_files() == []
    assert upload(client, sid).status_code == 200


def test_process_without_match_changes_nothing(client):
    sid = new_session(client)
    upload(client, sid)
    assert client.post(f"/api/v1/sessions/{sid}/process").status_code == 409
    assert status_of(client, sid) == "uploaded"


def test_failed_generation_can_be_requested_again(client, monkeypatch):
    add_seed()
    sid = new_session(client)
    upload(client, sid)
    monkeypatch.setenv("PHOTOBOX_GENERATION_MAX_ATTEMPTS", "1")

    class Broken:
        name, strip_footer = "broken", "BROKEN"

        def generate_frame(self, request, should_cancel):
            raise jobs.GenerationError("broken")

    assert process(client, sid, Broken()).status_code == 202
    state = client.get(f"/api/v1/sessions/{sid}").json()
    assert state["status"] == "failed" and state["error"] == "broken"
    assert process(client, sid).status_code == 202
    state = client.get(f"/api/v1/sessions/{sid}").json()
    assert state["status"] == "complete" and state["error"] is None


# Upload validation

@pytest.mark.parametrize("size,accepted", [((64, 64), True), ((63, 200), False), ((200, 63), False), ((8000, 100), True), ((8001, 100), False)])
def test_side_limits(client, size, accepted):
    response = upload(client, new_session(client), png(size=size))
    assert response.status_code == (200 if accepted else 422)


def test_animated_image_is_refused(client):
    out = io.BytesIO()
    frames = [Image.new("RGB", (100, 100), color) for color in ("#111111", "#222222")]
    frames[0].save(out, format="PNG", save_all=True, append_images=frames[1:])
    response = upload(client, new_session(client), out.getvalue())
    assert response.status_code == 422 and "animated" in response.text


def test_non_image_type_is_refused_whatever_it_claims(client):
    out = io.BytesIO()
    Image.new("RGB", (100, 100)).save(out, format="BMP")
    assert upload(client, new_session(client), out.getvalue()).status_code == 422


def test_image_over_the_byte_limit_is_refused(client, monkeypatch):
    sid = new_session(client)
    monkeypatch.setenv("PHOTOBOX_MAX_UPLOAD_BYTES", "1000")
    response = upload(client, sid, b"x" * 2000)
    assert response.status_code == 413 and portrait_files() == []


def test_body_over_the_limit_is_refused_before_it_is_read(client, monkeypatch):
    sid = new_session(client)
    monkeypatch.setenv("PHOTOBOX_MAX_UPLOAD_BYTES", "1000")
    response = upload(client, sid, b"x" * 200_000)
    assert response.status_code == 413


def test_upload_to_another_devices_session_does_not_decode_the_image(client, monkeypatch):
    sid = new_session(client)
    with db_connect() as db:
        token = create_device(db, "pi-02")
    monkeypatch.setattr(app_module, "validate_image", lambda _: pytest.fail("decoded before authorization"))
    response = client.post(f"/api/v1/sessions/{sid}/portrait", files={"image": ("x.png", png(), "image/png")}, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 404


# Per-device throttling

def test_uploads_are_limited_per_device(client, monkeypatch):
    monkeypatch.setenv("PHOTOBOX_MAX_UPLOADS_PER_MINUTE", "2")
    with db_connect() as db:
        other = {"Authorization": f"Bearer {create_device(db, 'pi-02')}"}
    sessions = [new_session(client) for _ in range(3)]
    assert [upload(client, s).status_code for s in sessions[:2]] == [200, 200]
    refused = upload(client, sessions[2])
    assert refused.status_code == 429 and int(refused.headers["Retry-After"]) >= 1
    assert count("portraits") == 2
    sid = client.post("/api/v1/sessions", headers=other).json()["id"]
    response = client.post(f"/api/v1/sessions/{sid}/portrait", files={"image": ("x.png", png(), "image/png")}, headers=other)
    assert response.status_code == 409


def test_limit_frees_up_after_the_window(client, monkeypatch):
    from photobox_server import throttle
    now = [1000.0]
    monkeypatch.setattr(throttle.time, "monotonic", lambda: now[0])
    monkeypatch.setenv("PHOTOBOX_MAX_UPLOADS_PER_MINUTE", "1")
    assert throttle.retry_after("d") is None
    assert throttle.retry_after("d") == 61
    now[0] += 61
    assert throttle.retry_after("d") is None


def test_unauthenticated_uploads_do_not_use_up_a_devices_limit(anonymous_client, monkeypatch):
    monkeypatch.setenv("PHOTOBOX_MAX_UPLOADS_PER_MINUTE", "1")
    for _ in range(3):
        assert anonymous_client.post("/api/v1/sessions/x/portrait", files={"image": ("x.png", b"x", "image/png")}).status_code == 401


# Consent records

def records(sid):
    with db_connect() as db:
        return {r["purpose"]: dict(r) for r in db.execute("SELECT * FROM consent_records WHERE session_id=?", (sid,))}


def test_consent_is_recorded_per_purpose_with_version_and_provenance(client):
    sid = new_session(client, evidence_ref="FORM-0042")
    found = records(sid)
    assert set(found) == {"processing"}
    record = found["processing"]
    assert record["policy_version"] == app_module.CONSENT_POLICY_VERSION
    assert record["provenance"] == "operator_attestation"
    assert record["evidence_ref"] == "FORM-0042" and record["granted_at"] and record["withdrawn_at"] is None


def test_pool_is_a_separate_record_and_decides_eligibility(client):
    pool, plain = new_session(client, pool=True), new_session(client)
    assert set(records(pool)) == {"processing", "pool"} and set(records(plain)) == {"processing"}
    upload(client, pool)
    upload(client, plain)
    with db_connect() as db:
        rows = {(r["session_id"], r["pool_eligible"]) for r in db.execute("SELECT session_id,pool_eligible FROM portraits")}
    assert rows == {(pool, 0), (pool, 1), (plain, 0)}


def test_refused_consent_records_nothing(client):
    sid = client.post("/api/v1/sessions").json()["id"]
    assert client.post(f"/api/v1/sessions/{sid}/consent", json={"processing": True}).status_code == 403
    assert records(sid) == {}


@pytest.mark.parametrize("value", ["with space", "name@example.com", "x" * 65, "ä"])
def test_evidence_reference_format_is_limited(client, value):
    sid = client.post("/api/v1/sessions").json()["id"]
    body = {"processing": True, "documented_externally": True, "evidence_ref": value}
    assert client.post(f"/api/v1/sessions/{sid}/consent", json=body).status_code == 422
    assert records(sid) == {}


# Migration

def old_database(path):
    db = sqlite3.connect(path)
    for statement in MIGRATIONS[0]:
        db.execute(statement)
    db.execute("INSERT INTO sessions(id,box_id,status,consent_processing,consent_pool,created_at) VALUES('both','pi','uploaded',1,1,'2026-01-01T00:00:00+00:00')")
    db.execute("INSERT INTO sessions(id,box_id,status,consent_processing,consent_pool,created_at) VALUES('one','pi','uploaded',1,0,'2026-01-02T00:00:00+00:00')")
    db.execute("INSERT INTO sessions(id,box_id,status,consent_processing,consent_pool,created_at) VALUES('none','pi','awaiting_consent',0,0,'2026-01-03T00:00:00+00:00')")
    db.execute("INSERT INTO devices(id,token_hash,created_at) VALUES('pi','hash','2026-01-01T00:00:00+00:00')")
    db.commit()
    db.close()


def test_old_database_is_migrated_without_losing_data(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOBOX_STORAGE_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PHOTOBOX_DB_PATH", str(tmp_path / "old.sqlite3"))
    old_database(tmp_path / "old.sqlite3")
    with db_connect() as db:
        assert schema_version(db) == len(MIGRATIONS)
        columns = {r["name"] for r in db.execute("PRAGMA table_info(sessions)")}
        assert "consent_pool" not in columns and "create_key" in columns
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 3
        assert db.execute("SELECT COUNT(*) FROM devices").fetchone()[0] == 1
        rows = [tuple(r) for r in db.execute("SELECT session_id,purpose,provenance,policy_version,granted_at FROM consent_records ORDER BY session_id,purpose")]
    assert rows == [
        ("both", "pool", "migrated_flag", "pre-m2", "2026-01-01T00:00:00+00:00"),
        ("both", "processing", "migrated_flag", "pre-m2", "2026-01-01T00:00:00+00:00"),
        ("one", "processing", "migrated_flag", "pre-m2", "2026-01-02T00:00:00+00:00"),
    ]
    with db_connect() as db:
        assert db.execute("SELECT COUNT(*) FROM consent_records").fetchone()[0] == 3


def test_failed_migration_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOBOX_STORAGE_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PHOTOBOX_DB_PATH", str(tmp_path / "old.sqlite3"))
    old_database(tmp_path / "old.sqlite3")
    from photobox_server import schema
    monkeypatch.setattr(schema, "MIGRATIONS", MIGRATIONS + (("THIS IS NOT SQL",),))
    with pytest.raises(sqlite3.OperationalError):
        with db_connect():
            pass
    db = sqlite3.connect(tmp_path / "old.sqlite3")
    assert db.execute("PRAGMA user_version").fetchone()[0] == 0
    assert "consent_pool" in {r[1] for r in db.execute("PRAGMA table_info(sessions)")}
    db.close()


def test_database_from_a_newer_version_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOBOX_STORAGE_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PHOTOBOX_DB_PATH", str(tmp_path / "new.sqlite3"))
    db = sqlite3.connect(tmp_path / "new.sqlite3")
    db.execute("PRAGMA user_version = 999")
    db.close()
    with pytest.raises(RuntimeError, match="newer"):
        with db_connect():
            pass


# Restart

def wait_for(client, sid, status):
    for _ in range(200):
        if status_of(client, sid) == status:
            return
        time.sleep(0.02)
    pytest.fail(f"session never became {status}: {status_of(client, sid)}")


def test_restart_keeps_uploaded_state_and_processing_continues(client, tmp_path, monkeypatch):
    add_seed()
    monkeypatch.chdir(tmp_path)
    token_header = client.headers["Authorization"]
    sid = new_session(client)
    portrait = upload(client, sid, key="u1").json()["portrait_id"]
    with TestClient(app, headers={"Authorization": token_header}) as restarted:
        assert status_of(restarted, sid) == "uploaded"
        assert upload(restarted, sid, key="u1").json()["portrait_id"] == portrait
        assert restarted.post(f"/api/v1/sessions/{sid}/process").status_code == 202
        wait_for(restarted, sid, "complete")
    with TestClient(app, headers={"Authorization": token_header}) as restarted:
        assert restarted.get(f"/api/v1/sessions/{sid}/result").status_code == 200


def test_session_interrupted_while_processing_continues_after_restart(client, tmp_path, monkeypatch):
    add_seed()
    monkeypatch.chdir(tmp_path)
    token_header = client.headers["Authorization"]
    sid = new_session(client)
    upload(client, sid)
    client.post(f"/api/v1/sessions/{sid}/process")
    with db_connect() as db:
        transition(db, sid, "queued", "processing")
        db.execute("UPDATE generation_jobs SET status='running',run_id='gone',attempts=1")
    with TestClient(app, headers={"Authorization": token_header}) as restarted:
        wait_for(restarted, sid, "complete")


# Client

class FlakyServer:
    """Fails the first call to each path, as if the connection dropped after the server acted."""

    def __init__(self):
        self.requests = []
        self.failed = set()

    def __call__(self, request):
        path = request.url.path
        self.requests.append(request)
        if path not in self.failed:
            self.failed.add(path)
            raise httpx.ReadTimeout("lost", request=request)
        if path.endswith("/sessions"):
            return httpx.Response(201, json={"id": "sid", "status": "awaiting_consent"})
        return httpx.Response(200, json={"status": "ok"})


@pytest.fixture
def flaky(monkeypatch):
    server = FlakyServer()
    real_client = httpx.Client

    def fake_client(**kwargs):
        return real_client(transport=httpx.MockTransport(server), **kwargs)

    monkeypatch.setattr(cli.httpx, "Client", fake_client)
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    monkeypatch.setenv("PHOTOBOX_DEVICE_TOKEN", "test-token")
    return server


def test_client_retries_with_the_same_keys(flaky, tmp_path, capsys):
    image = tmp_path / "x.png"
    image.write_bytes(png())
    cli.main(["capture", "--file", str(image), "--attest-documented-consent", "--no-process"])
    sessions = [r for r in flaky.requests if r.url.path.endswith("/sessions")]
    uploads = [r for r in flaky.requests if r.url.path.endswith("/portrait")]
    assert len(sessions) == 2 and len(uploads) == 2
    assert sessions[0].headers["Idempotency-Key"] == sessions[1].headers["Idempotency-Key"]
    assert uploads[0].headers["Idempotency-Key"] == uploads[1].headers["Idempotency-Key"]
    assert sessions[0].headers["Idempotency-Key"] != uploads[0].headers["Idempotency-Key"]
    assert "test-token" not in capsys.readouterr().err


def test_client_stops_after_bounded_retries(monkeypatch, tmp_path, capsys):
    calls = []

    def always_down(request):
        calls.append(request)
        raise httpx.ConnectError("down", request=request)

    real_client = httpx.Client
    monkeypatch.setattr(cli.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(always_down), **kw))
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    monkeypatch.setenv("PHOTOBOX_DEVICE_TOKEN", "t")
    image = tmp_path / "x.png"
    image.write_bytes(png())
    with pytest.raises(SystemExit):
        cli.main(["capture", "--file", str(image), "--attest-documented-consent"])
    assert len(calls) == cli.NETWORK_ATTEMPTS


def test_client_does_not_retry_when_the_server_answered(monkeypatch, tmp_path):
    calls = []

    def refuse(request):
        calls.append(request)
        return httpx.Response(429, json={"detail": "too many uploads from this device"})

    real_client = httpx.Client
    monkeypatch.setattr(cli.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(refuse), **kw))
    monkeypatch.setenv("PHOTOBOX_DEVICE_TOKEN", "t")
    image = tmp_path / "x.png"
    image.write_bytes(png())
    with pytest.raises(SystemExit):
        cli.main(["capture", "--file", str(image), "--attest-documented-consent"])
    assert len(calls) == 1


def test_consent_reference_reaches_the_server(flaky, tmp_path):
    image = tmp_path / "x.png"
    image.write_bytes(png())
    cli.main(["capture", "--file", str(image), "--attest-documented-consent", "--no-process", "--consent-reference", "FORM-7"])
    consent = next(r for r in flaky.requests if r.url.path.endswith("/consent"))
    assert b"FORM-7" in consent.content


def test_tests_use_no_real_portraits():
    assert not list(Path(__file__).parent.glob("*.jp*g"))
