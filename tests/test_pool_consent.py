"""Timed pool consent at the kiosk (ADR-024, milestone P-1). Synthetic images only."""
import io
from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from conftest import process
from photobox_client.kiosk import ENTER, ESC, Api, Kiosk
from photobox_server import dashboard
from photobox_server.core import db_connect
from photobox_server.consent_notice import load_pool_notice, pool_until
from photobox_server.dashboard.security import Sessions, set_password
from photobox_server.library import active_notice, save_notice
from photobox_server.pool import import_seed, select_match
from photobox_server.presence import snapshot
from photobox_server.withdrawal import remove_from_pool, session_for_code, withdraw_session

NOTICE = "FOTOBOX NOTICE\nENTER = agree, ESC = cancel"
POOL = "POOL? For {hours} hours, until {until}.\nENTER = yes, ESC = no"
PASSWORD = "correct horse battery"


def jpeg(color="#878787"):
    out = io.BytesIO()
    Image.new("RGB", (120, 160), color).save(out, format="JPEG")
    return out.getvalue()


@pytest.fixture
def notices(tmp_path, monkeypatch):
    (tmp_path / "notice.txt").write_text(NOTICE)
    (tmp_path / "pool.txt").write_text(POOL)
    monkeypatch.setenv("PHOTOBOX_CONSENT_NOTICE_FILE", str(tmp_path / "notice.txt"))
    monkeypatch.setenv("PHOTOBOX_POOL_NOTICE_FILE", str(tmp_path / "pool.txt"))
    return tmp_path / "pool.txt"


def served(client):
    return client.get("/api/v1/consent-notice").json()


def consent(client, pool=True, **change):
    body = served(client)
    sid = client.post("/api/v1/sessions").json()["id"]
    request = {"processing": True, "method": "participant_screen", "notice_version": body["version"], "pool": pool}
    if pool and body["pool"]:
        request |= {"pool_notice_version": body["pool"]["version"], "pool_until": body["pool"]["until"]}
    request |= change
    return sid, client.post(f"/api/v1/sessions/{sid}/consent", json=request)


def upload(client, sid):
    assert client.post(f"/api/v1/sessions/{sid}/portrait", files={"image": ("x.jpg", jpeg(), "image/jpeg")}).status_code == 200


def pool_row(sid):
    with db_connect() as db:
        return db.execute("SELECT * FROM portraits WHERE session_id=? AND pool_eligible=1", (sid,)).fetchone()


# The notice

def test_no_pool_question_without_a_pool_notice(client, notices):
    notices.unlink()
    assert served(client)["pool"] is None
    sid, response = consent(client, pool=False)
    assert response.status_code == 200
    _, response = consent(client, pool=True, pool_notice_version="pool-x-24h", pool_until=datetime.now(timezone.utc).isoformat())
    assert response.status_code == 403


def test_pool_question_states_the_hours_and_the_end_time(client, notices):
    pool = served(client)["pool"]
    assert pool["version"].startswith("pool-") and pool["version"].endswith("-24h")
    assert "For 24 hours, until " + pool["until_text"] in pool["text"] and "{" not in pool["text"]
    until = datetime.fromisoformat(pool["until"])
    assert until.second == 0 and timedelta(hours=23, minutes=58) < until - datetime.now(timezone.utc) <= timedelta(hours=24)


def test_end_time_is_rounded_down_never_up():
    now = datetime(2026, 11, 8, 12, 0, 59, 999, tzinfo=timezone.utc)
    assert pool_until(now, 24) == datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("text", ["POOL? no end time", "POOL until {until}. Responsible: TODO-OPERATOR", ""])
def test_unusable_pool_notice_is_not_offered(client, notices, text):
    notices.write_text(text)
    assert served(client)["pool"] is None and load_pool_notice() is None


def test_hours_are_part_of_the_version(client, notices, monkeypatch):
    first = served(client)["pool"]["version"]
    monkeypatch.setenv("PHOTOBOX_POOL_HOURS", "2")
    assert served(client)["pool"]["version"] == first.replace("-24h", "-2h")


# The consent

def test_pool_consent_is_its_own_record_with_its_version_and_end_time(client, notices):
    pool = served(client)["pool"]
    sid, response = consent(client)
    assert response.status_code == 200 and response.json()["pool_consent"] is True
    with db_connect() as db:
        records = {row["purpose"]: row for row in db.execute("SELECT * FROM consent_records WHERE session_id=?", (sid,))}
    assert records["processing"]["policy_version"] == served(client)["version"] and records["processing"]["expires_at"] is None
    assert records["pool"]["policy_version"] == pool["version"] and records["pool"]["provenance"] == "participant_screen"
    assert datetime.fromisoformat(records["pool"]["expires_at"]) == datetime.fromisoformat(pool["until"])


