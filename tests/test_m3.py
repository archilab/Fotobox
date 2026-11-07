"""M3: pool permission, audit trail, seed import, fair selection, separate files, withdrawal. Synthetic images only."""
import io
import os
import random
import runpy
import stat
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from PIL import Image
from conftest import process
from photobox_client import cli
from photobox_server import admin, jobs
from photobox_server import app as app_module
from photobox_server.core import cleanup_expired, db_connect, storage_root
from photobox_server.pool import import_seed, remove_seed, select_match
from photobox_server.withdrawal import hash_code, session_for_code, withdraw_session


def jpeg(color="#878787", exif=False):
    out = io.BytesIO()
    image = Image.new("RGB", (120, 160), color)
    if exif:
        data = Image.Exif()
        data[0x010F] = "SyntheticCameraMake"
        image.save(out, format="JPEG", exif=data)
    else:
        image.save(out, format="JPEG")
    return out.getvalue()


def add_seed(color="#444444"):
    with db_connect() as db:
        return import_seed(db, jpeg(color), "SYNTHETIC")


def consent(client, pool=False):
    sid = client.post("/api/v1/sessions").json()["id"]
    response = client.post(f"/api/v1/sessions/{sid}/consent", json={"processing": True, "pool": pool, "documented_externally": True})
    assert response.status_code == 200
    return sid, response.json()["withdrawal_code"]


def uploaded(client, pool=False):
    sid, code = consent(client, pool)
    assert client.post(f"/api/v1/sessions/{sid}/portrait", files={"image": ("x.jpg", jpeg(), "image/jpeg")}).status_code == 200
    return sid, code


def files(folder):
    return list((storage_root() / folder).iterdir())


def pool_id(session_id):
    with db_connect() as db:
        return db.execute("SELECT id FROM portraits WHERE session_id=? AND pool_eligible=1", (session_id,)).fetchone()[0]


def set_expired(portrait_id):
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with db_connect() as db:
        db.execute("UPDATE portraits SET expires_at=? WHERE id=?", (past, portrait_id))


def match(session_id="other"):
    with db_connect() as db:
        return select_match(db, session_id)


def events():
    with db_connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM audit_events ORDER BY id")]


# Separate files

def test_pool_consent_creates_a_separate_file_with_its_own_lifetime(client, monkeypatch):
    monkeypatch.setenv("PHOTOBOX_POOL_HOURS", "24")
    sid, _ = uploaded(client, pool=True)
    assert len(files("portraits")) == 1 and len(files("pool_portraits")) == 1
    with db_connect() as db:
        rows = {r["pool_eligible"]: r for r in db.execute("SELECT * FROM portraits WHERE session_id=?", (sid,))}
    assert Path(rows[0]["path"]).parent.name == "portraits"
    assert Path(rows[1]["path"]).parent.name == "pool_portraits"
    assert rows[0]["expires_at"] < rows[1]["expires_at"]


def test_without_pool_consent_nothing_enters_the_pool(client):
    uploaded(client)
    assert files("pool_portraits") == [] and len(files("portraits")) == 1


def test_session_image_can_expire_while_the_pool_copy_stays_usable(client):
    donor, _ = uploaded(client, pool=True)
    with db_connect() as db:
        session_row = db.execute("SELECT portrait_id FROM sessions WHERE id=?", (donor,)).fetchone()[0]
    set_expired(session_row)
    with db_connect() as db:
        cleanup_expired(db)
    assert files("portraits") == [] and len(files("pool_portraits")) == 1
    assert match()[0] == pool_id(donor)


def test_pool_files_are_private_and_swept_when_untracked(client):
    uploaded(client, pool=True)
    folder = storage_root() / "pool_portraits"
    assert stat.S_IMODE(os.stat(folder).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(files("pool_portraits")[0]).st_mode) == 0o600
    stray = folder / "stray.bin"
    stray.write_bytes(b"x")
    os.utime(stray, (1, 1))
    with db_connect() as db:
        assert cleanup_expired(db).untracked_files == 1


