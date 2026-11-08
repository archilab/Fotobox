"""Ephemeral message relay and its simulator (ADR-025, milestone M-VOICE-0). The numbers refer to spec section 15.9."""
import re
import threading
from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from photobox_server import dashboard, messaging
from photobox_server.core import db_connect
from photobox_server.dashboard.encounter import Encounter
from photobox_server.dashboard.security import Sessions, set_password
from photobox_server.messaging import Limits, MessagingError, Participant, Relay
from photobox_server.presence import simulator

ESSEN = Participant("s-essen", "box-essen", "Essen")
BERLIN = Participant("s-berlin", "box-berlin", "Berlin")


class Clock:
    def __init__(self):
        self.moment = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.moment

    def advance(self, seconds):
        self.moment += timedelta(seconds=seconds)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def relay(clock):
    r = Relay(Limits(), clock)
    r.open_window("m", "live", [ESSEN, BERLIN])
    r.drain("box-essen"), r.drain("box-berlin")
    return r


def send(relay, who=ESSEN, text="Hello from Essen!", message_id="a1", match="m"):
    return relay.send(who.box_id, who.session_id, match, message_id, text)


def reason(event):
    return event.get("reason")


def test_text_reaches_only_the_partner_with_a_server_verified_name(relay):  # 6, 8
    assert send(relay) == {"type": "messaging.message_ack", "message_id": "a1", "status": "accepted"}
    received = relay.drain("box-berlin")
    assert [(e["type"], e["sender_label"], e["text"]) for e in received] == [("messaging.message_received", "ESSEN", "Hello from Essen!")]
    assert relay.drain("box-essen") == [] and relay.drain("box-third") == []


def test_both_directions(relay, clock):
    send(relay)
    clock.advance(1)
    send(relay, BERLIN, "Hello back", "b1")
    assert relay.drain("box-essen")[0]["text"] == "Hello back"


def test_window_opened_names_the_partner_and_expiry(clock):
    r = Relay(Limits(), clock)
    r.open_window("m", "live", [ESSEN, BERLIN])
    event = r.drain("box-essen")[0]
    assert event["type"] == "messaging.window_opened" and event["partners"] == ["BERLIN"]
    assert datetime.fromisoformat(event["expires_at"]) == clock() + timedelta(seconds=45)


@pytest.mark.parametrize("kind", ["pool", "seed", None])
def test_only_a_live_match_opens_a_window(clock, kind):  # 7
    with pytest.raises(MessagingError):
        Relay(Limits(), clock).open_window("m", kind, [ESSEN, BERLIN])


def test_a_window_needs_two_different_boxes(clock):
    r = Relay(Limits(), clock)
    with pytest.raises(MessagingError):
        r.open_window("m", "live", [ESSEN])
    with pytest.raises(MessagingError):
        r.open_window("m", "live", [ESSEN, Participant("other", ESSEN.box_id, "Essen")])


@pytest.mark.parametrize("box,session,match", [
    ("box-third", "s-third", "m"),        # a stranger
    ("box-third", "s-essen", "m"),        # another box claiming a participant's session
    ("box-essen", "s-essen", "unknown"),  # no such match
])
def test_outsiders_all_get_the_same_answer(relay, box, session, match):  # 6, 7
    assert reason(relay.send(box, session, match, "x", "hi")) == "not_in_match"
    assert relay.drain("box-berlin") == []


def test_a_repeated_message_is_delivered_once(relay):  # 9
    first = send(relay)
    assert send(relay) == first and len(relay.drain("box-berlin")) == 1


def test_sender_limit_and_cooldown(relay, clock):  # 9
    send(relay, message_id="1")
    assert reason(send(relay, message_id="2")) == "cooldown"
    clock.advance(3)
    send(relay, message_id="2")
    clock.advance(3)
    send(relay, message_id="3")
    clock.advance(3)
    assert reason(send(relay, message_id="4")) == "limit_reached"


