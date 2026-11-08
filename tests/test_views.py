"""Kiosk screens as data (ADR-028, milestone U1). No camera, terminal or browser."""
import json
import re
from pathlib import Path
import pytest
from photobox_client import views
from photobox_client.conversation import Conversation
from photobox_client.kiosk import ENTER, ESC, Kiosk
from photobox_client.speech import MAX_SECONDS, PushToTalk

UI_VIEWS = set(re.findall(r"^    (\w+): \(", (Path(__file__).parent.parent / "src/photobox_client/ui/screens.js").read_text(encoding="utf-8"), flags=re.MULTILINE))


class ViewScreen:
    """A screen that understands states, as the browser will."""

    def __init__(self):
        self.states = []

    def view(self, state):
        self.states.append(state)

    def show(self, text):
        raise AssertionError("a screen with view() must not be sent plain text")


class Keys:
    def __init__(self, *answers):
        self.answers = list(answers)

    def flush(self):
        pass

    def wait(self, accepted, timeout):
        return self.answers.pop(0) if self.answers else None

    def read_key(self, timeout):
        return None


POOL = {"version": "pool-1", "text": "POOL?\n\nENTER = Ja / Yes", "until": "x", "until_text": "09.11. 17:42"}
SNAPSHOT = {
    "self": {"box_id": "pi-01", "label": "Berlin", "state": "ready"},
    "message": {"priority": "P2", "key": "partner_waiting", "badge": {"de": "LIVE", "en": "LIVE"}, "texts": {
        "de": {"hero": "Jemand in Köln wartet.", "support": "Mach jetzt deins."}, "en": {"hero": "Someone in Cologne waits.", "support": "Take yours."}}},
    "locations": [{"label": "Köln", "device_state": "online", "activity_state": "session_active", "status": {"de": "LIVE", "en": "LIVE"}},
                  {"label": None, "device_state": "offline", "activity_state": "none", "status": {"de": "OFFLINE", "en": "OFFLINE"}}],
    "more_locations": 0,
}


class Api:
    states = None

    def __init__(self, pool=POOL):
        self.pool = pool
        self.states = [
            {"status": "queued", "job": {"waiting_for_partner": True, "wait_seconds_left": 9}, "match": {"kind": None}},
            {"status": "processing", "job": {"frames_done": 2, "frames_total": 4}, "match": {"kind": "live", "partner_label": "Köln"}},
            {"status": "complete", "job": {}, "match": {"kind": "live", "partner_label": "Köln"}},
        ]

    def presence(self):
        return SNAPSHOT

    def notice(self):
        return "v", "NOTICE", self.pool

    def start(self, version, pool=None):
        return "sid", "CODE-1"

    def upload_and_process(self, sid, image):
        pass

    def status(self, sid):
        return self.states.pop(0)


def visit(*keys, test_mode=False, api=None):
    screen = ViewScreen()
    clock = iter(range(1000))
    kiosk = Kiosk(api or Api(), lambda: b"jpeg", screen, Keys(*keys), sleep=lambda _s: None, clock=lambda: next(clock), test_mode=test_mode)
    return kiosk.run_once(), screen.states


def test_a_whole_visit_is_described_by_states_the_browser_can_draw():
    ending, states = visit(ENTER, ENTER, ENTER, ENTER)
    assert ending == "complete"
    names = [s["screen"] for s in states]
    assert names == ["start", "consent", "pool", "countdown", "countdown", "countdown", "flash", "making", "live_moment", "making", "done"]
    assert set(names) <= UI_VIEWS
    json.dumps(states)  # they travel to the browser as JSON
    assert all(s["plain"] for s in states) and all("test_mode" in s for s in states)


def test_every_state_builder_names_a_real_view():
    built = [views.start(None, "p"), views.consent("t"), views.pool(POOL), views.message("a\nb"), views.countdown(3, "p"), views.flash(),
             views.making(5, {}, None, "p"), views.live_moment(None, "p"), views.done(None, None, "p"), views.listening(3, 8, "p"), views.converting("p")]
    assert {s["screen"] for s in built} <= UI_VIEWS