def test_failed_pool_copy_removes_both_files(client, monkeypatch):
    sid, _ = consent(client, pool=True)
    calls = []
    original = app_module.save_portrait

    def fail_second(img, portrait_id, folder="portraits"):
        calls.append(folder)
        if folder == "pool_portraits":
            raise OSError("disk full")
        return original(img, portrait_id, folder)

    with monkeypatch.context() as patch:
        patch.setattr(app_module, "save_portrait", fail_second)
        with pytest.raises(OSError):
            client.post(f"/api/v1/sessions/{sid}/portrait", files={"image": ("x.jpg", jpeg(), "image/jpeg")})
    assert calls == ["portraits", "pool_portraits"]
    assert files("portraits") == [] and files("pool_portraits") == []
    assert client.get(f"/api/v1/sessions/{sid}").json()["status"] == "awaiting_portrait"


# Matching: acceptance

def test_expired_pool_image_never_matches_even_before_its_file_is_deleted(client):
    donor, _ = uploaded(client, pool=True)
    set_expired(pool_id(donor))
    assert files("pool_portraits") and match() is None


def test_withdrawn_consent_never_matches_even_if_the_image_remains(client):
    donor, _ = uploaded(client, pool=True)
    with db_connect() as db:
        db.execute("UPDATE consent_records SET withdrawn_at='2026-01-01' WHERE session_id=? AND purpose='pool'", (donor,))
    assert match() is None


def test_session_only_image_never_matches(client):
    uploaded(client)
    assert match() is None


def test_own_images_never_match(client):
    donor, _ = uploaded(client, pool=True)
    assert match(donor) is None
    assert match("other")[0] == pool_id(donor)


def test_empty_pool_without_seed_gives_a_clear_error(client):
    sid, _ = uploaded(client)
    response = process(client, sid)
    assert response.status_code == 409 and "seed" in response.json()["detail"]
    assert client.get(f"/api/v1/sessions/{sid}").json()["status"] == "uploaded"


def test_pool_is_preferred_over_seeds(client):
    add_seed()
    donor, _ = uploaded(client, pool=True)
    assert match()[0] == pool_id(donor)


def test_synthetic_images_are_purged_completely(client):
    add_seed()
    donor, _ = uploaded(client, pool=True)
    sid, _ = uploaded(client)
    process(client, sid)
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with db_connect() as db:
        db.execute("UPDATE portraits SET expires_at=?", (past,))
        cleanup_expired(db)
    assert files("portraits") == [] and files("pool_portraits") == [] and files("generated") == []


# Fair selection

def test_less_used_images_are_chosen_more_often(client):
    fresh, worn = add_seed(), add_seed()
    with db_connect() as db:
        db.execute("UPDATE seeds SET use_count=9 WHERE id=?", (worn,))
    random.seed(42)
    with db_connect() as db:
        chosen = Counter(select_match(db, "x")[0] for _ in range(3000))
    ratio = chosen["seed:" + fresh] / chosen["seed:" + worn]
    assert 7 < ratio < 14


def test_no_image_is_ever_excluded_by_heavy_use(client):
    add_seed()
    worn = add_seed()
    with db_connect() as db:
        db.execute("UPDATE seeds SET use_count=99 WHERE id=?", (worn,))
    random.seed(1)
    with db_connect() as db:
        assert "seed:" + worn in {select_match(db, "x")[0] for _ in range(3000)}


def test_use_is_counted_only_after_success(client, monkeypatch):
    seed = add_seed()
    sid, _ = uploaded(client)
    monkeypatch.setenv("PHOTOBOX_GENERATION_MAX_ATTEMPTS", "1")

    class Broken:
        name, strip_footer = "broken", "BROKEN"

        def generate_frame(self, request, should_cancel):
            raise jobs.GenerationError("broken")

    assert process(client, sid, Broken()).status_code == 202
    with db_connect() as db:
        assert db.execute("SELECT use_count FROM seeds WHERE id=?", (seed,)).fetchone()[0] == 0
    assert process(client, sid).status_code == 202
    with db_connect() as db:
        assert db.execute("SELECT use_count FROM seeds WHERE id=?", (seed,)).fetchone()[0] == 1