@pytest.mark.parametrize("text,why", [("", "empty"), ("  \n ", "empty"), ("\x1b\x07", "empty"), ("x" * 161, "too_long"), ("x" * 5000, "too_long")])
def test_empty_or_oversized_text_is_rejected_not_cut(relay, text, why):  # 9
    assert reason(send(relay, text=text)) == why and relay.drain("box-berlin") == []


def test_exactly_the_limit_is_accepted(relay):
    assert send(relay, text="x" * 160)["type"] == "messaging.message_ack"


def test_control_codes_and_extra_space_are_removed(relay):  # 13
    send(relay, text="  Hi\x1b[2J ‮  there\n")
    assert relay.drain("box-berlin")[0]["text"] == "Hi[2J there"


def test_html_stays_plain_text_for_the_page_to_escape(relay):  # 13
    send(relay, text="<script>alert(1)</script>")
    assert relay.drain("box-berlin")[0]["text"] == "<script>alert(1)</script>"


def test_invalid_message_id(relay):
    assert reason(send(relay, message_id="bad id!")) == "invalid"


def test_closing_refuses_late_messages_and_drops_unread_ones(relay):  # 10, 11
    send(relay)
    assert relay.close("m", "generation_completed")
    events = relay.drain("box-berlin")
    assert [e["type"] for e in events] == ["messaging.window_closed"] and events[0]["reason"] == "generation_completed"
    assert reason(send(relay, BERLIN, "too late", "late")) == "window_closed"
    assert not relay.close("m", "timeout")  # already closed


def test_deadline_is_enforced_by_the_server(relay, clock):  # 10
    clock.advance(45)
    assert reason(send(relay)) == "window_closed"
    assert relay.drain("box-essen")[0]["reason"] == "timeout"


def test_unread_messages_expire_and_are_not_replayed(relay, clock):  # 11, 12
    send(relay)
    clock.advance(8)
    assert relay.drain("box-berlin") == []


def test_delivered_only_once(relay):  # 11, 12
    send(relay)
    assert len(relay.drain("box-berlin")) == 1 and relay.drain("box-berlin") == []


def test_disconnect_closes_the_window(relay):  # 11
    send(relay)
    relay.leave("box-berlin")
    assert [e["reason"] for e in relay.drain("box-berlin")] == ["participant_left"]
    assert reason(send(relay, message_id="z")) == "window_closed"


def test_old_windows_are_forgotten(relay, clock):
    relay.close("m", "cancelled")
    clock.advance(messaging.CLOSED_WINDOW_SECONDS + 1)
    assert reason(send(relay, message_id="z")) == "not_in_match"


def test_a_full_inbox_stays_bounded(clock):
    r = Relay(Limits(max_per_sender=10, cooldown_seconds=0), clock)
    for n in range(40):
        r.open_window(f"m{n}", "live", [ESSEN, BERLIN])
    assert len(r.drain("box-essen")) == messaging.INBOX_LIMIT


def test_unknown_close_reason(relay):
    with pytest.raises(MessagingError):
        relay.close("m", "because")


