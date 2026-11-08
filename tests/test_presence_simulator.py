"""Presence simulator and the four interaction scenarios (milestone S3, spec sections 7 and 13).

Synthetic images only. Simulated boxes stand in for other boxes; the box under test (`pi-01`) is real.
"""
import io
import re
import threading
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from conftest import process
from photobox_server import dashboard, presence
from photobox_server.core import db_connect
from photobox_server.dashboard.security import Sessions, set_password
from photobox_server.pool import import_seed
from photobox_server.presence import events, simulator
from photobox_server.presence.simulator import SimulatorError

PASSWORD = "correct horse battery"


def jpeg(color="#878787"):
    out = io.BytesIO()
    Image.new("RGB", (120, 160), color).save(out, format="JPEG")
    return out.getvalue()


def preview(box="pi-01"):
    with db_connect() as db:
        return presence.snapshot(db, box, include_simulated=True)


def real(box="pi-01"):
    with db_connect() as db:
        return presence.snapshot(db, box)


def act(box_id, *actions):
    with db_connect() as db:
        for action in actions:
            simulator.act(db, box_id, action)


def add(label):
    with db_connect() as db:
        return simulator.add_box(db, label)


def state(snapshot, label):
    item = next(item for item in snapshot["locations"] if item["label"] == label)
    return item["device_state"], item["activity_state"]


@pytest.fixture
def berlin(client):
    return add("Berlin")


# The simulator itself

def test_actions_mirror_a_real_box(client, berlin):
    assert state(preview(), "Berlin") == ("online", "none")
    act(berlin, "start")
    assert state(preview(), "Berlin") == ("online", "session_active")
    act(berlin, "capture")
    assert state(preview(), "Berlin") == ("online", "session_active")
    act(berlin, "finish")
    assert state(preview(), "Berlin") == ("online", "recent_photo")
    act(berlin, "disconnect")
    assert state(preview(), "Berlin") == ("offline", "none")


def test_cancel_ends_live_without_a_recent_photo(client, berlin):
    act(berlin, "start", "cancel")
    assert state(preview(), "Berlin") == ("online", "none")


def test_expire_ends_live_and_the_recent_photo(client, berlin):
    act(berlin, "start", "capture", "expire")
    assert state(preview(), "Berlin") == ("online", "none")


def test_simulated_boxes_and_events_never_reach_a_box(client, berlin):  # 10
    act(berlin, "start")
    assert real()["locations"] == [] and real()["message"]["key"] == "idle"
    assert client.get("/api/v1/presence/snapshot").json()["locations"] == []
    assert preview()["locations"][0]["simulated"] is True


def test_impossible_actions_are_refused(client, berlin):
    for action, problem in (("capture", "start a session first"), ("finish", "start a session first"), ("fly", "unknown action")):
        with pytest.raises(SimulatorError, match=problem):
            act(berlin, action)
    act(berlin, "start")
    with pytest.raises(SimulatorError, match="already running"):
        act(berlin, "start")
    act(berlin, "disconnect")
    with pytest.raises(SimulatorError, match="connect the box first"):
        act(berlin, "cancel")
    with pytest.raises(SimulatorError, match="unknown simulated box"):
        act("pi-01", "start")  # never a real box


def test_at_most_five_boxes_with_valid_names(client):
    for name in ("Berlin", "London", "Paris", "Wien", "Rom"):
        add(name)
    with pytest.raises(SimulatorError, match="at most 5"):
        add("Oslo")
    with db_connect() as db:
        simulator.reset(db)
    with pytest.raises(ValueError):
        add("<b>")
    assert [row["box_id"] for row in db_rows()] == []


def db_rows():
    with db_connect() as db:
        return simulator.listing(db)


def test_simulated_ids_cannot_be_real_device_names(client):
    from photobox_server.auth import create_device
    with db_connect() as db, pytest.raises(ValueError):
        create_device(db, "sim:1")


def test_simulator_is_off_outside_developer_mode(client, monkeypatch):
    box = add("Berlin")
    monkeypatch.setenv("PHOTOBOX_DEVELOPER_MODE", "false")
    with pytest.raises(SimulatorError, match="DEVELOPER_MODE"):
        act(box, "start")
    with pytest.raises(SimulatorError):
        add("London")
    with db_connect() as db:
        simulator.reset(db)  # cleaning up stays possible
    assert db_rows() == []


def test_reset_removes_simulated_events_but_not_real_ones(client, berlin):
    act(berlin, "start")
    with db_connect() as db:
        events.emit(db, "portrait.captured", "pi-01", session_id="real")
        simulator.reset(db)
        assert [(e.box_id, e.simulated) for e in events.load(db, include_simulated=True)] == [("pi-01", False)]


# Races (matrix 9)

