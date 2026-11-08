"""Operator dashboard, versioned content and box status (ADR-022). Synthetic images only."""
import io
import json
import re
from pathlib import Path
import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from conftest import process
from photobox_client.kiosk import StatusReporter
from photobox_server import admin, dashboard, jobs
from photobox_server.app import app as booth
from photobox_server.consent_notice import load_notice
from photobox_server.core import db_connect, storage_root
from photobox_server.dashboard.security import Sessions, set_password
from photobox_server.library import ContentError, active_prompt_version, prompt_content, save_prompt_content
from photobox_server.pairing import start_pairing
from photobox_server.pool import import_seed
from photobox_server.prompts import PROMPT_VERSION, content_version, default_content, prompts_for, validate_content

PASSWORD = "correct horse battery"
BASE = "http://127.0.0.1:8001"


def jpeg(color="#878787"):
    out = io.BytesIO()
    Image.new("RGB", (120, 160), color).save(out, format="JPEG")
    return out.getvalue()


@pytest.fixture
def anon(anonymous_client):
    dashboard.app.state.sessions = Sessions()
    return TestClient(dashboard.app, base_url=BASE, follow_redirects=False)


@pytest.fixture
def dash(anon):
    with db_connect() as db:
        set_password(db, PASSWORD)
    response = anon.post("/login", data={"password": PASSWORD})
    assert response.status_code == 303
    return anon


def csrf(client, path="/"):
    return re.search(r'name="csrf" value="([^"]+)"', client.get(path).text).group(1)


def post(client, path, data=None, files=None, page="/"):
    return client.post(path, data={"csrf": csrf(client, page), **(data or {})}, files=files)


def complete_strip(client):
    with db_connect() as db:
        import_seed(db, jpeg("#222222"), "SYNTHETIC")
    sid = client.post("/api/v1/sessions").json()["id"]
    client.post(f"/api/v1/sessions/{sid}/consent", json={"processing": True, "documented_externally": True})
    client.post(f"/api/v1/sessions/{sid}/portrait", files={"image": ("x.jpg", jpeg(), "image/jpeg")})
    process(client, sid)
    return sid


# Access

def test_only_this_mac_is_answered(anonymous_client):
    remote = TestClient(dashboard.app, base_url=BASE, client=("192.168.1.50", 50000))
    assert remote.get("/login").status_code == 403


@pytest.mark.parametrize("host", ["attacker.example", "192.168.1.24:8001", "photobox.local"])
def test_other_host_names_are_refused_against_dns_rebinding(anon, host):
    assert anon.get("/login", headers={"Host": host}).status_code == 403


def test_pages_need_a_login(anon):
    for path in ("/", "/queue", "/strips", "/consent", "/stories", "/prompts", "/seeds", "/devices", "/audit"):
        response = anon.get(path)
        assert response.status_code == 303 and response.headers["location"] == "/login"


def test_login_page_explains_how_to_set_a_password(anon):
    assert "dashboard set-password" in anon.get("/login").text


def test_wrong_passwords_are_refused_and_then_locked_out(anon):
    with db_connect() as db:
        set_password(db, PASSWORD)
    codes = [anon.post("/login", data={"password": "wrong password"}).status_code for _ in range(5)]
    assert codes == [401] * 5
    assert anon.post("/login", data={"password": PASSWORD}).status_code == 401
    assert "Try again in" in anon.get("/login").text


def test_session_cookie_is_strict_and_http_only(anon):
    with db_connect() as db:
        set_password(db, PASSWORD)
    cookie = anon.post("/login", data={"password": PASSWORD}).headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie


def test_forms_need_the_session_token(dash):
    assert dash.post("/consent", data={"text": "x" * 20}).status_code == 403
    assert dash.post("/consent", data={"text": "x" * 20, "csrf": "forged"}).status_code == 403


def test_logout_ends_the_session(dash):
    post(dash, "/logout")
    assert dash.get("/").status_code == 303


def test_security_headers(dash):
    headers = dash.get("/").headers
    assert headers["cache-control"] == "no-store" and headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]


