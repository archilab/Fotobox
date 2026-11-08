"""Social presence (ADR-023): events, projection, messages and the snapshot. Synthetic images only.

The numbered comments refer to the testing matrix in docs/INTERACTION_EXTENSION.md, section 13.
"""
import io
import json
from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from conftest import process
from photobox_client.kiosk import presence_text
from photobox_server import admin, presence
from photobox_server.app import app
from photobox_server.auth import create_device, revoke_device
from photobox_server.config import ConfigError
from photobox_server.core import db_connect
from photobox_server.pool import import_seed
from photobox_server.presence import events, messages
from photobox_server.presence.events import PresenceEvent
from photobox_server.presence.projection import LEASE_SECONDS, MAX_SESSION_SECONDS, OFFLINE_SECONDS, Box, Settings, order, project
from photobox_server.withdrawal import withdraw_session

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


def at(seconds_ago: float) -> datetime:
    return NOW - timedelta(seconds=seconds_ago)


def event(kind, ago, session="s1", box="berlin", event_id=None, simulated=False):
    return PresenceEvent(event_id or f"{kind}-{session}-{ago}", kind, box, session, session or "x", at(ago), simulated)


def box(status="idle", reported_ago=5, label="Berlin", box_id="berlin"):
    return Box(box_id, label, status, None if reported_ago is None else at(reported_ago))


def view(boxes, evs, settings=Settings()):
    return project(boxes, evs, NOW, settings)


# Projection: fixtures for offline, online, live, recent and expired states

def test_online_box_alone_is_never_active():  # 2
    result = view([box("idle")], [])["berlin"]
    assert result.device_state == "online" and result.activity_state == "none"


def test_status_reports_alone_never_make_a_box_active():
    # A box can report a session state, but without a recorded start nobody is signalled.
    assert view([box("capturing")], [])["berlin"].activity_state == "none"
    assert view([box("generating")], [event("portrait.captured", 400)])["berlin"].activity_state == "none"


def test_offline_box_shows_no_activity():  # 1
    evs = [event("session.started", 10), event("portrait.captured", 5)]
    result = view([box("capturing", reported_ago=OFFLINE_SECONDS + 1)], evs)["berlin"]
    assert result.device_state == "offline" and result.activity_state == "none"


def test_box_that_never_reported_is_offline():
    assert view([box(reported_ago=None)], [event("session.started", 5)])["berlin"].device_state == "offline"


def test_started_session_is_live():  # 3
    assert view([box("capturing", 2)], [event("session.started", 10)])["berlin"].activity_state == "session_active"


def test_ended_session_is_not_live():  # 3
    evs = [event("session.started", 20), event("session.ended", 10)]
    assert view([box("capturing", 2)], evs)["berlin"].activity_state == "none"


def test_idle_report_after_the_session_ends_it():  # 3
    evs = [event("session.started", 60), event("generation.completed", 30)]
    assert view([box("idle", 5)], evs)["berlin"].activity_state == "none"
    # An idle report from before the session started does not.
    assert view([box("idle", 70)], [event("session.started", 60)])["berlin"].activity_state == "session_active"


def test_session_expires_without_any_sign_of_it():  # 3
    old = LEASE_SECONDS + 5
    assert view([box("consent", old)], [event("session.started", old)])["berlin"].activity_state == "none"


def test_reports_of_a_session_state_renew_the_lease():
    started = LEASE_SECONDS + 100
    assert view([box("generating", 10)], [event("session.started", started)])["berlin"].activity_state == "session_active"


def test_a_session_cannot_stay_live_forever():
    started = MAX_SESSION_SECONDS + 1
    assert view([box("generating", 10)], [event("session.started", started)])["berlin"].activity_state == "none"


def test_activity_without_a_recorded_start_is_not_live():
    evs = [event("generation.started", 10, session="s2")]
    assert view([box("generating", 2)], evs)["berlin"].activity_state == "none"


