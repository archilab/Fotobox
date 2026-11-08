"""Someone seems to be at a box, told to the others in real time (ADR-029, milestone D3). No camera or image."""
import json
import threading
import time
from datetime import datetime, timedelta, timezone
import httpx
import pytest
from fastapi.testclient import TestClient
from photobox_client import views
from photobox_client.kiosk import ENTER, Kiosk
from photobox_client.live_feed import PersonReporter, PresenceFeed
from photobox_server import presence
from photobox_server.app import app
from photobox_server.auth import create_device
from photobox_server.core import db_connect
from photobox_server.presence import events, messages, simulator
from photobox_server.presence.events import PresenceEvent
from photobox_server.presence.projection import PERSON_MAX_SECONDS, Box, order, project

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


def at(ago):
    return NOW - timedelta(seconds=ago)


def person(kind, ago, box="berlin", event_id=None):
    return PresenceEvent(event_id or f"{kind}-{ago}", kind, box, None, "c", at(ago))


def state(evs, status="idle", reported=5):
    return project([Box("berlin", "Berlin", status, at(reported))], evs, NOW)["berlin"]


# The state

def test_someone_seen_is_person_nearby():
    assert state([person("person.arrived", 10)]).activity_state == "person_nearby"


def test_gone_again():
    assert state([person("person.arrived", 20), person("person.left", 10)]).activity_state == "none"


def test_events_arriving_out_of_order_keep_the_newest():
    assert state([person("person.left", 5), person("person.arrived", 30)]).activity_state == "none"


def test_an_offline_box_shows_nobody():
    assert state([person("person.arrived", 200)], reported=200).activity_state == "none"


def test_a_detection_that_never_ends_stops_counting():
    assert state([person("person.arrived", PERSON_MAX_SECONDS + 1)]).activity_state == "none"


def test_a_real_visitor_outranks_someone_seen():
    evs = [person("person.arrived", 30), PresenceEvent("s", "session.started", "berlin", "s1", "s1", at(10))]
    assert state(evs, "capturing").activity_state == "session_active"


def test_it_ranks_between_live_and_recent():
    boxes = [Box(n, n, "idle", at(5)) for n in ("recent", "near")] + [Box("live", "live", "capturing", at(5))]
    evs = [PresenceEvent("p", "portrait.captured", "recent", "x", "x", at(5)), person("person.arrived", 5, box="near"),
           PresenceEvent("s", "session.started", "live", "s", "s", at(5))]
    assert [v.box_id for v in order(list(project(boxes, evs, NOW).values()))] == ["live", "near", "recent"]


# It is never more than it is

def test_the_message_is_careful_and_never_live():
    view = state([person("person.arrived", 10)])
    assert messages.choose(self_unavailable=False, locations=[view], earlier_portrait=False)[0] == "person_nearby"
    rendered = messages.render("person_nearby", view)
    assert rendered["texts"]["de"]["hero"] == "Jemand scheint gerade in Berlin zu sein."
    assert rendered["texts"]["en"]["hero"] == "Someone seems to be in Berlin right now."
    assert rendered["badge"]["en"] == "NEARBY" and "LIVE" not in json.dumps(rendered)


def test_it_never_makes_a_live_match_available():
    assert state([person("person.arrived", 10)]).match_available is False


@pytest.fixture
def boxes(client):
    with db_connect() as db:
        token = create_device(db, "box-berlin")
        presence.set_label(db, "box-berlin", "Berlin")
    other = TestClient(app)
    other.headers["Authorization"] = f"Bearer {token}"
    other.post("/api/v1/devices/status", json={"state": "idle"})
    return client, other


def report(c, present):
    return c.post("/api/v1/devices/person", json={"present": present})


def snap(c):
    return c.get("/api/v1/presence/snapshot").json()


def test_needs_a_device_token(anonymous_client):
    assert report(anonymous_client, True).status_code == 401