def test_pool_copy_leaves_at_the_time_shown_and_never_later(client, notices):
    shown = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(second=0, microsecond=0)
    sid, response = consent(client, pool_until=shown.isoformat())
    assert response.status_code == 200
    upload(client, sid)
    assert datetime.fromisoformat(pool_row(sid)["expires_at"]) == shown
    own = [row for row in db_connect_rows("SELECT expires_at FROM portraits WHERE session_id=? AND pool_eligible=0", sid)]
    assert datetime.fromisoformat(own[0]["expires_at"]) < shown  # the session copy keeps its one hour


def db_connect_rows(query, *args):
    with db_connect() as db:
        return db.execute(query, args).fetchall()


def test_no_pool_copy_when_the_visitor_says_no(client, notices):
    sid, response = consent(client, pool=False)
    assert response.status_code == 200 and response.json()["pool_consent"] is False
    upload(client, sid)
    assert pool_row(sid) is None


@pytest.mark.parametrize("change,code", [
    ({"pool_notice_version": "pool-000000000000-24h"}, 409),
    ({"pool_until": None}, 422),
    ({"pool_until": "2026-11-08T12:00:00"}, 422),  # no time zone
    ({"pool_until": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()}, 422),
    ({"pool_until": (datetime.now(timezone.utc) + timedelta(hours=25)).isoformat()}, 422),
])
def test_pool_consent_needs_the_current_notice_and_a_valid_end_time(client, notices, change, code):
    sid, response = consent(client, **change)
    assert response.status_code == code
    with db_connect() as db:
        assert db.execute("SELECT COUNT(*) FROM consent_records WHERE session_id=?", (sid,)).fetchone()[0] == 0


def test_pooled_photo_is_matched_and_announced(client, notices):
    sid, _ = consent(client)
    upload(client, sid)
    other, _ = consent(client, pool=False)
    with db_connect() as db:
        assert select_match(db, other)[0] == pool_row(sid)["id"]
        assert snapshot(db, "pi-01")["earlier_portrait_available"] is True


def test_withdrawal_code_also_removes_the_pool_copy(client, notices):
    sid, response = consent(client)
    upload(client, sid)
    with db_connect() as db:
        withdraw_session(db, session_for_code(db, response.json()["withdrawal_code"]))
        assert snapshot(db, "pi-01")["earlier_portrait_available"] is False
    assert pool_row(sid) is None


def test_remove_from_pool_keeps_the_visitors_own_strip(client, notices):
    with db_connect() as db:
        import_seed(db, jpeg("#222222"), "SYNTHETIC")
    pooled, _ = consent(client)
    upload(client, pooled)
    assert process(client, pooled).status_code == 202  # matched with the seed: its own copy is never used
    other, _ = consent(client, pool=False)
    upload(client, other)
    process(client, other)
    with db_connect() as db:
        assert db.execute("SELECT match_portrait_id FROM sessions WHERE id=?", (other,)).fetchone()[0] == pool_row(pooled)["id"]
        assert remove_from_pool(db, pooled) == 1
        assert remove_from_pool(db, "unknown") is None
        record = db.execute("SELECT withdrawn_at FROM consent_records WHERE session_id=? AND purpose='pool'", (pooled,)).fetchone()
    assert record["withdrawn_at"] is not None and pool_row(pooled) is None
    assert client.get(f"/api/v1/sessions/{pooled}/result").status_code == 200
    assert client.get(f"/api/v1/sessions/{other}/result").status_code == 404


# The kiosk

class Screen:
    def __init__(self):
        self.shown = []

    def show(self, text):
        self.shown.append(text)


class Keys:
    def __init__(self, *answers):
        self.answers = list(answers)

    def flush(self):
        pass

    def wait(self, accepted, timeout):
        return self.answers.pop(0) if self.answers else None


class FakeApi:
    POOL = {"version": "pool-abc-24h", "text": "POOL QUESTION", "until": "2026-11-09T12:00:00+00:00", "until_text": "09.11. 13:00"}

    def __init__(self, pool=POOL):
        self.pool, self.started = pool, []

    def presence(self):
        return None

    def notice(self):
        return "notice-1", "NOTICE", self.pool

    def start(self, version, pool=None):
        self.started.append(pool)
        return "sid", "CODE"

    def upload_and_process(self, sid, image):
        pass

    def status(self, sid):
        return {"status": "complete", "job": None}