def test_out_of_order_events_cannot_revive_an_ended_session():  # 9
    evs = [event("session.ended", 30), event("session.started", 10), event("generation.started", 5)]
    assert view([box("generating", 2)], evs)["berlin"].activity_state == "none"


def test_recent_photo_and_its_age_out():  # 4
    assert view([box("idle", 5)], [event("portrait.captured", 60)])["berlin"].activity_state == "recent_photo"
    assert view([box("idle", 5)], [event("portrait.captured", 181)])["berlin"].activity_state == "none"
    short = Settings(recent_seconds=30)
    assert view([box("idle", 5)], [event("portrait.captured", 60)], short)["berlin"].activity_state == "none"


def test_ages_are_published_only_in_detailed_mode():
    evs = [event("portrait.captured", 125)]
    assert view([box()], evs)["berlin"].activity_age_seconds is None
    assert view([box()], evs, Settings(detail="detailed"))["berlin"].activity_age_seconds == 125


def test_live_beats_recent_and_orders_the_panel():
    boxes = [box(label="Zurich", box_id="z"), box(label="Aachen", box_id="a", reported_ago=200), box("capturing", label="Muenster", box_id="m"),
             box(label="Bonn", box_id="b")]
    evs = [event("session.started", 5, box="m"), event("portrait.captured", 5, box="b", session="s9")]
    names = [v.label for v in order(list(view(boxes, evs).values()))]
    assert names == ["Muenster", "Bonn", "Zurich", "Aachen"]


def test_match_is_never_available_before_live_matching_exists():
    assert view([box("capturing", 2)], [event("session.started", 10)])["berlin"].match_available is False


# Messages

def bv(activity="none", state="online", label="Berlin", age=None):
    from photobox_server.presence.projection import BoxView
    return BoxView("b", label, state, activity, age)


@pytest.mark.parametrize("locations,earlier,key", [
    ([bv("session_active")], True, "remote_active"),
    ([bv("recent_photo"), bv("none")], True, "recent_photo"),
    ([bv()], True, "earlier_portrait"),
    ([bv()], False, "online_only"),
    ([bv(state="offline")], False, "idle"),
    ([], False, "idle"),
])
def test_message_priority_follows_real_state(locations, earlier, key):
    assert messages.choose(self_unavailable=False, locations=locations, earlier_portrait=earlier)[0] == key


def test_unavailable_box_and_live_match_come_first():
    assert messages.choose(self_unavailable=True, locations=[bv("session_active")], earlier_portrait=True)[0] == "unavailable"
    assert messages.choose(self_unavailable=False, locations=[], earlier_portrait=True, live_partner=bv())[0] == "live_match"


def test_earlier_portrait_is_never_presented_as_live():  # 6
    rendered = messages.render("earlier_portrait", None)
    for language in messages.LANGUAGES:
        assert "LIVE" not in json.dumps(rendered["texts"][language]).upper()


def test_every_message_exists_in_every_language_without_leftover_placeholders():
    for key in messages.CATALOG:
        for label in ("Berlin", None):
            rendered = messages.render(key, bv(label=label))
            for language in messages.LANGUAGES:
                text = rendered["texts"][language]
                assert text["hero"] and text["support"] and "{" not in text["hero"] + text["support"]


def test_unnamed_box_is_described_without_an_identifier():
    text = messages.render("remote_active", bv("session_active", label=None))["texts"]
    assert text["en"]["hero"] == "Someone is taking a photo at another Photobox."
    assert text["de"]["hero"] == "Jemand macht gerade ein Foto an einer anderen Fotobox."


@pytest.mark.parametrize("label", ["Berlin\x1b[2J", "{where}{elapsed}", "<script>alert(1)</script>", "Ber‮lin"])
def test_labels_cannot_inject_codes_or_placeholders(label):  # 11
    hero = messages.render("remote_active", bv("session_active", label=label))["texts"]["en"]["hero"]
    assert "\x1b" not in hero and "‮" not in hero
    if label.startswith("{"):
        assert hero == "Someone is taking a photo in {where}{elapsed}."