# Seeds are admin-only

def test_file_placed_in_the_seed_folder_is_ignored(client):
    Image.new("RGB", (120, 160)).save(storage_root() / "seed_portraits" / "unvetted.png")
    assert match() is None


def test_seed_import_records_rights_and_removes_metadata(anonymous_client):
    with db_connect() as db:
        seed = import_seed(db, jpeg(exif=True), "LICENSE-123")
        row = db.execute("SELECT * FROM seeds WHERE id=?", (seed,)).fetchone()
    assert row["rights_ref"] == "LICENSE-123"
    assert b"SyntheticCameraMake" not in Path(row["path"]).read_bytes()
    assert stat.S_IMODE(os.stat(row["path"]).st_mode) == 0o600


@pytest.mark.parametrize("payload,reference", [(b"not an image", "OK"), (None, "has space"), (None, ""), (None, "x" * 65)])
def test_unsuitable_seed_is_refused(anonymous_client, payload, reference):
    with db_connect() as db:
        with pytest.raises(ValueError):
            import_seed(db, payload or jpeg(), reference)
    assert files("seed_portraits") == []


def test_admin_seed_add_requires_rights_attestation(anonymous_client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    image = tmp_path / "seed.jpg"
    image.write_bytes(jpeg())
    with pytest.raises(SystemExit):
        admin.main(["seed", "add", str(image), "--rights-reference", "X"])
    assert files("seed_portraits") == []
    assert admin.main(["seed", "add", str(image), "--rights-reference", "X", "--attest-rights"]) == 0
    assert len(files("seed_portraits")) == 1


def test_admin_seed_list_and_remove(anonymous_client, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    seed = add_seed()
    admin.main(["seed", "list"])
    assert seed in capsys.readouterr().out
    assert admin.main(["seed", "remove", seed]) == 0
    assert admin.main(["seed", "remove", seed]) == 1
    capsys.readouterr()
    admin.main(["seed", "list"])
    assert seed not in capsys.readouterr().out
    assert files("seed_portraits") == []


def test_admin_seed_add_reports_an_unreadable_image(anonymous_client, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")
    assert admin.main(["seed", "add", str(bad), "--rights-reference", "X", "--attest-rights"]) == 1
    assert "invalid image" in capsys.readouterr().err


def test_removing_a_seed_deletes_strips_containing_it(client):
    seed = add_seed()
    sid, _ = uploaded(client)
    assert process(client, sid).status_code == 202
    assert client.get(f"/api/v1/sessions/{sid}/result").status_code == 200
    with db_connect() as db:
        assert remove_seed(db, seed) is True
    assert files("generated") == []
    assert client.get(f"/api/v1/sessions/{sid}/result").status_code == 404


def test_no_http_route_can_add_a_seed_or_withdraw(client):
    paths = {route.path for route in app_module.app.routes}
    assert not any("seed" in path or "withdraw" in path or "audit" in path for path in paths)


def test_synthetic_seed_script_imports_through_the_same_path(anonymous_client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    script = Path(__file__).parents[1] / "scripts" / "create_synthetic_seed.py"
    runpy.run_path(str(script))
    with db_connect() as db:
        assert db.execute("SELECT rights_ref FROM seeds").fetchone()[0] == "SYNTHETIC"


# Withdrawal

def test_withdrawal_deletes_images_and_keeps_the_record(client):
    sid, code = uploaded(client, pool=True)
    with db_connect() as db:
        result = withdraw_session(db, session_for_code(db, code))
        records = db.execute("SELECT purpose,withdrawn_at FROM consent_records WHERE session_id=?", (sid,)).fetchall()
        remaining = db.execute("SELECT COUNT(*) FROM portraits WHERE session_id=?", (sid,)).fetchone()[0]
    assert result.session_id == sid and result.portraits == 2 and result.already_withdrawn is False
    assert files("portraits") == [] and files("pool_portraits") == [] and remaining == 0
    assert len(records) == 2 and all(r["withdrawn_at"] for r in records)
    assert client.get(f"/api/v1/sessions/{sid}").json()["status"] == "withdrawn"


def test_withdrawal_deletes_other_sessions_strips_that_contain_the_image(client):
    donor, code = uploaded(client, pool=True)
    other, _ = uploaded(client)
    assert process(client, other).status_code == 202
    assert client.get(f"/api/v1/sessions/{other}/result").status_code == 200
    with db_connect() as db:
        assert withdraw_session(db, session_for_code(db, code)).strips == 1
    assert files("generated") == []
    assert client.get(f"/api/v1/sessions/{other}/result").status_code == 404
    assert client.get(f"/api/v1/sessions/{other}").json()["has_result"] is False
    assert len(files("portraits")) == 1


def test_withdrawn_image_never_matches_again(client):
    donor, code = uploaded(client, pool=True)
    add_seed()
    with db_connect() as db:
        withdraw_session(db, session_for_code(db, code))
    assert match()[0].startswith("seed:")


@pytest.mark.parametrize("stage", ["awaiting_portrait", "uploaded", "complete"])
def test_withdrawn_session_accepts_nothing_more(client, stage):
    add_seed()
    sid, code = consent(client)
    if stage != "awaiting_portrait":
        client.post(f"/api/v1/sessions/{sid}/portrait", files={"image": ("x.jpg", jpeg(), "image/jpeg")})
    if stage == "complete":
        process(client, sid)
    with db_connect() as db:
        withdraw_session(db, sid)
    assert client.post(f"/api/v1/sessions/{sid}/portrait", files={"image": ("x.jpg", jpeg(), "image/jpeg")}).status_code == 409
    assert process(client, sid).status_code == 409
    assert client.get(f"/api/v1/sessions/{sid}/result").status_code == 404
    assert client.post(f"/api/v1/sessions/{sid}/consent", json={"processing": True, "documented_externally": True}).status_code == 409
    assert client.get(f"/api/v1/sessions/{sid}").json()["status"] == "withdrawn"


def test_withdrawal_during_generation_leaves_no_strip_or_frames(client):
    from photobox_server.providers.mock import MockProvider
    add_seed()
    sid, _ = uploaded(client)

    class WithdrawMidway(MockProvider):
        def generate_frame(self, request, should_cancel):
            with db_connect() as db:
                withdraw_session(db, sid)
            return super().generate_frame(request, should_cancel)

    assert process(client, sid, WithdrawMidway()).status_code == 202
    assert files("generated") == [] and files("jobs") == []
    assert client.get(f"/api/v1/sessions/{sid}").json()["status"] == "withdrawn"


def test_withdrawal_can_be_repeated(client):
    sid, _ = uploaded(client)
    with db_connect() as db:
        withdraw_session(db, sid)
        again = withdraw_session(db, sid)
    assert again.already_withdrawn is True and again.portraits == 0
    assert sum(e["event"] == "consent_withdrawn" for e in events()) == 1


def test_unknown_session_or_code_finds_nothing(anonymous_client):
    with db_connect() as db:
        assert withdraw_session(db, "missing") is None
        assert session_for_code(db, "missing") is None


def test_code_is_shown_once_and_only_its_hash_is_stored(client):
    sid, code = consent(client)
    repeat = client.post(f"/api/v1/sessions/{sid}/consent", json={"processing": True, "documented_externally": True})
    assert code and repeat.status_code == 200 and repeat.json()["withdrawal_code"] is None
    with db_connect() as db:
        stored = db.execute("SELECT withdrawal_hash FROM sessions WHERE id=?", (sid,)).fetchone()[0]
    assert stored == hash_code(code) and code not in stored
    database = Path(os.environ["PHOTOBOX_DB_PATH"])
    with db_connect():
        assert code.encode() not in database.read_bytes()


def test_each_session_gets_its_own_code(client):
    assert consent(client)[1] != consent(client)[1]


def test_admin_withdraws_by_code_prompt(client, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    sid, code = uploaded(client)
    monkeypatch.setattr("builtins.input", lambda _prompt: code + "\n")
    assert admin.main(["withdraw"]) == 0
    output = capsys.readouterr().out
    assert sid in output and code not in output and "not covered" in output
    assert client.get(f"/api/v1/sessions/{sid}").json()["status"] == "withdrawn"


def test_admin_withdraws_by_session_and_reports_unknown(client, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    sid, _ = uploaded(client)
    assert admin.main(["withdraw", "--session", sid]) == 0
    assert admin.main(["withdraw", "--session", "missing"]) == 1
    monkeypatch.setattr("builtins.input", lambda _prompt: "wrong")
    assert admin.main(["withdraw"]) == 1


def test_client_shows_the_code_without_the_token(tmp_path, monkeypatch, capsys):
    import httpx

    def handler(request):
        if request.url.path.endswith("/sessions"):
            return httpx.Response(201, json={"id": "sid", "status": "awaiting_consent"})
        if request.url.path.endswith("/consent"):
            return httpx.Response(200, json={"status": "awaiting_portrait", "withdrawal_code": "CODE-123"})
        return httpx.Response(200, json={"status": "ok"})

    real = httpx.Client
    monkeypatch.setattr(cli.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setenv(cli.TOKEN_ENV, "secret-token")
    image = tmp_path / "x.jpg"
    image.write_bytes(jpeg())
    cli.main(["capture", "--file", str(image), "--attest-documented-consent", "--no-process"])
    captured = capsys.readouterr()
    assert "CODE-123" in captured.out and "secret-token" not in captured.out + captured.err


# Audit trail

def test_audit_trail_follows_a_session(client):
    add_seed()
    donor, _ = uploaded(client, pool=True)
    sid, code = uploaded(client)
    process(client, sid)
    with db_connect() as db:
        withdraw_session(db, sid)
    found = [(e["event"], e["session_id"], e["detail"]) for e in events() if e["session_id"] == sid]
    assert found[:2] == [("consent_granted", sid, "processing"), ("match_used", sid, None)]
    assert ("consent_withdrawn", sid, None) in found
    assert ("portrait_deleted", sid, "withdrawn") in found
    assert ("strip_deleted", sid, None) in found
    granted = [e for e in events() if e["event"] == "consent_granted" and e["session_id"] == donor]
    assert {e["detail"] for e in granted} == {"processing", "pool"} and granted[0]["device_id"] == "pi-01"


def test_audit_records_seeds_and_expiry(client):
    seed = add_seed()
    sid, _ = uploaded(client)
    with db_connect() as db:
        session_row = db.execute("SELECT portrait_id FROM sessions WHERE id=?", (sid,)).fetchone()[0]
    set_expired(session_row)
    with db_connect() as db:
        cleanup_expired(db)
        remove_seed(db, seed)
    kinds = [(e["event"], e["detail"]) for e in events()]
    assert ("seed_imported", "SYNTHETIC") in kinds and ("portrait_deleted", "expired") in kinds and ("seed_removed", None) in kinds


def test_audit_never_contains_codes_tokens_or_paths(client):
    add_seed()
    sid, code = uploaded(client, pool=True)
    with db_connect() as db:
        withdraw_session(db, sid)
    text = " ".join(str(value) for event in events() for value in event.values())
    assert code not in text and client.headers["Authorization"].split()[1] not in text
    assert str(storage_root()) not in text and "jpg" not in text


def test_failed_action_leaves_no_audit_event(client, monkeypatch):
    sid = client.post("/api/v1/sessions").json()["id"]
    assert client.post(f"/api/v1/sessions/{sid}/consent", json={"processing": True}).status_code == 403
    assert events() == []


def test_admin_audit_prints_events_without_paths(client, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    add_seed()
    uploaded(client)
    assert admin.main(["audit", "--limit", "1"]) == 0
    output = capsys.readouterr().out.strip().splitlines()
    assert len(output) == 1 and "consent_granted" in output[0]
