"""The push button that works like ENTER in booth mode (ADR-036). A fake line stands in for the GPIO."""
import json
import os
import re
import threading
import time
from pathlib import Path

import httpx
import pytest

from photobox_client import views
from photobox_client.button import (
    DEBOUNCE_SECONDS,
    STUCK_SECONDS,
    Button,
    PressFilter,
    open_button,
)
from photobox_client.kiosk import ENTER, Kiosk, TerminalKeys, serve
from photobox_client.settings import Settings
from photobox_client.webui import WebUI

UI = Path(__file__).parent.parent / "src" / "photobox_client" / "ui"
# Measured on the box (docs/HARDWARE.md): a press bounces for under a millisecond, a release was clean.
PRESS_BOUNCE = [(True, 0.0), (True, 0.00002), (False, 0.00004), (True, 0.00005), (False, 0.0004), (True, 0.0005)]


def presses(filter_, edges, offset=0.0):
    return sum(filter_.edge(pressed, at + offset) for pressed, at in edges)


def test_a_bouncing_press_counts_once():
    f = PressFilter()
    assert presses(f, PRESS_BOUNCE) == 1
    assert presses(f, [(False, 1.0)]) == 0  # the release
    assert presses(f, PRESS_BOUNCE, offset=2.0) == 1


def test_holding_the_button_is_one_press_and_then_stuck():
    f = PressFilter()
    assert f.edge(True, 0.0) is True
    assert not f.stuck(STUCK_SECONDS - 0.1) and f.stuck(STUCK_SECONDS)
    assert f.edge(False, 10.0) is False and not f.stuck(11.0)


def test_a_tap_shorter_than_the_bounce_window_does_not_swallow_the_next_press():
    f = PressFilter()
    assert f.edge(True, 0.0) is True
    assert f.edge(False, 0.01) is False  # inside the window: ignored as bounce
    f.settle(False, 0.01 + DEBOUNCE_SECONDS)  # the line is quiet and up again
    assert f.edge(True, 1.0) is True


class FakeLine:
    def __init__(self):
        self.queue, self.level, self.closed = [], False, False
        self.ready = threading.Event()

    def push(self, *edges):
        self.queue.extend(edges)
        self.ready.set()

    def wait(self, timeout):
        return self.ready.wait(timeout)

    def edges(self):
        edges, self.queue = self.queue, []
        self.ready.clear()
        return edges

    def pressed(self):
        return self.level

    def close(self):
        self.closed = True