def test_start_uses_the_servers_message_with_real_umlauts():
    state = views.start(SNAPSHOT, "plain")
    assert state["hero"]["de"] == "Jemand in Köln wartet." and state["badge"]["kind"] == "live" and state["label"] == "BERLIN"
    assert state["tiles"] == [{"kind": "live", "label": "Köln", "status": "● LIVE"}, {"kind": "offline", "label": "Fotobox", "status": "× OFFLINE"}]


def test_start_works_without_the_server():
    state = views.start(None, "plain")
    assert state["hero"] == views.IDLE_HERO and state["tiles"] == [] and state["badge"] is None


def test_the_box_name_stays_on_every_screen():
    _, states = visit(ENTER, ENTER, ENTER, ENTER)
    assert {s["label"] for s in states} == {"BERLIN"}


def test_test_mode_is_on_every_screen():
    _, states = visit(ENTER, ENTER, ENTER, ENTER, test_mode=True)
    assert all(s["test_mode"] and s["plain"].startswith("TEST MODE") for s in states)


def test_declining_shows_a_message_state():
    ending, states = visit(ENTER, ESC, api=Api(pool=None))
    assert ending == "declined" and states[-1]["screen"] == "message" and states[-1]["key"] is True and states[-1]["de"].startswith("Abgebrochen")


@pytest.mark.parametrize("status,kind,text", [
    ({"job": {"waiting_for_partner": True, "wait_seconds_left": 7}}, "searching", "Wir suchen"),
    ({"job": {"waiting_for_partner": True, "wait_seconds_left": 7, "partner_coming": True, "partner_coming_label": "Köln"}}, "searching", "Jemand in Köln"),
    ({"job": {}, "match": {"kind": "live", "partner_label": "Köln"}}, "live", "Verbunden mit Köln"),
    ({"job": {}, "match": {"kind": "live", "partner_label": None}}, "live", "einer anderen Fotobox"),
    ({"job": {}, "match": {"kind": "pool"}}, "other", "vorhin"),
])
def test_encounter_reports_what_the_server_knows(status, kind, text):
    view = views.encounter(status, False)
    assert view["kind"] == kind and text in view["de"]


def test_a_seed_shows_nothing_unless_someone_waited():
    status = {"job": {}, "match": {"kind": "seed"}}
    assert views.encounter(status, False) is None and views.encounter(status, True)["kind"] == "other"


def test_frames_are_only_given_once_known():
    assert views.making(0, {}, None, "p")["frames"] is None
    assert views.making(75, {"frames_done": 1, "frames_total": 4}, None, "p") | {"plain": ""} == {
        "screen": "making", "clock": "01:15", "frames": {"done": 1, "total": 4}, "encounter": None, "plain": ""}


def test_a_conversation_appears_inside_the_encounter():
    class ConvApi:
        def messaging_events(self):
            return [{"type": "messaging.window_opened", "match_id": "m", "expires_at": "x", "partners": ["KÖLN"]},
                    {"type": "messaging.message_received", "match_id": "m", "message_id": "1", "sender_label": "KÖLN", "text": "Grüße!", "expires_at": "x"}]

    c = Conversation(ConvApi(), "sid", Keys(), lambda _open: None, lambda: 0.0)
    assert c.view() is None
    c.poll()
    c.text()
    view = views.encounter({"job": {}, "match": {"kind": "live", "partner_label": "Köln"}}, False, c.view())
    assert view["talk"] == 8 and view["message"] == {"from": "KÖLN", "text": "Grüße!"} and view["status"] is None


def test_the_voice_screens_are_states_too():
    class Mic:
        def record(self, stop, seconds):
            stop.wait(0.3)
            return bytes(16000)

    class Transcriber:
        def transcribe(self, *_a):
            return "x"

    screen = ViewScreen()
    PushToTalk(Mic(), Transcriber(), Keys(), screen, lambda: True).talk()
    names = [s["screen"] for s in screen.states]
    assert names[0] == "listening" and screen.states[0]["limit"] == MAX_SECONDS and names[-2:] == ["converting", "message"]
