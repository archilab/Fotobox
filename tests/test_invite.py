"""The invitation to a shared strip on an idle booth (ADR-037, milestone J2)."""
from datetime import UTC, datetime, timedelta

from photobox_client import views
from photobox_client.kiosk import ENTER, Kiosk
from photobox_client.lights import PATTERNS, VIOLET, light_for
from photobox_client.webui import WebUI

NOW = datetime(2026, 11, 25, 12, 0, 0, tzinfo=UTC)
TEXTS = {"de": {"hero": "Jemand in Berlin wartet auf ein gemeinsames Foto.", "support": "Mach jetzt deins und ihr landet auf einem Streifen."},
         "en": {"hero": "Someone in Berlin is waiting for a shared photo.", "support": "Take yours now to join them."}}


def invited(seconds=17.2, key="partner_waiting", until=True):
    message = {"key": key, "texts": TEXTS, "badge": {"de": "LIVE", "en": "LIVE"}}
    if until:
        message["until"] = (NOW + timedelta(seconds=seconds)).isoformat()
    return {"generated_at": NOW.isoformat(), "self": {"label": "Medienhaus"}, "locations": [], "message": message}


def test_seconds_left_come_from_the_servers_two_times():
    assert views.invitation_seconds(invited(17.2)) == 18
    assert views.invitation_seconds(invited(-1)) == 0
    assert views.invitation_seconds(invited(until=False)) == 0  # an older server without an end time
    assert views.invitation_seconds(invited(key="remote_active")) == 0
    assert views.invitation_seconds(None) == 0
    broken = invited()
    broken["message"]["until"] = "soon"
    assert views.invitation_seconds(broken) == 0


def test_an_invitation_turns_the_start_screen_into_the_big_invitation():
    state = views.start(invited(17.2), "START")
    assert state["screen"] == "invite" and state["seconds"] == 18 and state["label"] == "MEDIENHAUS"
    assert state["hero"]["de"] == TEXTS["de"]["hero"] and state["support"]["en"] == TEXTS["en"]["support"]
    assert "Noch 18 s / 18 s left" in state["plain"] and "ENTER = Mitmachen / Join" in state["plain"]
    assert views.start(invited(-1), "START")["screen"] == "start"
    assert views.start(invited(until=False), "START")["screen"] == "start"


def test_the_invitation_blinks_violet_fast():
    assert light_for(views.start(invited(), "")) == "invite"
    colour, on, off = PATTERNS["invite"]
    assert colour == VIOLET and off > 0 and on < PATTERNS["done"][1]


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


class Api:
    pool, printing = None, False

    def __init__(self):
        self.calls = []

    def presence(self):
        return invited()

    def notice(self):
        self.calls.append("notice")
        return "notice-abc", "NOTICE", None

    def start(self, version, pool=None):
        self.calls.append("start")
        return "sid", "CODE-1"

    def upload_and_process(self, sid, image):
        pass

    def status(self, sid):
        return {"status": "complete", "job": None}


def test_pressing_on_the_invitation_starts_an_ordinary_visit_with_consent():
    api, screen, lights = Api(), Screen(), []
    kiosk = Kiosk(api, lambda: b"jpeg", screen, Keys(ENTER, ENTER), sleep=lambda _s: None, clock=iter(range(10_000)).__next__,
                  light=lambda name: name and lights.append(name), button=True)
    assert kiosk.run_once() == "complete"
    assert api.calls == ["notice", "start"]  # the visitor still read and agreed to the notice
    assert "KNOPF/ENTER = Mitmachen / Join" in screen.shown[0] and lights[0] == "invite" and lights[1] == "press"


def test_a_redrawn_invitation_keeps_a_press_made_on_it():
    ui = WebUI(0)
    ui.view(views.start(invited(17), ""))
    screen = ui.revision
    assert ui.press(screen, "enter")
    ui.view(views.start(invited(14), ""))  # fresh seconds from the next snapshot
    assert ui.revision == screen and list(ui.pending) == ["enter"]
