"""Moving an operator's content to a new server, and nothing about visitors. Synthetic images only."""
import importlib.util
import io
import json
from pathlib import Path
from PIL import Image
from photobox_server.core import db_connect, storage_root
from photobox_server.library import active_notice, save_notice
from photobox_server.pool import import_seed

spec = importlib.util.spec_from_file_location("content_transfer", Path(__file__).parent.parent / "scripts" / "content_transfer.py")
transfer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transfer)


def jpeg():
    out = io.BytesIO()
    Image.new("RGB", (120, 160), "#222").save(out, format="JPEG")
    return out.getvalue()


def visit(client):
    sid = client.post("/api/v1/sessions").json()["id"]
    client.post(f"/api/v1/sessions/{sid}/consent", json={"processing": True, "documented_externally": True})
    client.post(f"/api/v1/sessions/{sid}/portrait", files={"image": ("x.jpg", jpeg(), "image/jpeg")})
    return sid


def move(client, tmp_path, monkeypatch):
    with db_connect() as db:
        seed = import_seed(db, jpeg(), "SYNTHETIC")
        save_notice(db, "PROCESSING")
        save_notice(db, "POOL until {until}", "pool")
    visit(client)
    folder = tmp_path / "transfer"
    counts = transfer.export_content(folder)
    monkeypatch.setenv("PHOTOBOX_STORAGE_DIR", str(tmp_path / "new"))
    monkeypatch.setenv("PHOTOBOX_DB_PATH", str(tmp_path / "new.sqlite3"))
    return seed, folder, counts


def test_content_arrives_active_and_usable(client, tmp_path, monkeypatch):
    seed, folder, counts = move(client, tmp_path, monkeypatch)
    assert counts["seeds"] == 1 and counts["consent_notices"] == 2
    assert transfer.import_content(folder)["seeds"] == 1
    with db_connect() as db:
        assert active_notice(db)[1] == "PROCESSING" and active_notice(db, "pool")[1].startswith("POOL")
        row = db.execute("SELECT * FROM seeds").fetchone()
    assert row["id"] == seed and row["rights_ref"] == "SYNTHETIC"
    assert Path(row["path"]).parent == storage_root() / "seed_portraits" and Path(row["path"]).read_bytes()[:2] == b"\xff\xd8"
    assert oct(Path(row["path"]).stat().st_mode)[-3:] == "600"


def test_nothing_about_visitors_or_devices_moves(client, tmp_path, monkeypatch):
    _, folder, _ = move(client, tmp_path, monkeypatch)
    manifest = json.loads((folder / "content.json").read_text())
    assert set(manifest) == {"consent_notices", "prompt_sets", "message_filter", "seeds"}
    assert [p.name for p in folder.iterdir() if p.suffix != ".json" and not p.name.startswith("seed-")] == []
    transfer.import_content(folder)
    with db_connect() as db:
        for table in ("sessions", "portraits", "consent_records", "audit_events", "devices"):
            count = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert count == 0 or table == "audit_events" and count == 0, table


def test_importing_twice_changes_nothing(client, tmp_path, monkeypatch):
    _, folder, _ = move(client, tmp_path, monkeypatch)
    transfer.import_content(folder)
    assert set(transfer.import_content(folder).values()) == {0}


def test_export_folder_is_private(client, tmp_path, monkeypatch):
    _, folder, _ = move(client, tmp_path, monkeypatch)
    assert oct(folder.stat().st_mode)[-3:] == "700"
