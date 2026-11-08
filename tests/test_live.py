"""Live matching across boxes (ADR-024, milestone L-1). Synthetic images only.

Two boxes: `pi-01` and `box-berlin`, as a second kiosk on the Mac would be.
"""
import io
import threading
from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from photobox_client.kiosk import Api, encounter_text
from photobox_server import jobs, live, presence
from photobox_server.app import app
from photobox_server.auth import create_device
from photobox_server.core import db_connect
from photobox_server.pool import import_seed
from photobox_server.providers.base import GenerationError
from photobox_server.providers.mock import MockProvider
from photobox_server.withdrawal import withdraw_session


def jpeg(color):
    out = io.BytesIO()
    Image.new("RGB", (120, 160), color).save(out, format="JPEG")
    return out.getvalue()


def box_client(name, label=None):
    with db_connect() as db:
        token = create_device(db, name)
        if label:
            presence.set_label(db, name, label)
    other = TestClient(app)
    other.headers["Authorization"] = f"Bearer {token}"
    return other


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("PHOTOBOX_LIVE_MATCHING", "true")


@pytest.fixture
def boxes(client, on):
    with db_connect() as db:
        import_seed(db, jpeg("#222222"), "SYNTHETIC")
    berlin = box_client("box-berlin", "Berlin")
    for c in (client, berlin):
        assert c.post("/api/v1/devices/status", json={"state": "capturing"}).status_code == 204
    return client, berlin


def visit(c, color="#878787", live_consent=True):
    sid = c.post("/api/v1/sessions").json()["id"]
    body = {"processing": True, "documented_externally": True, "live": live_consent}
    assert c.post(f"/api/v1/sessions/{sid}/consent", json=body).status_code == 200
    assert c.post(f"/api/v1/sessions/{sid}/portrait", files={"image": ("x.jpg", jpeg(color), "image/jpeg")}).status_code == 200
    assert c.post(f"/api/v1/sessions/{sid}/process").status_code == 202
    return sid


def status(c, sid):
    return c.get(f"/api/v1/sessions/{sid}").json()


def row(sid):
    with db_connect() as db:
        return db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()


def run():
    return jobs.run_pending(MockProvider())


def pass_deadline():
    with db_connect() as db:
        db.execute("UPDATE generation_jobs SET next_attempt_at=? WHERE status='queued'", ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))


# Switching it on

def test_off_by_default(client, monkeypatch):
    assert client.get("/api/v1/consent-notice").status_code in (200, 503)
    sid = client.post("/api/v1/sessions").json()["id"]
    response = client.post(f"/api/v1/sessions/{sid}/consent", json={"processing": True, "documented_externally": True, "live": True})
    assert response.status_code == 403 and "live matching" in response.json()["detail"]


def test_live_consent_is_its_own_record(boxes):
    client, _ = boxes
    sid = visit(client)
    with db_connect() as db:
        purposes = {r["purpose"] for r in db.execute("SELECT purpose FROM consent_records WHERE session_id=?", (sid,))}
    assert purposes == {"processing", "live"}


def test_notice_tells_the_box_whether_live_matching_is_on(boxes, tmp_path, monkeypatch):
    client, _ = boxes
    (tmp_path / "n.txt").write_text("NOTICE: you may be paired live with someone at another box.")
    monkeypatch.setenv("PHOTOBOX_CONSENT_NOTICE_FILE", str(tmp_path / "n.txt"))
    api = Api(client)
    version, _, _ = api.notice()
    assert api.live_matching is True
    sid, _ = api.start(version)
    assert live.consented(db_connect().__enter__(), sid)


# Waiting and pairing

def test_first_visitor_waits_for_someone_at_another_box(boxes):
    client, _ = boxes
    sid = visit(client)
    assert status(client, sid)["job"]["waiting_for_partner"] is True
    assert run() == 0  # nothing starts during the wait


def test_visitors_at_two_boxes_share_one_identical_strip(boxes):
    client, berlin = boxes
    first = visit(client, "#aa0000")
    second = visit(berlin, "#0000aa")
    lead, follow = row(first), row(second)
    assert (lead["live_role"], lead["live_partner"], lead["match_portrait_id"]) == ("lead", second, follow["portrait_id"])
    assert (follow["live_role"], follow["live_partner"], follow["match_portrait_id"]) == ("follow", first, lead["portrait_id"])
    assert status(berlin, second)["match"] == {"kind": "live", "partner_label": None}
    assert status(client, first)["match"] == {"kind": "live", "partner_label": "Berlin"}
    assert run() == 1  # one job makes the strip for both
    assert status(client, first)["status"] == status(berlin, second)["status"] == "complete"
    assert row(first)["output_path"] == row(second)["output_path"]
    assert client.get(f"/api/v1/sessions/{first}/result").content == berlin.get(f"/api/v1/sessions/{second}/result").content


