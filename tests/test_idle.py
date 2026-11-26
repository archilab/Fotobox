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


# The QR code (ADR-041, I2)

def _decode(rows):
    """Reads rows of modules back the way a phone would, from a picture with the quiet zone."""
    import numpy as np
    cv2 = __import__("pytest").importorskip("cv2")
    size, scale = len(rows) + 8, 10
    image = np.full((size, size), 255, dtype=np.uint8)
    for y, row in enumerate(rows):
        for x, module in enumerate(row):
            if module == "1":
                image[y + 4, x + 4] = 0
    image = cv2.resize(image, (size * scale, size * scale), interpolation=cv2.INTER_NEAREST)
    return cv2.QRCodeDetector().detectAndDecode(image)[0]


def test_the_qr_code_reads_as_the_project_website():
    from photobox_client import qr
    rows = qr.modules(qr.DEFAULT_ADDRESS)
    assert rows and all(len(row) == len(rows) and set(row) <= {"0", "1"} for row in rows)
    assert _decode(rows) == "https://www.midap.space"


def test_the_resting_screen_carries_the_code_and_the_address_as_text():
    state = views.resting(False, "https://www.midap.space/")
    assert state["address"]["text"] == "midap.space" and state["address"]["modules"]
    assert "midap.space" in state["plain"]
    assert "address" not in views.resting()


def test_without_segno_the_address_is_still_shown(monkeypatch):
    import sys
    from photobox_client import qr
    qr.modules.cache_clear()
    monkeypatch.setitem(sys.modules, "segno", None)  # import fails as if it were not installed
    try:
        state = views.resting(False, "https://example.org/about")
        assert state["address"]["modules"] is None and state["address"]["text"] == "example.org/about"
    finally:
        qr.modules.cache_clear()


def test_the_website_setting(tmp_path):
    from photobox_client.qr import DEFAULT_ADDRESS
    path = tmp_path / "kiosk.env"
    assert Settings.load(path).idle_address == DEFAULT_ADDRESS
    for text, address in (("https://udk-berlin.de/x", "https://udk-berlin.de/x"), ("ftp://x.org", DEFAULT_ADDRESS),
                          ("midap.space", DEFAULT_ADDRESS), ("https://a b.org", DEFAULT_ADDRESS), ("https://" + "a" * 300, DEFAULT_ADDRESS)):
        path.write_text(f"PHOTOBOX_IDLE_URL={text}\n")
        assert Settings.load(path).idle_address == address
    settings = Settings.load(path)
    settings.idle_address = "https://folkwang-uni.de"
    settings.save(path)
    assert Settings.load(path).idle_address == "https://folkwang-uni.de"


def test_the_kiosk_shows_its_address_while_resting():
    clock, screen = Clock(), Screen()
    kiosk = Kiosk(Api(), lambda: b"", screen, Keys(clock, screen, [("resting", SETUP)]), clock=clock, sleep=lambda _s: None,
                  idle_seconds=60, idle_address="https://www.midap.space")
    assert kiosk.start_screen() == SETUP
    assert screen.states[-1]["address"]["text"] == "midap.space"
