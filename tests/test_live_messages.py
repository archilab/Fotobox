"""Messages between real live pairs (ADR-027, milestone M-VOICE-2). Synthetic images only; no audio."""
import io
import re
from datetime import datetime, timedelta, timezone
import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from photobox_client.conversation import LOST, Conversation
from photobox_client.kiosk import ENTER, Kiosk
from photobox_server import dashboard, jobs, message_windows, presence
from photobox_server.app import app
from photobox_server.auth import create_device
from photobox_server.core import db_connect
from photobox_server.dashboard.security import Sessions, set_password
from photobox_server.message_filter import DEFAULT_WORDS, is_blocked, load_words, save_words
from photobox_server.pool import import_seed
from photobox_server.providers.base import GenerationError
from photobox_server.providers.mock import MockProvider
from photobox_server.withdrawal import withdraw_session


def jpeg(color="#878787"):
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
    other.post("/api/v1/devices/status", json={"state": "generating"})
    return other


@pytest.fixture
def boxes(client, monkeypatch):
    monkeypatch.setenv("PHOTOBOX_LIVE_MATCHING", "true")
    monkeypatch.setenv("PHOTOBOX_MESSAGING", "true")
    with db_connect() as db:
        import_seed(db, jpeg("#222222"), "SYNTHETIC")
        presence.set_label(db, "pi-01", "Essen")
    client.post("/api/v1/devices/status", json={"state": "generating"})
    return client, box_client("box-berlin", "Berlin")


def visit(c, live_consent=True):
    sid = c.post("/api/v1/sessions").json()["id"]
    c.post(f"/api/v1/sessions/{sid}/consent", json={"processing": True, "documented_externally": True, "live": live_consent})
    c.post(f"/api/v1/sessions/{sid}/portrait", files={"image": ("x.jpg", jpeg(), "image/jpeg")})
    assert c.post(f"/api/v1/sessions/{sid}/process").status_code == 202
    return sid


def events(c):
    response = c.get("/api/v1/messaging/events")
    assert response.status_code == 200
    return response.json()["events"]


def say(c, sid, match, text="Hello!", message_id="m1"):
    return c.post(f"/api/v1/sessions/{sid}/messages", json={"message_id": message_id, "match_id": match, "text": text}).json()


def row(sid):
    with db_connect() as db:
        return db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()


@pytest.fixture
def pair(boxes):
    client, berlin = boxes
    leader, follower = visit(client), visit(berlin)
    message_windows.open_for(leader)
    match = message_windows.match_id(leader)
    assert [e["type"] for e in events(client)] == ["messaging.window_opened"] and [e["partners"] for e in events(berlin)] in ([], [["ESSEN"]])
    return client, berlin, leader, follower, match


def test_off_by_default(client, monkeypatch):
    monkeypatch.setenv("PHOTOBOX_MESSAGING", "false")
    assert client.get("/api/v1/messaging/events").status_code == 404
    assert client.post("/api/v1/sessions/x/messages", json={"message_id": "1", "match_id": "m", "text": "hi"}).status_code == 404


def test_needs_a_device_token(anonymous_client):
    assert anonymous_client.get("/api/v1/messaging/events").status_code == 401


def test_text_reaches_the_partner_with_the_servers_name_for_the_sender(pair):  # 6, 8
    client, berlin, leader, follower, match = pair
    assert say(client, leader, match)["type"] == "messaging.message_ack"
    received = events(berlin)
    assert [(e["sender_label"], e["text"]) for e in received] == [("ESSEN", "Hello!")] and events(client) == []
    assert say(berlin, follower, match, "Back at you", "m2")["type"] == "messaging.message_ack"
    assert events(client)[0]["sender_label"] == "BERLIN"


def test_a_destination_chosen_by_the_client_is_ignored(pair):  # 8
    client, berlin, leader, _, match = pair
    third = box_client("box-third", "Third")
    body = {"message_id": "m1", "match_id": match, "text": "Hi", "recipient_box_id": "box-third", "sender_label": "SANTA"}
    assert client.post(f"/api/v1/sessions/{leader}/messages", json=body).json()["status"] == "accepted"
    assert events(third) == [] and events(berlin)[0]["sender_label"] == "ESSEN"


def test_a_third_box_can_neither_send_nor_receive(pair):  # 7
    client, berlin, leader, _, match = pair
    third = box_client("box-third")
    own = third.post("/api/v1/sessions").json()["id"]
    assert say(third, own, match)["reason"] == "not_in_match"
    assert third.post(f"/api/v1/sessions/{leader}/messages", json={"message_id": "x", "match_id": match, "text": "hi"}).status_code == 404
    say(client, leader, match)
    assert events(third) == []
    assert events(client) == []