def test_the_other_boxes_are_told_with_this_boxs_name(boxes):
    client, berlin = boxes
    assert report(berlin, True).status_code == 204
    body = snap(client)
    assert body["message"]["texts"]["de"]["hero"] == "Jemand scheint gerade in Berlin zu sein."
    assert body["locations"][0]["activity_state"] == "person_nearby" and body["locations"][0]["status"]["de"] == "JEMAND DA"
    report(berlin, False)
    assert snap(client)["locations"][0]["activity_state"] == "none"


def test_a_box_is_not_told_about_itself(boxes):
    _, berlin = boxes
    report(berlin, True)
    assert snap(berlin)["message"]["key"] != "person_nearby"


def test_it_creates_no_session_and_no_consent(boxes):
    _, berlin = boxes
    report(berlin, True)
    with db_connect() as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM consent_records").fetchone()[0] == 0


def test_only_yes_or_no_is_accepted_and_kept(boxes):
    _, berlin = boxes
    assert berlin.post("/api/v1/devices/person", json={"present": "maybe"}).status_code == 422
    assert berlin.post("/api/v1/devices/person", json={"present": True, "image": "abc", "faces": 2}).status_code == 204
    with db_connect() as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM presence_events")]
    assert [r["type"] for r in rows] == ["person.arrived"] and rows[0]["session_id"] is None
    assert "abc" not in json.dumps(rows)


def test_person_events_are_deleted_with_the_others(client):
    with db_connect() as db:
        events.emit(db, "person.arrived", "pi-01", occurred_at=datetime.now(timezone.utc) - timedelta(seconds=events.RETENTION_SECONDS + 1))
        assert events.load(db) == []


def test_simulated_people_never_reach_a_box(boxes, client):
    with db_connect() as db:
        box = simulator.add_box(db, "Wien")
        simulator.act(db, box, "person")
        assert presence.snapshot(db, "pi-01", include_simulated=True)["locations"][0]["activity_state"] == "person_nearby"
    assert all(item["label"] != "Wien" for item in snap(client)["locations"])


# Real time

def test_the_stream_needs_a_token(anonymous_client):
    assert anonymous_client.get("/api/v1/presence/stream").status_code == 401


def test_the_stream_sends_the_snapshot(boxes):
    client, berlin = boxes
    report(berlin, True)
    response = client.get("/api/v1/presence/stream", params={"once": True})
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    kind, data = response.text.strip().split("\n")
    assert kind == "event: snapshot" and json.loads(data[6:])["message"]["key"] == "person_nearby"


# The box's side

def test_tile_shows_someone_nearby():
    snapshot = {"locations": [{"label": "Berlin", "device_state": "online", "activity_state": "person_nearby", "status": {"de": "JEMAND DA"}}],
                "message": {"key": "person_nearby", "badge": {"de": "JEMAND DA", "en": "NEARBY"}, "texts": {
                    "de": {"hero": "h", "support": "s"}, "en": {"hero": "h", "support": "s"}}}}
    state_ = views.start(snapshot, "p")
    assert state_["tiles"] == [{"kind": "nearby", "label": "Berlin", "status": "◐ JEMAND DA"}] and state_["badge"]["kind"] == "nearby"


def wait_until(check, seconds=3):
    deadline = time.monotonic() + seconds
    while not check() and time.monotonic() < deadline:
        time.sleep(0.01)
    return check()


def test_reporter_starts_by_saying_nobody():
    sent = []
    reporter = PersonReporter(sent.append).start()
    assert wait_until(lambda: sent == [False])
    reporter.stop()


def test_reporter_sends_changes():
    sent = []
    reporter = PersonReporter(sent.append).start()
    wait_until(lambda: sent)
    reporter(True)
    assert wait_until(lambda: sent[-1] is True)
    reporter(False)
    assert wait_until(lambda: sent[-1] is False)
    reporter.stop()