def test_password_rules_and_admin_command(anonymous_client, tmp_path, monkeypatch, capsys):
    with db_connect() as db:
        with pytest.raises(ValueError):
            set_password(db, "short")
    monkeypatch.chdir(tmp_path)
    answers = iter([PASSWORD, PASSWORD])
    monkeypatch.setattr("getpass.getpass", lambda _prompt: next(answers))
    assert admin.main(["dashboard", "set-password"]) == 0
    with db_connect() as db:
        stored = db.execute("SELECT hash FROM dashboard_password").fetchone()[0]
    assert PASSWORD not in stored and "127.0.0.1:8001" in capsys.readouterr().out


def test_start_script_binds_to_this_mac_only():
    script = (Path(__file__).parents[1] / "scripts" / "run_dashboard.sh").read_text()
    assert "--host 127.0.0.1" in script and "0.0.0.0" not in script


# Box status

def test_boxes_report_their_state(client, dash):
    assert client.post("/api/v1/devices/status", json={"state": "generating"}).status_code == 204
    page = dash.get("/").text
    assert "pi-01" in page and "online" in page and "generating" in page


def test_status_needs_a_token_and_a_known_state(client, anonymous_client):
    assert anonymous_client.post("/api/v1/devices/status", json={"state": "idle"}, headers={"Authorization": ""}).status_code == 401
    assert client.post("/api/v1/devices/status", json={"state": "hacking"}).status_code == 422
    assert client.post("/api/v1/devices/status", json={"state": "idle", "detail": "x" * 121}).status_code == 422


def test_public_box_name_is_set_and_checked_on_the_boxes_page(client, dash):
    assert post(dash, "/devices/pi-01/label", {"label": "Hall B"}, page="/devices").status_code == 303
    assert 'value="Hall B"' in dash.get("/devices").text
    assert client.get("/api/v1/presence/snapshot").json()["self"]["label"] == "Hall B"
    refused = post(dash, "/devices/pi-01/label", {"label": "<script>"}, page="/devices")
    assert refused.status_code == 303 and "must be" in dash.get(refused.headers["location"]).text
    assert "<script>" not in dash.get("/devices").text
    assert post(dash, "/devices/nobody/label", {"label": "X"}, page="/devices").status_code == 303
    post(dash, "/devices/pi-01/label", {"label": ""}, page="/devices")
    assert client.get("/api/v1/presence/snapshot").json()["self"]["label"] is None


def test_silent_box_shows_offline(client, dash):
    client.post("/api/v1/devices/status", json={"state": "idle"})
    with db_connect() as db:
        db.execute("UPDATE device_status SET reported_at='2020-01-01T00:00:00+00:00'")
    assert "offline" in dash.get("/").text


def test_kiosk_reporter_sends_changes_and_survives_failures():
    import threading
    sent = []
    seen = threading.Event()

    def post(body):
        sent.append(body)
        seen.set()
        if len(sent) == 1:
            raise httpx.ConnectError("down")

    reporter = StatusReporter(post).start()
    assert seen.wait(2)
    seen.clear()
    reporter("generating")
    assert seen.wait(2)
    reporter.stop()
    assert sent[0] == {"state": "idle", "detail": None} and {"state": "generating", "detail": None} in sent


# Queue and strips

def test_queue_shows_jobs_and_can_cancel(client, dash):
    with db_connect() as db:
        import_seed(db, jpeg("#222222"), "SYNTHETIC")
    sid = client.post("/api/v1/sessions").json()["id"]
    client.post(f"/api/v1/sessions/{sid}/consent", json={"processing": True, "documented_externally": True})
    client.post(f"/api/v1/sessions/{sid}/portrait", files={"image": ("x.jpg", jpeg(), "image/jpeg")})
    client.post(f"/api/v1/sessions/{sid}/process")
    assert "queued" in dash.get("/queue").text
    post(dash, f"/queue/{sid}/cancel", page="/queue")
    assert client.get(f"/api/v1/sessions/{sid}").json()["status"] == "uploaded"
    assert jobs.run_pending() == 0


