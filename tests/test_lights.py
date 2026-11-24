"""The status lights (ADR-036). A recording output stands in for the GPIO lines."""
import threading
import time
from pathlib import Path

from photobox_client import views
from photobox_client.kiosk import ENTER, Kiosk
from photobox_client.lights import (
    BLUE,
    CYAN,
    DARK,
    GREEN,
    PATTERNS,
    RED,
    SCREEN_LIGHTS,
    VIOLET,
    WHITE,
    Lights,
    light_for,
    open_lights,
)
from photobox_client.settings import Settings


class Output:
    def __init__(self):
        self.colours, self.closed = [], False
        self.lock = threading.Lock()

    def set(self, colour):
        with self.lock:
            self.colours.append(colour)

    def close(self):
        self.closed = True

    def seen(self):
        with self.lock:
            return list(self.colours)


def until(condition, seconds=2.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not condition():
        time.sleep(0.01)
    return condition()


def test_the_owners_colours():
    assert PATTERNS["ready"] == (GREEN, 1, 0) and PATTERNS["ready_live"] == (VIOLET, 1, 0)  # steady
    assert PATTERNS["press"][0] == GREEN and PATTERNS["press"][2] > 0 and PATTERNS["press"][1] < PATTERNS["done"][1]  # fast
    assert PATTERNS["done"][0] == GREEN and PATTERNS["done"][2] > 0  # blinking slowly
    assert PATTERNS["done_live"][0] == VIOLET and PATTERNS["done_live"][1:] == PATTERNS["done"][1:]
    assert PATTERNS["photo"] == (WHITE, 1, 0) and PATTERNS["making"][0] == BLUE
    assert PATTERNS["live"][0] == VIOLET and PATTERNS["printing"][0] == CYAN
    assert PATTERNS["error"][0] == RED and PATTERNS["off"][0] == DARK
    assert set(SCREEN_LIGHTS.values()) <= set(PATTERNS)


def test_a_live_pairing_shows_violet_while_the_strip_is_made():
    assert light_for(views.making(3, {}, None, "")) == "making"
    assert light_for(views.making(3, {}, {"kind": "live", "de": "", "en": ""}, "")) == "live"
    assert light_for(views.message("A\nB")) is None  # the kiosk names it


def test_violet_at_the_start_while_someone_is_live_at_another_booth():
    quiet = views.start(None, "")
    live = views.start({"message": {"key": "remote_active", "badge": {"de": "x", "en": "y"}, "texts": {}}}, "")
    assert light_for(quiet) == "ready" and light_for(live) == "ready_live"


def test_the_code_screen_blinks_violet_after_a_live_pairing():
    code = views.done("CODE-1", None, "")
    assert light_for(code) == "done" and light_for(code, live_visit=True) == "done_live"


def test_steady_and_blinking_patterns():
    output = Output()
    lights = Lights(output).start()
    try:
        lights.show("error")
        assert until(lambda: output.seen()[-1:] == [RED])
        lights.show("press")  # blinks green fast
        assert until(lambda: output.seen().count(GREEN) >= 2 and DARK in output.seen()[output.seen().index(GREEN):])
    finally:
        lights.stop()
    assert output.seen()[-1] != RED and output.closed


def test_unknown_names_and_none_change_nothing():
    output = Output()
    lights = Lights(output).start()
    try:
        lights.show("done")
        assert until(lambda: output.seen()[-1:] == [GREEN])
        lights.show(None)
        lights.show("disco")
        time.sleep(0.1)
        assert lights.name == "done"
    finally:
        lights.stop()


def test_failing_lights_do_not_stop_anything():
    class Broken(Output):
        def set(self, colour):
            raise OSError("gone")

    lights = Lights(Broken()).start()
    lights.show("error")
    time.sleep(0.05)
    lights.stop()


def test_lights_are_made_only_from_three_gpio_numbers():
    made = []
    lights = open_lights("18,23,24", make=lambda lines: made.append(lines) or Output())
    try:
        assert made == [(18, 23, 24)]
    finally:
        lights.stop()
    assert open_lights("", make=lambda lines: Output()) is None
    assert open_lights("18,23", make=lambda lines: Output()) is None
    assert open_lights("18,23,24", make=lambda lines: (_ for _ in ()).throw(OSError())) is None


class Screen:
    def show(self, text):
        pass


class Keys:
    def __init__(self, *answers):
        self.answers = list(answers)

    def flush(self):
        pass

    def wait(self, accepted, timeout):
        return self.answers.pop(0) if self.answers else None


class Api:
    pool, printing = None, False

    def presence(self):
        return None

    def notice(self):
        return "notice-abc", "NOTICE", None

    def start(self, version, pool=None):
        return "sid", "CODE-1"

    def upload_and_process(self, sid, image):
        pass

    def status(self, sid):
        return {"status": "complete", "job": None}


def visit(*answers, api=None):
    shown = []
    kiosk = Kiosk(api or Api(), lambda: b"jpeg", Screen(), Keys(*answers), sleep=lambda _s: None,
                  clock=iter(range(10_000)).__next__, light=lambda name: name and shown.append(name))
    outcome = kiosk.run_once()
    return outcome, [name for i, name in enumerate(shown) if i == 0 or shown[i - 1] != name]


def test_a_visit_walks_through_the_colours():
    outcome, lights = visit(ENTER, ENTER)
    assert outcome == "complete"
    assert lights == ["ready", "press", "photo", "making", "done"]


def test_a_live_visit_ends_in_blinking_violet():
    class Live(Api):
        def status(self, sid):
            return {"status": "complete", "job": None, "match": {"kind": "live", "partner_label": "Berlin"}}

    outcome, lights = visit(ENTER, ENTER, api=Live())
    assert outcome == "complete" and lights[-1] == "done_live"
    # the next visitor starts plain again
    assert visit(ENTER, ENTER)[1][-1] == "done"


def test_cancelling_goes_back_to_ready_and_failures_are_red():
    assert visit(ENTER, None)[1] == ["ready", "press", "ready"]

    class Down(Api):
        def notice(self):
            import httpx
            raise httpx.ConnectError("down")

    assert visit(ENTER, api=Down())[1] == ["ready", "error"]


def test_the_lights_setting_is_kept(tmp_path):
    path = tmp_path / "kiosk.env"
    Settings(lights="18,23,24", button="25").save(path)
    assert Settings.load(path).lights == "18,23,24" and Settings.load(path).button == "25"


def test_the_kiosk_lets_the_lights_go_dark_when_systemd_stops_it():
    source = (Path(__file__).parent.parent / "src" / "photobox_client" / "kiosk.py").read_text()
    assert "for stop in (signal.SIGTERM, signal.SIGHUP):" in source and "signal.signal(stop, lambda *_args: sys.exit(0))" in source
    assert "atexit.register(lights.stop)" in source