def test_follower_reports_the_leaders_progress(boxes):
    client, berlin = boxes
    visit(client)
    second = visit(berlin)
    body = status(berlin, second)
    assert body["status"] == "queued" and body["job"]["waiting_for_partner"] is False and body["job"]["frames_total"] == 4
    assert berlin.post(f"/api/v1/sessions/{second}/process").status_code == 202  # repeating is harmless


def test_a_box_is_never_paired_with_itself(boxes):
    client, _ = boxes
    first = visit(client)
    second = visit(client)
    assert row(first)["live_partner"] is None and row(second)["live_partner"] is None


def test_no_wait_without_another_box_online(client, on):
    with db_connect() as db:
        import_seed(db, jpeg("#222222"), "SYNTHETIC")
    sid = visit(client)
    assert status(client, sid)["job"]["waiting_for_partner"] is False and run() == 1


def test_no_wait_and_no_pairing_without_live_consent(boxes):
    client, berlin = boxes
    first = visit(client, live_consent=False)
    assert status(client, first)["job"]["waiting_for_partner"] is False
    waiting = visit(berlin)
    other = visit(client, live_consent=False)
    assert row(waiting)["live_partner"] is None and row(other)["live_partner"] is None


def test_after_the_wait_the_strip_uses_a_seed_and_late_arrivals_wait_anew(boxes):
    client, berlin = boxes
    first = visit(client)
    pass_deadline()
    assert run() == 1 and status(client, first)["match"]["kind"] == "seed"
    late = visit(berlin)
    assert row(late)["live_partner"] is None and status(berlin, late)["job"]["waiting_for_partner"] is True


def test_two_newcomers_at_once_pair_only_one(boxes):
    client, berlin = boxes
    waiting = visit(client)
    others = [box_client(f"box-{n}") for n in range(3)]
    sessions = []
    for c in others:
        c.post("/api/v1/devices/status", json={"state": "capturing"})
        sid = c.post("/api/v1/sessions").json()["id"]
        c.post(f"/api/v1/sessions/{sid}/consent", json={"processing": True, "documented_externally": True, "live": True})
        c.post(f"/api/v1/sessions/{sid}/portrait", files={"image": ("x.jpg", jpeg("#00aa00"), "image/jpeg")})
        sessions.append((c, sid))
    threads = [threading.Thread(target=lambda c=c, sid=sid: c.post(f"/api/v1/sessions/{sid}/process")) for c, sid in sessions]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    paired = [sid for _, sid in sessions if row(sid)["live_partner"] == waiting]
    assert len(paired) == 1 and row(waiting)["live_partner"] == paired[0]


# Leaving a pair

def test_withdrawn_follower_leaves_the_leader_with_a_seed(boxes):
    client, berlin = boxes
    first, second = visit(client), visit(berlin)
    with db_connect() as db:
        withdraw_session(db, second)
    assert row(first)["live_partner"] is None and row(first)["match_portrait_id"] is None
    run()
    assert status(client, first)["status"] == "complete" and status(client, first)["match"]["kind"] == "seed"


def test_withdrawn_leader_leaves_the_follower_its_own_strip(boxes):
    client, berlin = boxes
    first, second = visit(client), visit(berlin)
    with db_connect() as db:
        withdraw_session(db, first)
    assert row(second)["live_partner"] is None and row(second)["status"] == "queued"
    run()
    assert status(berlin, second)["status"] == "complete" and status(berlin, second)["match"]["kind"] == "seed"


@pytest.mark.parametrize("who", ["leader", "follower"])
def test_cancelling_either_side_breaks_the_pair(boxes, who):
    client, berlin = boxes
    first, second = visit(client), visit(berlin)
    cancel_client, cancelled, other_client, other = (client, first, berlin, second) if who == "leader" else (berlin, second, client, first)
    assert cancel_client.post(f"/api/v1/sessions/{cancelled}/cancel").json()["cancelled"] is True
    assert row(first)["live_partner"] is None and row(second)["live_partner"] is None
    run()
    assert status(other_client, other)["status"] == "complete" and status(other_client, other)["match"]["kind"] == "seed"
    assert status(cancel_client, cancelled)["status"] == "uploaded"


