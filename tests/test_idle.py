"""The booth rests after a while without a press, and one press wakes it (ADR-041)."""
from __future__ import annotations

from photobox_client import views
from photobox_client.kiosk import ENTER, RESTING, SETUP, Kiosk
from photobox_client.lights import light_for
from photobox_client.settings import Settings


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Screen:
    def __init__(self):
        self.states = []

    def view(self, state):
        self.states.append(state)

    def show(self, text):
        raise AssertionError("the browser screen gets states")

    def names(self):
        names = []
        for state in self.states:
            if not names or names[-1] != state["screen"]:
                names.append(state["screen"])
        return names


class Keys:
    """Lets time pass while nobody presses, until the screen named in `press_on` appears."""

    def __init__(self, clock, screen, presses):
        self.clock, self.screen, self.presses = clock, screen, list(presses)
        self.timeouts = []

    def flush(self):
        pass

    def wait(self, accepted, timeout):
        self.timeouts.append((self.screen.states[-1]["screen"], timeout))
        if self.presses and self.screen.states[-1]["screen"] == self.presses[0][0]:
            return self.presses.pop(0)[1]
        self.clock.now += timeout
        if self.clock.now > 3600:
            raise AssertionError("nobody pressed in an hour")
        return None


class Api:
    def __init__(self, snapshot=None):
        self.snapshot = snapshot

    def presence(self):
        return self.snapshot


class Detector:
    def __init__(self):
        self.calls = []

    def resume(self):
        self.calls.append("resume")

    def pause(self):
        self.calls.append("pause")


def booth(presses, idle_seconds=300, snapshot=None, detector=None):
    clock, screen, reports = Clock(), Screen(), []
    keys = Keys(clock, screen, presses)
    kiosk = Kiosk(Api(snapshot), lambda: b"", screen, keys, clock=clock, sleep=lambda _s: None, idle_seconds=idle_seconds,
                  report=lambda *args: reports.append(args), detector=detector)
    return kiosk, clock, screen, keys, reports


def test_the_start_screen_rests_after_five_minutes_and_a_press_brings_it_back():
    kiosk, clock, screen, keys, reports = booth([("resting", ENTER), ("start", ENTER)])
    assert kiosk.start_screen() == ENTER
    assert screen.names() == ["start", "resting", "start"]
    rested = next(i for i, (name, _t) in enumerate(keys.timeouts) if name == "resting")
    assert sum(t for _name, t in keys.timeouts[:rested]) == 300  # exactly five minutes, not a refresh later
    assert ("idle", RESTING) in reports and reports[-1] == ("idle",)


def test_the_wake_up_press_is_not_the_start_press():
    """The press on the resting screen only brings the start screen back; starting takes another press."""
    kiosk, clock, screen, keys, _reports = booth([("resting", ENTER)])
    try:
        kiosk.start_screen()
    except AssertionError:
        pass  # nobody pressed again: the booth went on waiting, and rested again
    assert screen.names()[:3] == ["start", "resting", "start"]


def test_the_timer_starts_again_after_waking():
    kiosk, clock, screen, keys, _reports = booth([("resting", ENTER), ("resting", ENTER), ("start", ENTER)])
    assert kiosk.start_screen() == ENTER
    assert screen.names() == ["start", "resting", "start", "resting", "start"]


def test_the_setup_keys_work_on_the_resting_screen():
    kiosk, *_rest = booth([("resting", SETUP)])
    assert kiosk.start_screen() == SETUP


def test_no_resting_when_switched_off():
    kiosk, clock, screen, keys, _reports = booth([], idle_seconds=0)
    try:
        kiosk.start_screen()
    except AssertionError:
        pass
    assert screen.names() == ["start"] and clock.now > 3600


def test_never_rests_during_an_invitation_from_another_booth():
    snapshot = {"generated_at": "2026-11-26T10:00:00+00:00",
                "message": {"key": "partner_waiting", "until": "2026-11-26T10:00:20+00:00",
                            "texts": {"de": {"hero": "H", "support": "S"}, "en": {"hero": "H", "support": "S"}}},
                "locations": [{"label": "Essen", "invite_until": "2026-11-26T10:00:20+00:00"}]}
    assert views.invitation_seconds(snapshot)  # the snapshot really is an invitation
    kiosk, clock, screen, keys, _reports = booth([], snapshot=snapshot)
    try:
        kiosk.start_screen()
    except AssertionError:
        pass
    assert "resting" not in screen.names()


def test_the_camera_does_not_look_while_resting():
    detector = Detector()
    kiosk, *_rest = booth([("resting", ENTER), ("start", ENTER)], detector=detector)
    kiosk.start_screen()
    # looking on the start screen, not while resting, again after waking, and not after leaving the start screen
    assert detector.calls == ["resume", "pause", "resume", "pause"]


def test_resting_light_flashes_green_or_violet():
    assert light_for(views.resting()) == "rest"
    assert light_for(views.resting(live=True)) == "rest_live"
    assert views.someone_live({"message": {"key": "partner_waiting"}})
    assert not views.someone_live({"message": {"key": "nobody_online"}}) and not views.someone_live(None)


def test_resting_screen_names_the_project_and_both_partners():
    state = views.resting()
    assert state["name"] == "Multisensory in Dialogue and Artistic Practice"
    assert "Universität der Künste Berlin" in state["cooperation"]["de"] and "Folkwang" in state["cooperation"]["de"]
    assert "Berlin University of the Arts" in state["cooperation"]["en"] and "Folkwang" in state["cooperation"]["en"]
    assert "ü" not in state["plain"] and "Folkwang" in state["plain"]


def test_idle_minutes_setting(tmp_path):
    path = tmp_path / "kiosk.env"
    assert Settings.load(path).idle_minutes == 5
    for text, minutes in (("0", 0), ("12", 12), ("soon", 5), ("-3", 5), ("99999", 5)):
        path.write_text(f"PHOTOBOX_SERVER=http://x\nPHOTOBOX_IDLE_MINUTES={text}\n")
        assert Settings.load(path).idle_minutes == minutes
    settings = Settings.load(path)
    settings.idle_minutes = 7
    settings.save(path)
    assert Settings.load(path).idle_minutes == 7
