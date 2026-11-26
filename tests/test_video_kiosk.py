"""The hidden video link at the booth (ADR-045), with a stand-in server. No camera, no images."""
from datetime import datetime, timedelta, timezone

from photobox_client import views
from photobox_client.kiosk import ENTER, SETUP, VIDEO, Kiosk
from photobox_client.lights import light_for

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)


def link(status="requested", role="caller", seconds=20, place="Berlin", reason=None):
    return {"link": {"id": "L1", "role": role, "status": status, "until": (NOW + timedelta(seconds=seconds)).isoformat(),
                     "place": place, "end_reason": reason, "link_seconds": 120}, "now": NOW.isoformat()}


class Api:
    def __init__(self, *answers, seconds=120, free=True, snapshot=None):
        self.answers, self.seconds, self.free, self.snapshot = list(answers), seconds, free, snapshot
        self.calls = []

    def presence(self):
        return self.snapshot

    def video_seconds(self):
        return self.seconds

    def video_call(self):
        self.calls.append("call")
        return link() if self.free else None

    def video_link(self, link_id):
        self.calls.append("poll")
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]

    def video_accept(self, link_id):
        self.calls.append("accept")
        return link("connected", "callee", 120)

    def video_end(self, link_id):
        self.calls.append("end")


class Screen:
    def __init__(self):
        self.states = []

    def view(self, state):
        self.states.append(state)

    def show(self, text):
        pass

    def names(self):
        out = []
        for s in self.states:
            if not out or out[-1] != s["screen"]:
                out.append(s["screen"])
        return out


class Keys:
    """Answers each wait in turn; None means time passes."""

    def __init__(self, *answers):
        self.answers, self.accepted = list(answers), []

    def flush(self):
        pass

    def wait(self, accepted, timeout):
        self.accepted.append(set(accepted))
        return self.answers.pop(0) if self.answers else None


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        self.now += 1
        return self.now


def booth(api, *keys):
    screen, holds, reports = Screen(), [], []
    kiosk = Kiosk(api, lambda: b"", screen, Keys(*keys), clock=Clock(), sleep=lambda _s: None,
                  hold_mode=holds.append, report=lambda *a: reports.append(a))
    return kiosk, screen, holds, reports


def test_the_hold_on_the_start_screen_starts_a_call_after_agreeing():
    api = Api(link("requested"), link("connected", seconds=120), link("ended", reason="callee"))
    kiosk, screen, holds, reports = booth(api, VIDEO, ENTER)
    assert kiosk.run_once() == "video done"
    assert screen.names() == ["start", "video_ask", "video_calling", "video_link"]
    assert api.calls[0] == "call" and "end" not in api.calls  # the other side ended it
    assert holds[:2] == [True, False]  # the hold works on the start screen only
    assert ("showing", "video link") in reports


def test_doing_nothing_on_the_notice_calls_nobody():
    api = Api(link())
    kiosk, screen, *_ = booth(api, VIDEO, None)
    assert kiosk.run_once() == "video declined" and api.calls == []


def test_switched_off_the_hold_does_nothing_visible():
    api = Api(link(), seconds=None)
    kiosk, screen, *_ = booth(api, VIDEO)
    assert kiosk.run_once() == "start" and screen.names() == ["start"] and api.calls == []


def test_no_free_booth_says_so():
    api = Api(link(), free=False)
    kiosk, screen, *_ = booth(api, VIDEO, ENTER)
    assert kiosk.run_once() == "video busy" and screen.names()[-1] == "message"


def test_the_caller_can_cancel_while_calling():
    api = Api(link("requested"))
    kiosk, screen, *_ = booth(api, VIDEO, ENTER, ENTER)
    assert kiosk.run_once() == "video cancelled" and api.calls[-1] == "end"


def test_an_unanswered_call_says_so():
    api = Api(link("ended", reason="unanswered"))
    kiosk, screen, *_ = booth(api, VIDEO, ENTER, None)
    assert kiosk.run_once() == "video unanswered" and screen.states[-1]["screen"] == "message"


def test_a_request_from_another_booth_needs_a_press_here():
    snapshot = {"generated_at": NOW.isoformat(), "message": {}, "video": link("requested", "callee", 18, "Mac")["link"]}
    api = Api(link("connected", "callee", 120), link("ended", reason="caller"), snapshot=snapshot)
    kiosk, screen, *_ = booth(api, ENTER)
    assert kiosk.run_once() == "video done"
    assert screen.names() == ["start", "video_request", "video_link"]
    request = next(s for s in screen.states if s["screen"] == "video_request")
    assert request["seconds"] == 18 and "Mac" in request["hero"]["de"] and "Nichts wird aufgezeichnet" in request["notice"]["de"]
    assert "accept" in api.calls


def test_ignoring_a_request_agrees_to_nothing():
    snapshot = {"generated_at": NOW.isoformat(), "message": {}, "video": link("requested", "callee", 18)["link"]}
    api = Api(link(), snapshot=snapshot)
    kiosk, *_ = booth(api, None)
    assert kiosk.run_once() == "video unanswered" and "accept" not in api.calls


def test_either_side_ends_the_link_with_one_press():
    api = Api(link("connected", seconds=120))
    kiosk, *_ = booth(api, VIDEO, ENTER, None, ENTER)  # agree, wait one poll (answered), then press during the link
    assert kiosk.run_once() == "video ended" and api.calls[-1] == "end"


def test_the_setup_keys_still_work():
    kiosk, *_ = booth(Api(link()), SETUP)
    assert kiosk.run_once() == "setup"


def test_notice_says_what_happens_and_for_how_long():
    notice = views.video_notice(120)
    assert notice["de"] == "Bild und Ton gehen live an eine andere Fotobox, bis zu 2 Minuten. Nichts wird aufgezeichnet. Ein Druck beendet es."
    assert "2 minutes" in notice["en"] and "1 Minute" in views.video_notice(45)["de"]


def test_lights_for_the_video_screens():
    assert light_for(views.video_ask(30, 120)) == "press"
    assert light_for(views.video_request("Mac", 20, 120)) == "invite"
    assert light_for(views.video_link("Mac", 120)) == "live"


def test_the_link_screen_says_picture_and_sound_are_not_built_yet():
    state = views.video_link("Mac", 120)
    assert "nächsten Schritt" in state["pending"]["de"]


def test_the_page_and_terminal_turn_the_hold_into_its_own_key():
    from photobox_client.webui import WebUI
    ui = WebUI(0)
    ui.hold()
    assert ui.wait({VIDEO}, 0.1) == VIDEO
