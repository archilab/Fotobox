"""The hidden video link between two boxes (ADR-045), server side. No images are involved."""
from datetime import datetime, timedelta, timezone

import pytest

from photobox_server import presence
from photobox_server.core import db_connect
from tests.test_live import box_client


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("PHOTOBOX_VIDEO_LINK", "true")


@pytest.fixture
def boxes(client, on):
    berlin = box_client("box-berlin", "Berlin")
    for c in (client, berlin):
        assert c.post("/api/v1/devices/status", json={"state": "idle"}).status_code == 204
    return client, berlin


def agreed(c):
    return c.post("/api/v1/video/calls", json={"agreed": True})


def audit_events():
    with db_connect() as db:
        return [(r["event"], r["device_id"], r["detail"].split()[1]) for r in db.execute("SELECT * FROM audit_events WHERE event='video_agreed' ORDER BY id")]


def test_off_unless_switched_on(client):
    assert client.get("/api/v1/video/current").status_code == 404
    assert agreed(client).status_code == 404
    assert "video" not in client.get("/api/v1/presence/snapshot").json()


def test_a_call_needs_the_visitors_agreement(boxes):
    client, _ = boxes
    assert client.post("/api/v1/video/calls", json={"agreed": False}).status_code == 400


def test_both_sides_agree_then_either_ends_it(boxes):
    client, berlin = boxes
    response = agreed(client)
    assert response.status_code == 201
    link = response.json()["link"]
    assert link["role"] == "caller" and link["status"] == "requested" and link["place"] == "Berlin"
    # the asked box learns of it through its presence stream
    asked = berlin.get("/api/v1/presence/snapshot").json()["video"]
    assert asked["id"] == link["id"] and asked["role"] == "callee" and asked["place"] is None  # pi-01 has no public name
    until = datetime.fromisoformat(asked["until"])
    assert 15 <= (until - datetime.now(timezone.utc)).total_seconds() <= 20
    # the caller cannot accept its own call
    assert client.post(f"/api/v1/video/calls/{link['id']}/accept", json={"agreed": True}).status_code == 409
    accepted = berlin.post(f"/api/v1/video/calls/{link['id']}/accept", json={"agreed": True})
    assert accepted.status_code == 200 and accepted.json()["link"]["status"] == "connected"
    seconds = (datetime.fromisoformat(accepted.json()["link"]["until"]) - datetime.fromisoformat(accepted.json()["now"])).total_seconds()
    assert 118 <= seconds <= 120
    assert client.get("/api/v1/video/current").json()["link"]["status"] == "connected"
    ended = berlin.post(f"/api/v1/video/calls/{link['id']}/end")
    assert ended.json()["link"]["status"] == "ended" and ended.json()["link"]["end_reason"] == "callee"
    assert client.get(f"/api/v1/video/calls/{link['id']}").json()["link"]["end_reason"] == "callee"
    assert client.get("/api/v1/video/current").json()["link"] is None
    assert audit_events() == [("video_agreed", "pi-01", "caller"), ("video_agreed", "box-berlin", "callee")]


def test_no_free_box_means_no_call(boxes):
    client, berlin = boxes
    assert berlin.post("/api/v1/devices/status", json={"state": "idle", "detail": "resting"}).status_code == 204
    assert agreed(client).status_code == 409
    assert berlin.post("/api/v1/devices/status", json={"state": "generating"}).status_code == 204
    assert agreed(client).status_code == 409
    assert berlin.post("/api/v1/devices/status", json={"state": "idle"}).status_code == 204
    assert agreed(client).status_code == 201
    assert agreed(client).status_code == 409  # one link at a time per box
    assert agreed(berlin).status_code == 409  # berlin is already asked


def test_an_unanswered_request_ends_after_twenty_seconds(boxes):
    client, berlin = boxes
    link = agreed(client).json()["link"]
    with db_connect() as db:
        db.execute("UPDATE video_links SET answer_by=?", ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
    assert berlin.get("/api/v1/presence/snapshot").json()["video"] is None
    assert berlin.post(f"/api/v1/video/calls/{link['id']}/accept", json={"agreed": True}).status_code == 409
    assert client.get(f"/api/v1/video/calls/{link['id']}").json()["link"]["end_reason"] == "unanswered"


def test_a_link_ends_when_its_time_is_up(boxes):
    client, berlin = boxes
    link = agreed(client).json()["link"]
    berlin.post(f"/api/v1/video/calls/{link['id']}/accept", json={"agreed": True})
    with db_connect() as db:
        db.execute("UPDATE video_links SET ends_at=?", ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
    assert client.get(f"/api/v1/video/calls/{link['id']}").json()["link"]["end_reason"] == "time"


def test_the_caller_can_withdraw_the_request(boxes):
    client, berlin = boxes
    link = agreed(client).json()["link"]
    assert client.post(f"/api/v1/video/calls/{link['id']}/end").json()["link"]["end_reason"] == "cancelled"
    assert berlin.post(f"/api/v1/video/calls/{link['id']}/accept", json={"agreed": True}).status_code == 409


def test_other_boxes_see_nothing_of_a_link(boxes):
    client, berlin = boxes
    third = box_client("box-wien", "Wien")
    third.post("/api/v1/devices/status", json={"state": "generating"})
    link = agreed(client).json()["link"]
    assert third.get("/api/v1/presence/snapshot").json()["video"] is None
    assert third.get(f"/api/v1/video/calls/{link['id']}").status_code == 404
    assert third.post(f"/api/v1/video/calls/{link['id']}/end").status_code == 404


def test_someone_nearby_is_asked_first(boxes):
    client, berlin = boxes
    wien = box_client("box-wien", "Wien")
    wien.post("/api/v1/devices/status", json={"state": "idle"})
    assert wien.post("/api/v1/devices/person", json={"present": True}).status_code == 204
    assert agreed(client).json()["link"]["place"] == "Wien"


def test_old_links_are_forgotten_after_a_day(boxes):
    client, berlin = boxes
    link = agreed(client).json()["link"]
    client.post(f"/api/v1/video/calls/{link['id']}/end")
    with db_connect() as db:
        db.execute("UPDATE video_links SET created_at=?", ((datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),))
    client.get("/api/v1/video/current")
    with db_connect() as db:
        assert db.execute("SELECT COUNT(*) FROM video_links").fetchone()[0] == 0


def test_the_link_record_holds_nothing_about_a_person():
    with db_connect() as db:
        columns = {row["name"] for row in db.execute("PRAGMA table_info(video_links)")}
    assert columns == {"id", "caller", "callee", "status", "created_at", "answer_by", "connected_at", "ends_at", "ended_at", "end_reason"}