def test_withdrawal_after_the_strip_deletes_it_for_both(boxes):
    client, berlin = boxes
    first, second = visit(client), visit(berlin)
    run()
    with db_connect() as db:
        withdraw_session(db, second)
    assert client.get(f"/api/v1/sessions/{first}/result").status_code == 404
    assert row(first)["output_path"] is None


class Broken(MockProvider):
    def generate_frame(self, request, should_cancel):
        raise GenerationError("broken on purpose")


def test_failed_strip_fails_for_both_and_breaks_the_pair(boxes, monkeypatch):
    monkeypatch.setenv("PHOTOBOX_GENERATION_MAX_ATTEMPTS", "1")
    client, berlin = boxes
    first, second = visit(client), visit(berlin)
    jobs.run_pending(Broken())
    assert status(client, first)["status"] == status(berlin, second)["status"] == "failed"
    assert row(first)["live_partner"] is None and row(first)["match_portrait_id"] is None
    assert berlin.post(f"/api/v1/sessions/{second}/process").status_code == 202  # it can try again on its own


# What the boxes show

def test_both_boxes_are_told_they_are_connected(boxes):
    client, berlin = boxes
    first, second = visit(client), visit(berlin)
    for c in (client, berlin):
        c.post("/api/v1/devices/status", json={"state": "generating"})
    mine = client.get("/api/v1/presence/snapshot").json()["message"]
    theirs = berlin.get("/api/v1/presence/snapshot").json()["message"]
    assert mine["key"] == "live_match" and mine["texts"]["en"]["hero"] == "You're connected with Berlin!"
    assert theirs["key"] == "live_match" and theirs["texts"]["en"]["hero"] == "You're connected with another Photobox!"
    with db_connect() as db:
        kinds = [r["type"] for r in db.execute("SELECT type FROM presence_events WHERE type LIKE 'match.%'")]
    assert kinds == ["match.live_created", "match.live_created"]


def test_kiosk_explains_the_encounter():
    assert "Looking for someone" in encounter_text({"job": {"waiting_for_partner": True}})
    assert "someone in Berlin" in encounter_text({"job": {}, "match": {"kind": "live", "partner_label": "Berlin"}})
    assert "at another photo booth" in encounter_text({"job": {}, "match": {"kind": "live", "partner_label": None}})
    assert "earlier" in encounter_text({"job": {}, "match": {"kind": "pool"}})
    assert encounter_text({"job": {}, "match": {"kind": "seed"}}) == "" and encounter_text({}) == ""


def test_consent_records_accept_live_and_still_refuse_other_purposes(client):
    import sqlite3
    with db_connect() as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO consent_records(id,session_id,purpose,policy_version,granted_at,provenance) VALUES('x','s','gallery','v','t','p')")


# Waiting for someone who is on the way (milestone L-2)

def agree(c, live_consent=True):
    sid = c.post("/api/v1/sessions").json()["id"]
    assert c.post(f"/api/v1/sessions/{sid}/consent", json={"processing": True, "documented_externally": True, "live": live_consent}).status_code == 200
    return sid


def finish_visit(c, sid):
    assert c.post(f"/api/v1/sessions/{sid}/portrait", files={"image": ("x.jpg", jpeg("#00aa00"), "image/jpeg")}).status_code == 200
    assert c.post(f"/api/v1/sessions/{sid}/process").status_code == 202


def wait_left(c, sid):
    return status(c, sid)["job"]["wait_seconds_left"]


def test_wait_extends_when_a_visitor_at_another_box_agrees(boxes):
    client, berlin = boxes
    first = visit(client)
    assert 15 <= wait_left(client, first) <= 21 and status(client, first)["job"]["partner_coming"] is False
    agree(berlin)
    job = status(client, first)["job"]
    assert 50 <= job["wait_seconds_left"] <= 61 and job["partner_coming"] is True and job["partner_coming_label"] == "Berlin"


def test_wait_does_not_extend_without_live_consent_or_from_the_same_box(boxes):
    client, berlin = boxes
    first = visit(client)
    agree(berlin, live_consent=False)
    agree(client)  # the same box can never be the partner
    assert wait_left(client, first) <= 21 and status(client, first)["job"]["partner_coming"] is False


def test_visitor_already_on_the_way_means_a_long_wait_from_the_start(boxes):
    client, berlin = boxes
    agree(berlin)
    first = visit(client)
    assert 50 <= wait_left(client, first) <= 61


def test_someone_who_agreed_long_ago_is_not_waited_for(boxes):
    client, berlin = boxes
    agree(berlin)
    with db_connect() as db:
        db.execute("UPDATE sessions SET created_at=?", ((datetime.now(timezone.utc) - timedelta(seconds=61)).isoformat(),))
    first = visit(client)
    assert wait_left(client, first) <= 21