def test_elapsed_text():
    assert messages.elapsed(30, "en") == "just now" and messages.elapsed(150, "de") == "vor 2 Min."


# Events

def test_unknown_event_types_are_refused(client):
    with db_connect() as db, pytest.raises(ValueError):
        events.emit(db, "someone.waiting", "pi-01")


def test_repeated_events_are_stored_once(client):  # 9
    with db_connect() as db:
        assert events.emit(db, "session.started", "pi-01", session_id="s", event_id="e1")
        assert not events.emit(db, "session.started", "pi-01", session_id="s", event_id="e1")
        assert len(events.load(db)) == 1


def test_event_times_from_the_future_are_clamped(client):
    with db_connect() as db:
        events.emit(db, "portrait.captured", "pi-01", occurred_at=datetime.now(timezone.utc) + timedelta(days=1))
        assert events.load(db)[0].occurred_at <= datetime.now(timezone.utc)


def test_old_events_are_deleted(client):
    with db_connect() as db:
        events.emit(db, "portrait.captured", "pi-01", occurred_at=datetime.now(timezone.utc) - timedelta(seconds=events.RETENTION_SECONDS + 1))
        assert events.load(db) == []


def test_events_hold_no_image_data_or_paths(client):  # 12
    with db_connect() as db:
        import_seed(db, jpeg("#222222"), "SYNTHETIC")
    sid = start(client)
    upload(client, sid)
    process(client, sid)
    with db_connect() as db:
        columns = [row[1] for row in db.execute("PRAGMA table_info(presence_events)")]
        rows = [dict(row) for row in db.execute("SELECT * FROM presence_events")]
    assert columns == ["event_id", "type", "box_id", "session_id", "correlation_id", "occurred_at", "simulated"]
    assert [row["type"] for row in rows] == ["session.started", "portrait.captured", "generation.started", "generation.completed"]
    assert "storage" not in json.dumps(rows) and ".png" not in json.dumps(rows)


# The real session flow, through the API

def jpeg(color="#878787"):
    out = io.BytesIO()
    Image.new("RGB", (120, 160), color).save(out, format="JPEG")
    return out.getvalue()


def start(client, pool=False):
    sid = client.post("/api/v1/sessions").json()["id"]
    assert client.post(f"/api/v1/sessions/{sid}/consent", json={"processing": True, "pool": pool, "documented_externally": True}).status_code == 200
    return sid


def upload(client, sid):
    assert client.post(f"/api/v1/sessions/{sid}/portrait", files={"image": ("x.jpg", jpeg(), "image/jpeg")}).status_code == 200


@pytest.fixture
def berlin(client):
    """A second box, as a second kiosk on the Mac would be."""
    with db_connect() as db:
        token = create_device(db, "box-berlin")
        presence.set_label(db, "box-berlin", "Berlin")
    other = TestClient(app)
    other.headers["Authorization"] = f"Bearer {token}"
    return other


def report(client, state):
    assert client.post("/api/v1/devices/status", json={"state": state}).status_code == 204


def snap(client):
    response = client.get("/api/v1/presence/snapshot")
    assert response.status_code == 200
    return response.json()


def location(snapshot, box_id="box-berlin"):
    return next(item for item in snapshot["locations"] if item["box_id"] == box_id)


def test_snapshot_needs_a_device_token(anonymous_client):
    assert anonymous_client.get("/api/v1/presence/snapshot").status_code == 401


def test_snapshot_without_other_boxes(client):
    body = snap(client)
    assert body["type"] == "presence.snapshot" and body["self"] == {"box_id": "pi-01", "label": None, "state": "ready"}
    assert body["locations"] == [] and body["message"]["key"] == "idle" and body["detail"] == "coarse"