def test_strips_are_listed_viewable_and_withdrawable(client, dash):
    sid = complete_strip(client)
    page = dash.get("/strips").text
    assert f"/strips/{sid}.png" in page and "deleted in" in page
    for profile, mode in (("color", "RGB"), ("thermal", "1")):
        response = dash.get(f"/strips/{sid}.png", params={"profile": profile})
        assert response.status_code == 200 and Image.open(io.BytesIO(response.content)).mode == mode
    post(dash, f"/strips/{sid}/withdraw", page="/strips")
    assert client.get(f"/api/v1/sessions/{sid}").json()["status"] == "complete"
    post(dash, f"/strips/{sid}/withdraw", {"confirm": "yes"}, page="/strips")
    assert client.get(f"/api/v1/sessions/{sid}").json()["status"] == "withdrawn"
    assert dash.get(f"/strips/{sid}.png").status_code == 404


def test_strip_images_need_a_login(client, anon):
    sid = complete_strip(client)
    assert anon.get(f"/strips/{sid}.png").status_code == 303


# Consent notice

def test_consent_notice_is_versioned_and_served_to_boxes(client, dash):
    first = "First notice. ENTER = agree, ESC = cancel."
    second = "Second notice, with more detail. ENTER = agree, ESC = cancel."
    post(dash, "/consent", {"text": first}, page="/consent")
    version = client.get("/api/v1/consent-notice").json()["version"]
    post(dash, "/consent", {"text": second}, page="/consent")
    assert client.get("/api/v1/consent-notice").json()["text"] == second
    page = dash.get("/consent").text
    assert version in page and "active" in page
    post(dash, f"/consent/{version}/activate", page="/consent")
    assert client.get("/api/v1/consent-notice").json() == {"version": version, "text": first, "live_matching": False, "pool": None}
    assert first in dash.get(f"/consent/{version}").text


def test_consent_counts_follow_their_version(client, dash):
    post(dash, "/consent", {"text": "A notice for counting. ENTER = agree."}, page="/consent")
    version = client.get("/api/v1/consent-notice").json()["version"]
    sid = client.post("/api/v1/sessions").json()["id"]
    client.post(f"/api/v1/sessions/{sid}/consent", json={"processing": True, "method": "participant_screen", "notice_version": version})
    assert re.search(rf"{version}.*?<td>1</td>", dash.get("/consent").text, re.S)


@pytest.mark.parametrize("text,reason", [("", "empty"), ("Responsible: TODO-OPERATOR", "TODO-OPERATOR"), ("x" * 4001, "longer")])
def test_unusable_notice_is_refused(client, dash, text, reason):
    response = post(dash, "/consent", {"text": text}, page="/consent")
    assert response.status_code == 422 and reason in response.text


def test_file_notice_is_used_until_the_dashboard_saves_one(dash, tmp_path, monkeypatch):
    path = tmp_path / "notice.txt"
    path.write_text("From the file. ENTER = agree.")
    monkeypatch.setenv("PHOTOBOX_CONSENT_NOTICE_FILE", str(path))
    assert load_notice()[1] == "From the file. ENTER = agree."
    assert "consent-notice.txt" in dash.get("/consent").text
    post(dash, "/consent", {"text": "From the dashboard. ENTER = agree."}, page="/consent")
    assert load_notice()[1] == "From the dashboard. ENTER = agree."


# Stories and prompts

def test_default_content_is_valid_and_matches_the_original_prompts():
    assert validate_content(default_content()) == []
    assert content_version(default_content()) == PROMPT_VERSION
    assert prompts_for(4, 7) == prompts_for(4, 7, default_content())