def test_two_sends_at_once_count_once(relay):
    results = []
    threads = [threading.Thread(target=lambda: results.append(send(relay))) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(relay.drain("box-berlin")) == 1 and len(results) == 8


def test_limits_come_from_settings(monkeypatch):
    monkeypatch.setenv("PHOTOBOX_MESSAGE_MAX_CHARS", "80")
    assert Limits.from_env().max_chars == 80


# The simulator (acceptance of M-VOICE-0)

@pytest.fixture
def dash(anonymous_client):
    dashboard.app.state.sessions = Sessions()
    dashboard.app.state.encounter = Encounter()
    browser = TestClient(dashboard.app, base_url="http://127.0.0.1:8001", follow_redirects=False)
    with db_connect() as db:
        set_password(db, "correct horse battery")
    browser.post("/login", data={"password": "correct horse battery"})
    return browser


@pytest.fixture
def sims(dash):
    with db_connect() as db:
        ids = [simulator.add_box(db, name) for name in ("Essen", "Berlin", "London")]
        for box in ids:
            simulator.act(db, box, "start")
    return ids


def post(dash, path, **data):
    token = re.search(r'name="csrf" value="([^"]+)"', dash.get("/presence").text).group(1)
    return dash.post(f"/presence/simulator/encounter/{path}", data={"csrf": token, **data})


def cards(dash):
    return {c["label"]: c for c in dash.get("/presence/simulator/encounter").json()["cards"]}


def test_text_flows_only_between_the_pair_and_ends_with_the_strip(dash, sims):
    essen, berlin, london = sims
    assert post(dash, "live", first=essen, second=berlin).status_code == 303
    assert cards(dash)["Essen"]["open"] and cards(dash)["Essen"]["partner"] == "BERLIN"
    post(dash, "send", sender=essen, text="Hello from Essen!")
    view = cards(dash)
    assert view["Berlin"]["received"] == [{"from": "ESSEN", "text": "Hello from Essen!"}]
    assert view["Essen"]["status"] == "SENT TO BERLIN" and view["Essen"]["received"] == []
    assert view["London"]["received"] == [] and view["London"]["member"] is False
    post(dash, "send", sender=london, text="Let me in")
    view = cards(dash)
    assert view["London"]["status"].startswith("Not part") and all("Let me in" not in str(c["received"]) for c in view.values())
    post(dash, "send", sender=berlin, text="Hi Essen")
    assert cards(dash)["Essen"]["received"][0]["text"] == "Hi Essen"
    post(dash, "ready")
    view = cards(dash)
    assert view["Essen"]["closed"] == "Your photo is ready. Messaging has ended." and not view["Berlin"]["open"]
    assert view["Berlin"]["received"] == []
    post(dash, "send", sender=essen, text="too late")
    assert cards(dash)["Essen"]["status"].startswith("Messaging has ended")


def test_a_pool_match_opens_no_window(dash, sims):
    post(dash, "pool", first=sims[0])
    view = cards(dash)["Essen"]
    assert view["pool_text"].endswith("No one is online in this encounter.") and not view["open"]
    post(dash, "send", sender=sims[0], text="Anyone there?")
    assert cards(dash)["Essen"]["status"].startswith("Not part")


def test_pairing_needs_running_sessions(dash, sims):
    with db_connect() as db:
        simulator.act(db, sims[1], "cancel")
    response = post(dash, "live", first=sims[0], second=sims[1])
    assert "session" in response.headers["location"] and not cards(dash)["Essen"]["open"]


def test_a_box_cannot_be_paired_with_itself(dash, sims):
    response = post(dash, "live", first=sims[0], second=sims[0])
    assert "two+different" in response.headers["location"] or "two%20different" in response.headers["location"]


def test_message_text_is_never_stored(dash, sims):
    post(dash, "live", first=sims[0], second=sims[1])
    post(dash, "send", sender=sims[0], text="UniqueSecretWords")
    with db_connect() as db:
        dump = "\n".join(db.iterdump())
    assert "UniqueSecretWords" not in dump


def test_script_only_sets_text(dash):
    script = dash.get("/static/encounter.js").text
    assert "innerHTML" not in script and "textContent" in script


def test_off_outside_developer_mode(dash, sims, monkeypatch):
    token = re.search(r'name="csrf" value="([^"]+)"', dash.get("/presence").text).group(1)
    monkeypatch.setenv("PHOTOBOX_DEVELOPER_MODE", "false")
    assert dash.get("/presence/simulator/encounter").status_code == 404
    assert dash.post("/presence/simulator/encounter/live", data={"csrf": token, "first": sims[0], "second": sims[1]}).status_code == 404