def until(condition, seconds=2.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not condition():
        time.sleep(0.01)
    return condition()


def test_presses_count_only_while_serving_visitors():
    line, got = FakeLine(), []
    button = Button(line, lambda: got.append(1)).start()
    try:
        now = time.monotonic()
        line.push((True, now))
        time.sleep(0.15)
        assert got == []  # not enabled: the setup menu, the start-up screen
        line.push((False, now + 0.2))
        button.enabled.set()
        line.push((True, now + 0.4))
        assert until(lambda: got == [1])
    finally:
        button.stop()
    assert line.closed


def test_a_failing_line_does_not_stop_the_button():
    class Flaky(FakeLine):
        calls = 0

        def wait(self, timeout):
            self.calls += 1
            if self.calls == 1:
                raise OSError("gone")
            return super().wait(timeout)

    line, got = Flaky(), []
    button = Button(line, lambda: got.append(1))
    button.enabled.set()
    button.start()
    try:
        line.push((True, time.monotonic()))
        assert until(lambda: got == [1], seconds=3)
    finally:
        button.stop()


def test_serve_enables_the_button_only_during_a_visit():
    seen = []

    class FakeKiosk:
        def run_once(self):
            seen.append(button.enabled.is_set())
            raise RuntimeError("visit failed")

    button = Button(FakeLine(), lambda: None)
    with pytest.raises(RuntimeError):
        serve(FakeKiosk(), button)
    assert seen == [True] and not button.enabled.is_set()


def test_no_button_without_a_gpio_or_without_the_library():
    assert open_button("", lambda: None) is None
    assert open_button("not a number", lambda: None) is None


def test_the_text_screen_takes_the_button_as_enter():
    read_end, write_end = os.pipe()
    keys = TerminalKeys(read_end)
    threading.Timer(0.1, keys.button).start()
    started = time.monotonic()
    assert keys.read_key(2) == ENTER and time.monotonic() - started < 1
    os.close(write_end)


def test_the_browser_screen_gets_the_press_tied_to_the_screen_showing():
    ui = WebUI(0).start()
    try:
        ui.view(views.message("A\nB", key=True))
        screen = ui.revision
        events = []

        def listen():
            with httpx.Client(base_url=f"http://127.0.0.1:{ui.port}", headers={"Host": f"127.0.0.1:{ui.port}"}) as c, \
                    c.stream("GET", "/events", params={"t": ui.token}) as response:
                kind = None
                for line in response.iter_lines():
                    if line.startswith("event: "):
                        kind = line[7:]
                    elif line.startswith("data: ") and kind:
                        events.append((kind, json.loads(line[6:])))
                        if kind == "button":
                            return

        thread = threading.Thread(target=listen, daemon=True)
        thread.start()
        assert until(lambda: events)
        ui.button()
        thread.join(3)
        assert events[-1] == ("button", {"id": screen})
        assert ui.pending == type(ui.pending)()  # the page decides; nothing counted as ENTER yet
    finally:
        ui.stop()


def test_the_page_applies_the_notice_rule_and_the_screen_id_to_the_button():
    app = (UI / "app.js").read_text(encoding="utf-8")
    assert re.search(r'addEventListener\("button".*?JSON\.parse\(event\.data\)\.id !== screenId\) return;'
                     r'.*?canConfirm\(\)\) return send\("enter"\);.*?page\(1\)', app, flags=re.DOTALL)


def test_screens_name_the_button_when_there_is_one():
    screens = (UI / "screens.js").read_text(encoding="utf-8")
    assert 'buttonLabel = state.button ? "KNOPF" : null' in screens
    shown = []

    class Screen:
        def show(self, text):
            shown.append(text)

    kiosk = Kiosk(None, lambda: b"", Screen(), None, button=True)
    kiosk.present(views.consent("NOTICE", None, 45))
    assert "KNOPF/ENTER = Ich stimme zu" in shown[-1]
    Kiosk(None, lambda: b"", Screen(), None).present(views.consent("NOTICE", None, 45))
    assert "KNOPF" not in shown[-1]


def test_the_button_setting_is_kept(tmp_path):
    path = tmp_path / "kiosk.env"
    Settings(server="http://x:8880", button="25").save(path)
    assert Settings.load(path).button == "25"
    assert Settings.load(tmp_path / "missing").button == ""


def test_the_installer_installs_the_gpio_library():
    assert "'.[client-camera,client-gpio,client-screen]'" in (Path(__file__).parent.parent / "deploy" / "pi" / "install.sh").read_text()


# The hidden long press on the start screen (ADR-045)

class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def holding_button():
    line, clock, got = FakeLine(), Clock(), []
    button = Button(line, lambda: got.append("press"), clock=clock, on_hold=lambda: got.append("hold"))
    button.enabled.set()
    button.holding.set()
    return line, clock, got, button.start()


def test_on_the_start_screen_a_press_counts_when_let_go():
    line, clock, got, button = holding_button()
    try:
        line.push((True, clock.now))
        time.sleep(0.15)
        assert got == []  # still down: it could become a hold
        clock.now += 0.3
        line.level = False
        line.push((False, clock.now))
        assert until(lambda: got == ["press"])
    finally:
        button.stop()


def test_holding_five_seconds_is_a_hold_and_letting_go_is_not_a_press():
    from photobox_client.button import HOLD_SECONDS
    line, clock, got, button = holding_button()
    try:
        line.level = True
        line.push((True, clock.now))
        time.sleep(0.1)
        clock.now += HOLD_SECONDS - 0.5
        time.sleep(0.15)
        assert got == []
        clock.now += 0.6
        assert until(lambda: got == ["hold"])
        clock.now += 3  # still holding: no second hold
        time.sleep(0.15)
        line.level = False
        line.push((False, clock.now))
        time.sleep(0.2)
        assert got == ["hold"]
    finally:
        button.stop()


def test_leaving_the_start_screen_mid_press_counts_nothing():
    line, clock, got, button = holding_button()
    try:
        line.level = True
        line.push((True, clock.now))
        time.sleep(0.1)
        button.holding.clear()  # the screen changed, such as an invitation arriving
        time.sleep(0.1)
        clock.now += 6
        line.level = False
        line.push((False, clock.now))
        time.sleep(0.2)
        assert got == []
    finally:
        button.stop()


def test_elsewhere_a_press_counts_at_once_as_before():
    line, clock, got, button = holding_button()
    button.holding.clear()
    try:
        line.level = True
        line.push((True, clock.now))
        assert until(lambda: got == ["press"])
    finally:
        button.stop()