@pytest.mark.parametrize("change,reason", [
    (lambda c: c.update(scene=c["scene"].replace("{beat}", "")), "{beat}"),
    (lambda c: c.update(scene=c["scene"].replace("eye level", "street level")), "eye level"),
    (lambda c: c["stories"][0]["beats"].__setitem__(1, "{a} gives {b} a big hug."), "hug"),
    (lambda c: c["stories"][0]["beats"].__setitem__(2, "{a} is seen from above, tilted."), "camera"),
    (lambda c: c["stories"][0]["beats"].__setitem__(0, "Only {a} is here."), "both {a} and {b}"),
    (lambda c: c["stories"][0]["beats"].pop(), "four beats"),
    (lambda c: c["stories"][1].update(title=c["stories"][0]["title"]), "unique title"),
    (lambda c: c.update(stagings=c["stagings"][:2]), "three stagings"),
    (lambda c: c["stagings"].__setitem__(0, "{x} stands alone."), "both people"),
    (lambda c: [s.update(enabled=False) for s in c["stories"]], "enabled"),
    (lambda c: c.update(backdrops=[]), "backdrop"),
])
def test_content_rules(change, reason):
    content = default_content()
    change(content)
    errors = validate_content(content)
    assert errors and any(reason in e for e in errors), errors


def test_disabled_stories_are_never_chosen():
    content = default_content()
    for story in content["stories"][1:]:
        story["enabled"] = False
    title_beat = content["stories"][0]["beats"][1].split(" ")[3]
    assert all(title_beat in prompts_for(4, seed, content)[1] for seed in range(30))


def test_story_edits_create_versions_and_queued_jobs_keep_theirs(client, dash):
    with db_connect() as db:
        import_seed(db, jpeg("#222222"), "SYNTHETIC")
    sid = client.post("/api/v1/sessions").json()["id"]
    client.post(f"/api/v1/sessions/{sid}/consent", json={"processing": True, "documented_externally": True})
    client.post(f"/api/v1/sessions/{sid}/portrait", files={"image": ("x.jpg", jpeg(), "image/jpeg")})
    client.post(f"/api/v1/sessions/{sid}/process")
    with db_connect() as db:
        queued_with = db.execute("SELECT prompt_version FROM generation_jobs").fetchone()[0]
    assert queued_with == PROMPT_VERSION
    edited = {"title": "pigeon", "beat1": "{a} and {b} wait for a bus.", "beat2": "{a} hums.", "beat3": "{b} hums too.", "beat4": "Both sing.", "enabled": "yes"}
    post(dash, "/stories/0", edited, page="/stories/0")
    with db_connect() as db:
        assert active_prompt_version(db) != PROMPT_VERSION
    seen = []

    class Recording:
        name, strip_footer, max_faces = "rec", "REC", None

        def generate_frame(self, request, should_cancel):
            seen.append(request.prompt)
            Image.linear_gradient("L").resize((768, 1024)).save(request.output, "PNG")
            return jobs.FrameMetrics(seconds=1)

    jobs.run_pending(Recording())
    assert not any("wait for a bus" in p for p in seen)


def test_unsafe_story_is_refused_and_nothing_changes(dash):
    before = dash.get("/stories").text
    bad = {"title": "trouble", "beat1": "{a} and {b} meet.", "beat2": "{a} pulls a knife.", "beat3": "{b} runs.", "beat4": "Both laugh.", "enabled": "yes"}
    response = post(dash, "/stories/new", bad, page="/stories/new")
    assert response.status_code == 422 and "knife" in response.text
    with db_connect() as db:
        assert active_prompt_version(db) == PROMPT_VERSION


def test_new_story_toggle_delete_and_reset(dash):
    story = {"title": "tea party", "beat1": "{a} and {b} sit at a tiny table.", "beat2": "{a} pours tea for {b}.", "beat3": "The teapot keeps pouring over both of them.", "beat4": "Both are knee deep in tea, laughing.", "enabled": "yes"}
    post(dash, "/stories/new", story, page="/stories/new")
    with db_connect() as db:
        content = prompt_content(db)
    assert content["stories"][-1]["title"] == "tea party" and len(content["stories"]) == 101
    index = len(content["stories"]) - 1
    post(dash, f"/stories/{index}/toggle", page="/stories")
    with db_connect() as db:
        assert prompt_content(db)["stories"][index]["enabled"] is False
    post(dash, f"/stories/{index}", {"delete": "yes"}, page=f"/stories/{index}")
    with db_connect() as db:
        assert len(prompt_content(db)["stories"]) == 100
    post(dash, f"/stories/versions/{PROMPT_VERSION}/activate", page="/stories")
    with db_connect() as db:
        assert active_prompt_version(db) == PROMPT_VERSION


