"""The status lights (ADR-036). A recording output stands in for the GPIO lines."""
import threading
import time
from pathlib import Path

from photobox_client import views
from photobox_client.kiosk import ENTER, Kiosk
from photobox_client.lights import (
    FADING,
    BLUE,
    CYAN,
    DARK,
    GREEN,
    PATTERNS,
    RED,
    SCREEN_LIGHTS,
    VIOLET,
    WHITE,
    LgpioOutput,
    Lights,
    default_output,
    fade_level,
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


# Dimming and fading (ADR-044)

class Dimming(Output):
    dims = True


def test_violet_is_the_mix_the_owner_picked():
    assert VIOLET == (0.25, 0.0, 1.0)  # mix 3 of 4 at newbox: red at a quarter, blue full


def test_waiting_making_done_and_resting_fade_and_the_invitation_blinks():
    assert FADING == {"press", "making", "done", "done_live", "rest", "rest_live"}
    assert "invite" not in FADING and PATTERNS["invite"][2] > 0
    assert sum(PATTERNS["press"][1:]) == 1.6  # in step with the round button's pulse on screen (ui.css press-me)
    assert "press-me 1.6s" in (Path(__file__).parent.parent / "src" / "photobox_client" / "ui" / "ui.css").read_text()


def test_a_fade_goes_from_dark_to_full_and_back():
    assert fade_level(0, 2) == 0 and abs(fade_level(1, 2) - 1) < 1e-9 and fade_level(2, 2) < 1e-9
    assert 0 < fade_level(0.5, 2) < 0.5  # squared: slow out of the dark, so it looks even


def test_a_dimming_output_fades_between_levels():
    output = Dimming()
    lights = Lights(output).start()
    try:
        lights.show("making")  # blue, 2 s cycle
        assert until(lambda: any(0.2 < c[2] < 0.8 for c in output.seen()), 1.5)
        assert all(c[0] == 0 and c[1] == 0 for c in output.seen())
    finally:
        lights.stop()


def test_without_dimming_a_fade_blinks():
    output = Output()  # like gpiod: on or off
    lights = Lights(output).start()
    try:
        lights.show("press")
        assert until(lambda: GREEN in output.seen() and DARK in output.seen()[output.seen().index(GREEN):], 2.5)
        assert all(c in (GREEN, DARK) for c in output.seen())
    finally:
        lights.stop()


class FakeLgpio:
    class error(Exception):
        pass

    def __init__(self, busy=()):
        self.calls, self.busy = [], set(busy)

    def gpiochip_open(self, number):
        self.calls.append(("open", number))
        return 7

    def gpio_claim_output(self, handle, line, level):
        if line in self.busy:
            raise self.error("GPIO busy")
        self.calls.append(("claim", line, level))

    def tx_pwm(self, handle, line, frequency, duty):
        self.calls.append(("pwm", line, duty) if frequency else ("pwm", line, 0, 0))

    def gpio_write(self, handle, line, level):
        self.calls.append(("write", line, level))

    def gpio_free(self, handle, line):
        self.calls.append(("free", line))

    def gpiochip_close(self, handle):
        self.calls.append(("close",))


def test_lgpio_output_sets_duty_cycles_and_only_changes():
    fake = FakeLgpio()
    output = LgpioOutput((18, 23, 24), lgpio=fake)
    assert fake.calls[0] == ("open", 0) and [c for c in fake.calls if c[0] == "claim"] == [("claim", 18, 0), ("claim", 23, 0), ("claim", 24, 0)]
    fake.calls.clear()
    output.set(VIOLET)
    assert fake.calls == [("pwm", 18, 25.0), ("write", 24, 1)]  # only the quarter needs PWM; green was already dark
    fake.calls.clear()
    output.set(VIOLET)
    assert fake.calls == []  # a steady light is not restarted
    output.set(GREEN)
    assert fake.calls == [("pwm", 18, 0, 0), ("write", 18, 0), ("write", 23, 1), ("write", 24, 0)]  # PWM stopped first
    output.close()
    assert fake.calls[-1] == ("close",) and ("free", 24) in fake.calls and ("write", 23, 0) in fake.calls


def test_a_busy_line_closes_the_chip_again():
    fake = FakeLgpio(busy={23})
    try:
        LgpioOutput((18, 23, 24), lgpio=fake)
    except FakeLgpio.error:
        pass
    assert fake.calls[-1] == ("close",)


def test_without_lgpio_the_lights_use_gpiod(monkeypatch):
    import sys
    from photobox_client import lights
    monkeypatch.setitem(sys.modules, "lgpio", None)
    made = []
    monkeypatch.setattr(lights, "GpiodOutput", lambda lines: made.append(lines) or Output())
    default_output((18, 23, 24))
    assert made == [(18, 23, 24)]


def test_a_failing_dimming_output_does_not_stop_the_lights():
    class Flaky(Dimming):
        def __init__(self):
            super().__init__()
            self.failed = False

        def set(self, colour):
            if not self.failed:
                self.failed = True
                raise FakeLgpio.error("bad")  # lgpio's own error, not an OSError
            super().set(colour)

    output = Flaky()
    lights = Lights(output).start()
    try:
        lights.show("error")
        assert until(lambda: output.seen()[-1:] == [RED], 2.5)
    finally:
        lights.stop()