def test_two_starts_at_once_open_one_session(client, berlin):
    results = []

    def start():
        try:
            act(berlin, "start")
            results.append("started")
        except SimulatorError:
            results.append("refused")

    threads = [threading.Thread(target=start) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    with db_connect() as db:
        started = [e for e in events.load(db, include_simulated=True) if e.type == "session.started"]
    assert results.count("started") == 1 and len(started) == 1


def test_late_events_of_an_ended_session_cannot_revive_it(client, berlin):
    act(berlin, "start", "cancel")
    session = f"{berlin}-session-1"
    with db_connect() as db:
        events.emit(db, "session.started", berlin, session_id=session, simulated=True)  # redelivered late
        events.emit(db, "generation.started", berlin, session_id=session, simulated=True)
    assert state(preview(), "Berlin")[1] == "none"


def test_redelivered_events_count_once(client, berlin):
    with db_connect() as db:
        assert events.emit(db, "portrait.captured", berlin, event_id="same", simulated=True)
        assert not events.emit(db, "portrait.captured", berlin, event_id="same", simulated=True)


# The four interaction scenarios (spec section 7)

def test_scenario_a_another_visitor_is_active(client, berlin):
    add("London")
    act(berlin, "start")
    snapshot = preview()
    assert snapshot["message"]["key"] == "remote_active" and snapshot["message"]["badge"]["en"] == "LIVE"
    assert snapshot["message"]["texts"]["en"]["hero"] == "Someone is taking a photo in Berlin."
    assert [item["label"] for item in snapshot["locations"]] == ["Berlin", "London"]  # live first


def test_scenario_b_a_photo_was_taken_moments_ago(client, berlin):
    act(berlin, "start", "capture", "finish")
    snapshot = preview()
    assert snapshot["message"]["key"] == "recent_photo" and snapshot["message"]["badge"]["en"] == "RECENT"
    # A recent photo without pool consent is not offered as an earlier portrait (matrix 5).
    assert snapshot["earlier_portrait_available"] is False


def test_scenario_c_only_an_earlier_portrait(client, berlin, tmp_path, monkeypatch):
    (tmp_path / "pool.txt").write_text("POOL until {until}")
    monkeypatch.setenv("PHOTOBOX_POOL_NOTICE_FILE", str(tmp_path / "pool.txt"))
    sid = client.post("/api/v1/sessions").json()["id"]
    client.post(f"/api/v1/sessions/{sid}/consent", json={"processing": True, "pool": True, "documented_externally": True})
    client.post(f"/api/v1/sessions/{sid}/portrait", files={"image": ("x.jpg", jpeg(), "image/jpeg")})
    client.post("/api/v1/devices/status", json={"state": "idle"})  # that visitor has gone
    snapshot = preview()
    assert snapshot["message"]["key"] == "earlier_portrait" and snapshot["message"]["badge"]["en"] == "EARLIER"
    text = str(snapshot["message"]["texts"])
    assert "LIVE" not in text.upper() and "now" not in text  # never presented as live (matrix 6)
    act(berlin, "start")
    assert preview()["message"]["key"] == "remote_active"  # a real, live visitor outranks an earlier portrait


def test_scenario_d_single_pi_with_simulated_boxes(client, berlin):
    """The real box runs its whole session while simulated boxes are busy, and is unaffected (matrix 13)."""
    with db_connect() as db:
        import_seed(db, jpeg("#222222"), "SYNTHETIC")
    act(berlin, "start", "capture")
    sid = client.post("/api/v1/sessions").json()["id"]
    client.post(f"/api/v1/sessions/{sid}/consent", json={"processing": True, "documented_externally": True})
    client.post(f"/api/v1/sessions/{sid}/portrait", files={"image": ("x.jpg", jpeg(), "image/jpeg")})
    assert process(client, sid).status_code == 202
    assert client.get(f"/api/v1/sessions/{sid}").json()["status"] == "complete"
    with db_connect() as db:
        kinds = [(e.type, e.simulated) for e in events.load(db, include_simulated=True) if e.box_id == "pi-01"]
        assert kinds == [("session.started", False), ("portrait.captured", False), ("generation.started", False), ("generation.completed", False)]
    assert real()["locations"] == []


# The dashboard controls

@pytest.fixture
def dash(anonymous_client):
    dashboard.app.state.sessions = Sessions()
    browser = TestClient(dashboard.app, base_url="http://127.0.0.1:8001", follow_redirects=False)
    with db_connect() as db:
        set_password(db, PASSWORD)
    browser.post("/login", data={"password": PASSWORD})
    return browser


def token(browser):
    return re.search(r'name="csrf" value="([^"]+)"', browser.get("/presence").text).group(1)


def test_dashboard_controls_drive_the_preview(client, dash):
    keep = {"box": "pi-01", "lang": "en", "aspect": "9:16"}
    response = dash.post("/presence/simulator/add", data={"csrf": token(dash), "label": "Berlin", **keep})
    assert response.status_code == 303 and response.headers["location"] == "/presence?box=pi-01&lang=en&aspect=9%3A16"
    assert dash.post("/presence/simulator/sim:1/start", data={"csrf": token(dash), **keep}).status_code == 303
    page = dash.get("/presence?box=pi-01&lang=en").text
    assert "Someone is taking a photo in Berlin." in page and "session running" in page and "SIMULATED" in page
    refused = dash.post("/presence/simulator/sim:1/start", data={"csrf": token(dash), **keep})
    assert "already+running" in refused.headers["location"] or "already%20running" in refused.headers["location"]
    dash.post("/presence/simulator/reset", data={"csrf": token(dash)})
    assert "No simulated boxes yet" in dash.get("/presence").text


def test_dashboard_controls_need_the_form_token(client, dash):
    assert dash.post("/presence/simulator/add", data={"label": "Berlin"}).status_code == 403


def test_dashboard_hides_the_simulator_outside_developer_mode(client, dash, monkeypatch):
    monkeypatch.setenv("PHOTOBOX_DEVELOPER_MODE", "false")
    assert "Simulator" not in dash.get("/presence").text
    assert dash.post("/presence/simulator/add", data={"csrf": token(dash), "label": "Berlin"}).status_code == 404