def test_a_window_opens_when_the_strip_starts_and_ends_when_it_is_done(boxes):  # 10
    client, berlin = boxes
    leader, follower = visit(client), visit(berlin)
    match = message_windows.match_id(leader)
    assert events(client) == []  # paired, but the strip has not started
    seen = {}

    class Talking(MockProvider):
        def generate_frame(self, request, should_cancel):
            if request.frame_index == 0:
                seen["open"] = [e["type"] for e in events(berlin)]
                seen["sent"] = say(client, leader, match)
            return super().generate_frame(request, should_cancel)

    jobs.run_pending(Talking())
    assert seen["open"] == ["messaging.window_opened"] and seen["sent"]["status"] == "accepted"
    closing = events(berlin)
    assert [e["reason"] for e in closing if e["type"] == "messaging.window_closed"] == ["generation_completed"]
    assert not [e for e in closing if e["type"] == "messaging.message_received"]  # unread text is dropped
    assert say(berlin, follower, match, "too late", "late")["reason"] in ("window_closed", "not_in_match")


@pytest.mark.parametrize("live_consent", [True, False])
def test_a_seed_or_pool_match_never_opens_a_window(boxes, live_consent):  # 7
    client, berlin = boxes
    sid = visit(client, live_consent)
    with db_connect() as db:
        db.execute("UPDATE generation_jobs SET next_attempt_at=?", ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
    message_windows.open_for(sid)
    assert jobs.run_pending(MockProvider()) == 1 and row(sid)["live_partner"] is None
    assert events(client) == [] and say(client, sid, message_windows.match_id(sid))["reason"] == "not_in_match"


def test_a_retry_keeps_the_original_window(pair):
    client, _, leader, _, match = pair
    before = message_windows.relay().open_matches("pi-01")
    message_windows.open_for(leader)
    assert message_windows.relay().open_matches("pi-01") == before and events(client) == []


class Broken(MockProvider):
    def generate_frame(self, request, should_cancel):
        raise GenerationError("broken on purpose")


def test_a_failed_strip_closes_the_window(boxes, monkeypatch):
    monkeypatch.setenv("PHOTOBOX_GENERATION_MAX_ATTEMPTS", "1")
    client, berlin = boxes
    visit(client), visit(berlin)
    jobs.run_pending(Broken())
    assert [e["reason"] for e in events(client) if e["type"] == "messaging.window_closed"] == ["generation_failed"]


def test_withdrawal_ends_the_window(pair):
    client, berlin, _, follower, match = pair
    with db_connect() as db:
        withdraw_session(db, follower)
    assert events(client)[-1]["reason"] == "participant_left"


def test_a_change_made_by_another_process_is_noticed(pair):
    """Withdrawal from the dashboard runs elsewhere; the database is checked on every send and poll."""
    client, berlin, leader, follower, match = pair
    with db_connect() as db:
        db.execute("UPDATE sessions SET live_partner=NULL, live_role=NULL, match_portrait_id=NULL WHERE id IN (?,?)", (leader, follower))
    assert say(client, leader, match)["reason"] == "window_closed"
    assert events(berlin)[-1]["reason"] == "participant_left"


def test_a_partner_box_going_silent_ends_the_window(pair):  # 11
    client, berlin, _, _, _ = pair
    with db_connect() as db:
        db.execute("UPDATE device_status SET reported_at='2020-01-01T00:00:00+00:00' WHERE device_id='box-berlin'")
    assert events(client)[-1]["reason"] == "participant_left"


# The filter

@pytest.mark.parametrize("text", ["Du Arschloch", "you ASSHOLE!", "Ruf mich an: 0171 234 5678", "0171-234-5678", "schreib an max@example.org",
                                  "max at example dot com", "Geh auf www.example.de", "https://example.org", "Mein Instagram ist toll", "Heil  Hitler"])
def test_blocked_text(text):  # 13
    assert is_blocked(text, list(DEFAULT_WORDS))


@pytest.mark.parametrize("text", ["Hallo aus Essen!", "Ich bin 25 Jahre alt", "Wir treffen uns um 12 30", "Class of 2024 here", "Punkt acht bin ich da", "Hello from Berlin"])
def test_ordinary_text_passes(text):
    assert not is_blocked(text, list(DEFAULT_WORDS))


def test_words_match_whole_words_and_umlauts_either_way():
    assert not is_blocked("Das Klassenzimmer", ["ass"]) and is_blocked("Du Müll", ["muell"]) and is_blocked("Du Muell", ["müll"])


def test_blocked_text_gets_a_neutral_refusal_and_is_not_delivered(pair):  # 13
    client, berlin, leader, _, match = pair
    assert say(client, leader, match, "Du Arschloch")["reason"] == "blocked"
    assert events(berlin) == []


def test_daily_counts_hold_no_text_or_identifiers(boxes):
    client, berlin = boxes
    leader, _ = visit(client), visit(berlin)
    message_windows.open_for(leader)
    match = message_windows.match_id(leader)
    say(client, leader, match, "Hallo")
    say(client, leader, match, "Du Arschloch", "m2")
    with db_connect() as db:
        rows = [dict(row) for row in db.execute("SELECT * FROM message_stats")]
    assert len(rows) == 1 and set(rows[0]) == {"day", "windows", "accepted", "blocked"}
    assert (rows[0]["windows"], rows[0]["accepted"], rows[0]["blocked"]) == (1, 1, 1)


def test_old_daily_counts_are_removed(boxes):
    with db_connect() as db:
        db.execute("INSERT INTO message_stats(day,windows,accepted,blocked) VALUES('2020-01-01',1,1,1)")
        message_windows.count(db, windows=1)
        assert [row["day"] for row in db.execute("SELECT day FROM message_stats")] != ["2020-01-01"]
        assert db.execute("SELECT COUNT(*) FROM message_stats WHERE day='2020-01-01'").fetchone()[0] == 0


def test_operator_list_is_used_and_validated(pair):
    client, berlin, leader, _, match = pair
    with db_connect() as db:
        assert save_words(db, "  Blumenkohl \n\nBlumenkohl\n") == ["blumenkohl"]
        assert load_words(db) == ["blumenkohl"]
        with pytest.raises(ValueError):
            save_words(db, "x" * 61)
    assert say(client, leader, match, "Blumenkohl!")["reason"] == "blocked"
    assert say(client, leader, match, "Arschloch", "m2")["status"] == "accepted"


def test_message_text_is_never_stored_and_not_in_the_logs(pair, caplog):  # 12
    client, berlin, leader, _, match = pair
    say(client, leader, match, "UniqueSecretWords")
    events(berlin)
    with db_connect() as db:
        assert "UniqueSecretWords" not in "\n".join(db.iterdump())
    assert "UniqueSecretWords" not in caplog.text


@pytest.fixture
def dash(anonymous_client):
    dashboard.app.state.sessions = Sessions()
    browser = TestClient(dashboard.app, base_url="http://127.0.0.1:8001", follow_redirects=False)
    with db_connect() as db:
        set_password(db, "correct horse battery")
    browser.post("/login", data={"password": "correct horse battery"})
    return browser


def test_dashboard_edits_the_word_list(dash):
    page = dash.get("/messages").text
    assert "arschloch" in page and "off" in page and "No messages have been sent" in page
    token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    assert dash.post("/messages", data={"csrf": token, "words": "eins\nzwei"}).status_code == 303
    assert "eins\nzwei" in dash.get("/messages").text
    assert dash.post("/messages", data={"words": "x"}).status_code == 403


# The kiosk's side

class Clock:
    now = 0.0

    def __call__(self):
        return self.now


class Keys:
    def __init__(self, *presses):
        self.presses, self.flushes = list(presses), 0

    def flush(self):
        self.flushes += 1

    def read_key(self, timeout):
        return self.presses.pop(0) if self.presses else None


class Api:
    def __init__(self, *batches, fail=None):
        self.batches, self.sent, self.fail = list(batches), [], fail

    def messaging_events(self):
        if self.fail:
            raise self.fail
        return self.batches.pop(0) if self.batches else []

    def send_message(self, sid, match, message_id, text):
        self.sent.append((sid, match, text))
        return {"type": "messaging.message_ack", "message_id": message_id, "status": "accepted"}


class Talker:
    def __init__(self, window_open, text="Hello"):
        self.window_open, self.text, self.ran = window_open, text, False

    def talk(self):
        self.ran = self.window_open()
        return self.text if self.ran else None


OPENED = {"type": "messaging.window_opened", "match_id": "m", "expires_at": "x", "partners": ["BERLIN"]}
CLOSED = {"type": "messaging.window_closed", "match_id": "m", "reason": "generation_completed"}
MESSAGE = {"type": "messaging.message_received", "match_id": "m", "message_id": "1", "sender_label": "BERLIN", "text": "Hi there", "expires_at": "x"}


def conversation(api, keys, talker=None, clock=None):
    made = []

    def make(window_open):
        made.append((talker or Talker)(window_open))
        return made[-1]

    c = Conversation(api, "sid", keys, make, clock or Clock())
    return c, made


def test_enter_does_nothing_before_the_server_opens_a_window():  # 1
    c, made = conversation(Api([]), Keys(ENTER))
    c.poll(), c.wait(1)
    assert made == [] and c.text() == ""


def test_enter_records_and_sends_during_a_window():  # 2
    api, clock = Api([OPENED]), Clock()
    c, made = conversation(api, Keys(ENTER), clock=clock)
    c.poll()
    assert "CONNECTED TO BERLIN · LIVE" in c.text() and "PRESS ENTER TO TALK" in c.text()
    c.wait(1)
    assert made[0].ran and api.sent == [("sid", "m", "Hello")] and "SENT TO BERLIN" in c.text()


def test_a_key_pressed_before_the_window_opened_does_not_count():
    keys = Keys()
    c, _ = conversation(Api([OPENED]), keys)
    c.poll()
    assert keys.flushes == 1


def test_a_window_closing_during_recording_sends_nothing():  # 10
    api = Api([OPENED], [CLOSED])
    clock = Clock()
    c, made = conversation(api, Keys(ENTER), clock=clock)
    c.poll()
    clock.now = 2.0
    c.wait(1)
    assert api.sent == [] and "Your photo is ready" in c.text()


def test_received_text_shows_then_expires_and_waits_its_turn():
    clock = Clock()
    second = MESSAGE | {"message_id": "2", "text": "Second"}
    c, _ = conversation(Api([OPENED, MESSAGE, second]), Keys(), clock=clock)
    c.poll()
    assert "BERLIN: Hi there" in c.text() and "Second" not in c.text()
    clock.now = 8.0
    assert "BERLIN: Second" in c.text()
    clock.now = 16.0
    assert "BERLIN:" not in c.text()


def test_without_a_microphone_text_is_still_received_and_no_prompt_is_shown():  # 4
    c = Conversation(Api([OPENED, MESSAGE]), "sid", Keys(ENTER), None, Clock())
    c.poll()
    assert "PRESS ENTER" not in c.text() and "BERLIN: Hi there" in c.text()
    c.wait(1)


def test_messaging_switched_off_stops_asking():
    request = httpx.Request("GET", "http://x")
    api = Api(fail=httpx.HTTPStatusError("off", request=request, response=httpx.Response(404, request=request)))
    c, _ = conversation(api, Keys())
    c.poll()
    assert c.enabled is False and c.text() == ""


def test_a_lost_connection_ends_the_window_and_replays_nothing():  # 11
    api = Api([OPENED])
    clock = Clock()
    c, _ = conversation(api, Keys(), clock=clock)
    c.poll()
    api.fail = httpx.ConnectError("down")
    clock.now = 2.0
    c.poll()
    assert c.open is False and c.text() == LOST


def test_the_strip_carries_on_while_visitors_talk():  # 14
    class Screen:
        shown = []

        def show(self, text):
            self.shown.append(text)

    class FollowApi(Api):
        states = [{"status": "processing", "job": {"frames_done": 1, "frames_total": 4}, "match": {"kind": "live", "partner_label": "Berlin"}},
                  {"status": "processing", "job": {"frames_done": 2, "frames_total": 4}, "match": {"kind": "live", "partner_label": "Berlin"}},
                  {"status": "complete", "job": {}, "match": {"kind": "live", "partner_label": "Berlin"}}]

        def status(self, sid):
            return self.states.pop(0)

    class KioskKeys(Keys):
        def wait(self, accepted, timeout):
            return ENTER

    api, keys, clock = FollowApi([OPENED]), KioskKeys(), iter(range(1000))
    tick = lambda: next(clock)  # noqa: E731
    kiosk = Kiosk(api, lambda: b"", Screen(), keys, sleep=lambda _s: None, clock=tick,
                  make_conversation=lambda sid: Conversation(api, sid, keys, None, tick))
    assert kiosk.follow("sid", "CODE") == "complete"
    assert any("CONNECTED TO BERLIN" in text for text in Screen.shown)