def test_story_page_shows_example_prompts(dash):
    page = dash.get("/stories/0").text
    assert "Example prompts" in page and "the person from Image 1" in page


def test_prompt_parts_can_be_edited(dash):
    with db_connect() as db:
        content = prompt_content(db)
    data = {"scene": content["scene"], "clear_faces": "Both faces are clearly visible.", "backdrops": "mint green\nlavender",
            "opening_stagings": "\n".join(content["opening_stagings"]), "stagings": "\n".join(content["stagings"])}
    post(dash, "/prompts", data, page="/prompts")
    with db_connect() as db:
        assert prompt_content(db)["backdrops"] == ["mint green", "lavender"]
    data["scene"] = "No placeholders at all"
    assert post(dash, "/prompts", data, page="/prompts").status_code == 422


def test_saving_unchanged_content_reuses_its_version(anonymous_client):
    with db_connect() as db:
        first = save_prompt_content(db, default_content())
        second = save_prompt_content(db, default_content())
        assert first == second == PROMPT_VERSION
        assert db.execute("SELECT COUNT(*) FROM prompt_sets").fetchone()[0] == 1
        with pytest.raises(ContentError):
            save_prompt_content(db, {"scene": "x"})


# Seeds, boxes, audit

def test_seed_upload_needs_the_rights_confirmation(dash):
    files = {"image": ("seed.jpg", jpeg(), "image/jpeg")}
    post(dash, "/seeds", {"rights_reference": "FICTIONAL-AI"}, files=files, page="/seeds")
    with db_connect() as db:
        assert db.execute("SELECT COUNT(*) FROM seeds").fetchone()[0] == 0
    post(dash, "/seeds", {"rights_reference": "FICTIONAL-AI", "attest": "yes"}, files={"image": ("seed.jpg", jpeg(), "image/jpeg")}, page="/seeds")
    with db_connect() as db:
        seed_id = db.execute("SELECT id FROM seeds").fetchone()[0]
    assert dash.get(f"/seeds/{seed_id}.jpg").headers["content-type"] == "image/jpeg"
    post(dash, f"/seeds/{seed_id}/remove", page="/seeds")
    with db_connect() as db:
        assert db.execute("SELECT COUNT(*) FROM seeds WHERE removed_at IS NULL").fetchone()[0] == 0


def test_bad_seed_is_explained(dash):
    response = dash.post("/seeds", data={"csrf": csrf(dash, "/seeds"), "rights_reference": "X", "attest": "yes"}, files={"image": ("x.jpg", b"not an image", "image/jpeg")})
    assert "Not imported" in dash.get(response.headers["location"]).text


def test_pairing_code_from_the_dashboard_works(client, dash, anonymous_client):
    page = post(dash, "/devices/pair", {"device_id": "pi-02"}, page="/devices").text
    digits = re.search(r'class="code mono">(\d{3}) (\d{3})<', page)
    code = digits.group(1) + digits.group(2)
    paired = anonymous_client.post("/api/v1/pair", json={"code": code})
    assert paired.status_code == 200 and paired.json()["device_id"] == "pi-02"


def test_box_can_be_revoked(client, dash):
    post(dash, "/devices/pi-01/revoke", page="/devices")
    assert client.post("/api/v1/sessions").status_code == 401


def test_audit_page_filters_events(client, dash):
    complete_strip(client)
    assert "match_used" in dash.get("/audit").text
    filtered = dash.get("/audit", params={"event": "seed_imported"}).text
    assert "seed_imported" in filtered and "<td>match_used</td>" not in filtered
    assert "dashboard_login" in dash.get("/audit").text