def test_remote_box_goes_from_online_to_live_to_recent_to_online(client, berlin):
    report(berlin, "idle")
    assert location(snap(client))["activity_state"] == "none" and snap(client)["message"]["key"] == "online_only"
    report(berlin, "consent")
    sid = berlin.post("/api/v1/sessions").json()["id"]
    # Before consent nothing is signalled about the visitor.
    assert location(snap(client))["activity_state"] == "none"
    berlin.post(f"/api/v1/sessions/{sid}/consent", json={"processing": True, "documented_externally": True})
    report(berlin, "capturing")
    body = snap(client)
    assert location(body)["activity_state"] == "session_active" and location(body)["status"]["en"] == "LIVE"
    assert body["message"]["key"] == "remote_active"
    assert body["message"]["texts"]["en"]["hero"] == "Someone is taking a photo in Berlin."
    upload(berlin, sid)
    report(berlin, "idle")
    body = snap(client)
    assert location(body)["activity_state"] == "recent_photo" and body["message"]["texts"]["de"]["hero"] == "Gerade wurde ein Foto in Berlin gemacht."


def test_own_box_is_not_listed_and_can_be_unavailable(client, berlin):
    report(client, "not ready")
    body = snap(client)
    assert [item["box_id"] for item in body["locations"]] == ["box-berlin"]
    assert body["self"]["state"] == "unavailable" and body["message"]["key"] == "unavailable"


def test_withdrawal_ends_live_at_once(client, berlin):
    report(berlin, "capturing")
    sid = start(berlin)
    with db_connect() as db:
        withdraw_session(db, sid)
    assert location(snap(client))["activity_state"] == "none"


def test_earlier_portrait_needs_pool_consent_and_ends_with_withdrawal(client, berlin):  # 5
    with db_connect() as db:
        import_seed(db, jpeg("#222222"), "SYNTHETIC")
    assert snap(client)["earlier_portrait_available"] is False  # seeds never count
    upload(berlin, start(berlin))
    assert snap(client)["earlier_portrait_available"] is False  # a recent photo without pool consent
    pooled = start(berlin, pool=True)
    upload(berlin, pooled)
    assert snap(client)["earlier_portrait_available"] is True
    with db_connect() as db:
        withdraw_session(db, pooled)
    assert snap(client)["earlier_portrait_available"] is False