def test_late_photo_within_the_extended_wait_still_pairs(boxes):
    client, berlin = boxes
    first = visit(client)
    coming = agree(berlin)
    with db_connect() as db:  # 30 s later: past the 20 s, still within the extended wait
        db.execute("UPDATE generation_jobs SET created_at=?", ((datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat(),))
    finish_visit(berlin, coming)
    assert row(first)["live_partner"] == coming


def test_maximum_wait_can_be_configured(boxes, monkeypatch):
    client, berlin = boxes
    monkeypatch.setenv("PHOTOBOX_LIVE_MAX_WAIT_SECONDS", "30")
    first = visit(client)
    agree(berlin)
    assert 25 <= wait_left(client, first) <= 31
    assert live.max_wait_seconds() == 30
    monkeypatch.setenv("PHOTOBOX_LIVE_WAIT_SECONDS", "40")
    assert live.max_wait_seconds() == 40  # never shorter than the base wait


# What other boxes are told

def test_other_boxes_learn_that_someone_is_waiting(boxes):
    client, berlin = boxes
    first = visit(berlin)
    body = client.get("/api/v1/presence/snapshot").json()
    assert body["message"]["key"] == "partner_waiting" and body["message"]["badge"]["en"] == "LIVE"
    assert body["message"]["texts"]["en"]["hero"] == "Someone in Berlin is waiting for a shared photo."
    assert body["locations"][0]["match_available"] is True
    # The waiting box is not told about itself.
    assert berlin.get("/api/v1/presence/snapshot").json()["message"]["key"] != "partner_waiting"
    visit(client)  # paired: nobody is waiting any more
    assert client.get("/api/v1/presence/snapshot").json()["locations"][0]["match_available"] is False
    assert row(first)["live_partner"] is not None


def test_a_passed_deadline_is_no_longer_announced(boxes):
    client, berlin = boxes
    visit(berlin)
    pass_deadline()
    assert client.get("/api/v1/presence/snapshot").json()["locations"][0]["match_available"] is False


def test_nothing_is_announced_when_live_matching_is_off(boxes, monkeypatch):
    client, berlin = boxes
    visit(berlin)
    monkeypatch.setenv("PHOTOBOX_LIVE_MATCHING", "false")
    assert client.get("/api/v1/presence/snapshot").json()["locations"][0]["match_available"] is False


# The kiosk's screens

def test_kiosk_shows_the_real_time_left_and_who_is_coming():
    searching = encounter_text({"job": {"waiting_for_partner": True, "wait_seconds_left": 14}})
    assert "14 s left" in searching and "Looking for someone" in searching
    coming = encounter_text({"job": {"waiting_for_partner": True, "wait_seconds_left": 40, "partner_coming": True, "partner_coming_label": "Berlin"}})
    assert "Someone in Berlin is taking a photo too" in coming and "40 s left" not in coming.splitlines()[1]


def test_kiosk_says_when_nobody_came():
    state = {"job": {}, "match": {"kind": "seed"}}
    assert "Nobody was at another photo booth" in encounter_text(state, waited=True)
    assert encounter_text(state, waited=False) == ""


def test_kiosk_announces_the_pairing_once_then_shows_shared_progress():
    from photobox_client.kiosk import ENTER, Kiosk

    class Screen:
        shown = []

        def show(self, text):
            self.shown.append(text)

    class Keys:
        def flush(self):
            pass

        def wait(self, accepted, timeout):
            return ENTER

    class FakeApi:
        states = [
            {"status": "queued", "job": {"waiting_for_partner": True, "wait_seconds_left": 9}, "match": {"kind": None}},
            {"status": "queued", "job": {}, "match": {"kind": "live", "partner_label": "Berlin"}},
            {"status": "processing", "job": {"frames_done": 1, "frames_total": 4}, "match": {"kind": "live", "partner_label": "Berlin"}},
            {"status": "complete", "job": {}, "match": {"kind": "live", "partner_label": "Berlin"}},
        ]

        def status(self, sid):
            return self.states.pop(0)

    slept = []
    clock = iter(range(1000))
    kiosk = Kiosk(FakeApi(), lambda: b"", Screen(), Keys(), sleep=slept.append, clock=lambda: next(clock))
    assert kiosk.follow("sid", "CODE") == "complete"
    moments = [text for text in Screen.shown if "L I V E" in text]
    assert len(moments) == 1 and "someone in Berlin" in moments[0] and 3 in slept
    assert any("LIVE with someone in Berlin. You both see the same progress." in text for text in Screen.shown)