def test_reporter_retries_until_the_server_has_it(monkeypatch):
    from photobox_client import live_feed
    monkeypatch.setattr(live_feed, "RETRY_SECONDS", 0.01)
    sent, failures = [], [1, 1]

    def post(value):
        if failures:
            failures.pop()
            raise httpx.ConnectError("down")
        sent.append(value)

    reporter = PersonReporter(post).start()
    assert wait_until(lambda: sent == [False])
    reporter.stop()


def test_reporter_never_blocks_the_camera():
    block = threading.Event()
    reporter = PersonReporter(lambda _v: block.wait(2)).start()
    started = time.monotonic()
    reporter(True)
    reporter(False)
    assert time.monotonic() - started < 0.5
    block.set()
    reporter.stop()


def test_feed_keeps_the_newest_snapshot():
    feed = PresenceFeed("http://x", "t")
    assert feed.latest() == (0, None)
    feed._receive("event: snapshot")
    feed._receive(": keep-alive")
    feed._receive('data: {"a": 1}')
    assert feed.latest() == (1, {"a": 1})


class Screen:
    def __init__(self):
        self.shown = []

    def show(self, text):
        self.shown.append(text)


class Keys:
    def __init__(self, *answers):
        self.answers, self.timeouts = list(answers), []

    def flush(self):
        pass

    def wait(self, accepted, timeout):
        self.timeouts.append(timeout)
        return self.answers.pop(0)


class Api:
    polls = 0

    def presence(self):
        Api.polls += 1
        return None


class Feed:
    connected = True

    def __init__(self, *versions):
        self.versions = list(versions)

    def latest(self):
        return self.versions.pop(0) if len(self.versions) > 1 else self.versions[0]


def test_the_start_screen_redraws_only_when_the_feed_changes():
    Api.polls = 0
    screen, keys = Screen(), Keys(None, None, None, ENTER)
    feed = Feed((1, {}), (1, {}), (2, {}), (2, {}))
    Kiosk(Api(), lambda: b"", screen, keys, feed=feed).start_screen()
    assert len(screen.shown) == 2 and Api.polls == 0 and keys.timeouts[0] < 1


def test_without_the_feed_the_start_screen_asks_as_before():
    Api.polls = 0
    feed = Feed((0, None))
    feed.connected = False
    screen, keys = Screen(), Keys(None, ENTER)
    Kiosk(Api(), lambda: b"", screen, keys, feed=feed).start_screen()
    assert Api.polls == 2 and keys.timeouts[0] == 3


# What visitors can read

def test_the_start_screen_says_when_the_camera_is_looking():
    looking = views.start(None, "plain", detecting=True)
    assert "Die Kamera erkennt" in looking["camera_note"]["de"] and "No image is stored" in looking["camera_note"]["en"]
    assert "Die Kamera erkennt" in looking["plain"]
    assert views.start(None, "plain")["camera_note"] is None and "Kamera" not in views.start(None, "plain")["plain"]


def test_the_kiosk_shows_the_note_only_while_a_detector_is_set():
    class Detector:
        def resume(self):
            pass

        def pause(self):
            pass

    class ViewScreen:
        states = []

        def view(self, state):
            self.states.append(state)

    keys = Keys(ENTER)
    Kiosk(Api(), lambda: b"", ViewScreen(), keys, detector=Detector()).start_screen()
    assert ViewScreen.states[-1]["camera_note"] is not None
    ViewScreen.states.clear()
    Kiosk(Api(), lambda: b"", ViewScreen(), Keys(ENTER)).start_screen()
    assert ViewScreen.states[-1]["camera_note"] is None


# The operator's view

def test_activity_by_box_reports_real_boxes_only(boxes):
    client, berlin = boxes
    report(berlin, True)
    with db_connect() as db:
        box = simulator.add_box(db, "Wien")
        simulator.act(db, box, "person")
        activity = presence.activity_by_box(db)
    assert activity["box-berlin"] == "NEARBY" and box not in activity