def test_expired_pool_portrait_is_not_offered(client, berlin):
    upload(berlin, start(berlin, pool=True))
    with db_connect() as db:
        db.execute("UPDATE portraits SET expires_at=? WHERE pool_eligible=1", ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
    assert snap(client)["earlier_portrait_available"] is False


def test_simulated_events_never_reach_a_box(client, berlin):  # 10
    report(berlin, "capturing")
    with db_connect() as db:
        events.emit(db, "session.started", "box-berlin", session_id="sim", simulated=True)
        assert presence.snapshot(db, "pi-01", include_simulated=True)["locations"][0]["activity_state"] == "session_active"
    item = location(snap(client))
    assert item["activity_state"] == "none" and item["simulated"] is False


def test_state_survives_reconnects_and_restarts(client, berlin):  # 8
    report(berlin, "capturing")
    start(berlin)
    first = snap(client)
    second = TestClient(app, headers=dict(client.headers))
    assert location(second.get("/api/v1/presence/snapshot").json())["activity_state"] == location(first)["activity_state"] == "session_active"


def test_revoked_boxes_disappear(client, berlin):
    report(berlin, "idle")
    with db_connect() as db:
        revoke_device(db, "box-berlin")
    assert snap(client)["locations"] == []


def test_panel_is_limited_and_summarized(client):
    with db_connect() as db:
        for number in range(7):
            create_device(db, f"box-{number}")
    body = snap(client)
    assert len(body["locations"]) == presence.MAX_LOCATIONS and body["more_locations"] == 2


def test_snapshot_reveals_no_addresses_paths_or_people(client, berlin):  # 12
    with db_connect() as db:
        import_seed(db, jpeg("#222222"), "SYNTHETIC")
    report(berlin, "capturing")
    sid = start(berlin, pool=True)
    upload(berlin, sid)
    process(berlin, sid)
    text = json.dumps(snap(client))
    assert sid not in text and "storage" not in text and "127.0.0.1" not in text and "testclient" not in text
    assert set(location(snap(client))) == {"box_id", "label", "device_state", "activity_state", "activity_age_seconds", "match_available", "simulated", "status"}


def test_detail_setting(client, berlin, monkeypatch):
    upload(berlin, start(berlin))
    report(berlin, "idle")
    assert location(snap(client))["activity_state"] == "recent_photo"
    assert location(snap(client))["activity_age_seconds"] is None
    monkeypatch.setenv("PHOTOBOX_PUBLIC_ACTIVITY_DETAIL", "detailed")
    assert location(snap(client))["activity_age_seconds"] >= 0
    monkeypatch.setenv("PHOTOBOX_PUBLIC_ACTIVITY_DETAIL", "everything")
    with pytest.raises(ConfigError):
        presence.settings()


# Box names

@pytest.mark.parametrize("label", ["Hall B", "Berlin", "Köln (Dom)", "Booth 2/3"])
def test_valid_labels(label):
    assert presence.validate_label(label) == label


@pytest.mark.parametrize("label", ["", "-start", "x" * 33, "a<b>", "_x", "tab\there"])
def test_invalid_labels(label):
    if label == "tab\there":
        assert presence.validate_label(label) == "tab here"  # whitespace is normalized, not refused
        return
    with pytest.raises(ValueError):
        presence.validate_label(label)


def test_admin_command_sets_and_clears_a_label(client, capsys):
    assert admin.main(["device", "label", "pi-01", "Hall B"]) == 0
    assert snap(client)["self"]["label"] == "Hall B"
    assert admin.main(["device", "label", "pi-01"]) == 0
    assert snap(client)["self"]["label"] is None
    assert admin.main(["device", "label", "nobody", "X"]) == 1
    assert admin.main(["device", "label", "pi-01", "<bad>"]) == 1


# The kiosk's start screen

def test_kiosk_shows_both_languages_and_a_few_boxes(client, berlin):
    report(berlin, "capturing")
    start(berlin)
    text = presence_text(snap(client))
    assert "Jemand macht gerade ein Foto in Berlin." in text and "Someone is taking a photo in Berlin." in text
    assert "Berlin  ·  LIVE" in text


def test_kiosk_writes_umlauts_as_plain_letters():
    text = presence_text({"message": {"texts": {"de": {"hero": "Diese Fotobox ist gerade nicht verfügbar.", "support": "Größe"}}}})
    assert "verfuegbar" in text and "Groesse" in text


def test_kiosk_start_screen_without_presence():
    assert presence_text(None) == "" and presence_text({}) == ""


def test_kiosk_start_screen_refreshes_and_survives_a_failing_server():
    import httpx
    from photobox_client.kiosk import ENTER, Kiosk

    class Keys:
        def __init__(self):
            self.answers = [None, None, ENTER]
            self.flushes = 0

        def flush(self):
            self.flushes += 1

        def wait(self, accepted, timeout):
            assert timeout is not None
            return self.answers.pop(0)

    class Api:
        calls = 0

        def presence(self):
            Api.calls += 1
            if Api.calls == 2:
                raise httpx.ConnectError("down")
            return {"message": {"texts": {"en": {"hero": "Other Photoboxes are connected.", "support": "s"}}}, "locations": []}

    shown = []
    keys = Keys()
    kiosk = Kiosk(Api(), lambda: b"", type("S", (), {"show": lambda self, t: shown.append(t)})(), keys)
    assert kiosk.start_screen() == ENTER
    assert len(shown) == 3 and keys.flushes == 1  # refreshing never discards a key press
    assert "Other Photoboxes" in shown[0] and "Other Photoboxes" not in shown[1] and "ENTER" in shown[1]