def run(api, *answers):
    screen = Screen()
    kiosk = Kiosk(api, lambda: b"jpeg", screen, Keys(*answers), sleep=lambda _s: None)
    return kiosk.run_once(), screen.shown


def test_kiosk_asks_the_pool_question_separately_and_records_yes():
    api = FakeApi()
    ending, shown = run(api, ENTER, ENTER, ENTER, ENTER)
    assert ending == "complete" and api.started == [FakeApi.POOL]
    assert shown.index("NOTICE") < shown.index("POOL QUESTION")
    assert "bis 09.11. 13:00 im Pool" in shown[-1] and "until 09.11. 13:00" in shown[-1]


def test_kiosk_takes_the_photo_without_pool_on_escape():
    api = FakeApi()
    ending, shown = run(api, ENTER, ENTER, ESC, ENTER)
    assert ending == "complete" and api.started == [None] and "Pool" not in shown[-1]


def test_kiosk_takes_no_photo_when_the_pool_question_goes_unanswered():
    api = FakeApi()
    ending, _ = run(api, ENTER, ENTER, None)
    assert ending == "declined" and api.started == []


def test_kiosk_without_pool_question_asks_once():
    api = FakeApi(pool=None)
    ending, shown = run(api, ENTER, ENTER, ENTER)
    assert ending == "complete" and api.started == [None] and not any("POOL" in text for text in shown)


def test_kiosk_api_sends_the_pool_answer_to_the_server(client, notices):
    api = Api(client)
    version, _, pool = api.notice()
    sid, code = api.start(version, pool)
    assert code
    upload(client, sid)
    assert pool_row(sid) is not None
    sid, _ = api.start(version, None)
    upload(client, sid)
    assert pool_row(sid) is None


# The dashboard

@pytest.fixture
def dash(anonymous_client):
    dashboard.app.state.sessions = Sessions()
    browser = TestClient(dashboard.app, base_url="http://127.0.0.1:8001", follow_redirects=False)
    with db_connect() as db:
        set_password(db, PASSWORD)
    browser.post("/login", data={"password": PASSWORD})
    return browser


def csrf(browser, path):
    import re
    return re.search(r'name="csrf" value="([^"]+)"', browser.get(path).text).group(1)


def test_dashboard_lists_pool_photos_without_images_and_removes_them(client, notices, dash):
    sid, _ = consent(client)
    upload(client, sid)
    page = dash.get("/pool").text
    assert "pi-01" in page and " h " in page and "<img" not in page
    response = dash.post(f"/pool/{sid}/remove", data={"csrf": csrf(dash, "/pool")})
    assert response.status_code == 303 and pool_row(sid) is None
    assert "The pool is empty" in dash.get("/pool").text


def test_dashboard_pool_notice_needs_an_end_time_and_keeps_the_kinds_apart(client, notices, dash):
    refused = dash.post("/pool", data={"csrf": csrf(dash, "/pool"), "text": "POOL? yes or no"})
    assert refused.status_code == 422 and "{until}" in refused.text
    assert dash.post("/pool", data={"csrf": csrf(dash, "/pool"), "text": "NEW POOL until {until}"}).status_code == 303
    assert served(client)["pool"]["text"].startswith("NEW POOL until ")
    with db_connect() as db:
        save_notice(db, "PROCESSING V2")
        assert active_notice(db, "pool")[1] == "NEW POOL until {until}"
        assert active_notice(db)[1] == "PROCESSING V2"
    assert served(client)["text"] == "PROCESSING V2"


def test_dashboard_warns_when_the_pool_question_is_off(client, notices, dash):
    notices.unlink()
    assert "do not ask" in dash.get("/pool").text


def test_dashboard_can_stop_the_pool_question(client, notices, dash):
    dash.post("/pool", data={"csrf": csrf(dash, "/pool"), "text": "DASH POOL until {until}"})
    response = dash.post("/pool/off", data={"csrf": csrf(dash, "/pool")})
    assert "still asked" in dash.get(response.headers["location"]).text
    assert served(client)["pool"]["text"].startswith("POOL?")  # the file is still there
    notices.unlink()
    dash.post("/pool/off", data={"csrf": csrf(dash, "/pool")})
    assert served(client)["pool"] is None
