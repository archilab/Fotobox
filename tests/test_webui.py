"""The browser screen's local server (ADR-028, milestone U2), over a real socket on 127.0.0.1. Synthetic images only."""
import io
import json
import threading
import time
import httpx
import pytest
from PIL import Image
from photobox_client.kiosk import ENTER, ESC, Kiosk
from photobox_client.webui import MAX_PENDING_KEYS, WebUI


@pytest.fixture
def ui():
    server = WebUI().start()
    yield server
    server.stop()


def client(ui, **headers):
    return httpx.Client(base_url=f"http://127.0.0.1:{ui.port}", timeout=5, headers=headers)


def press(ui, key, screen_id=None):
    with client(ui, **{"X-Photobox-Token": ui.token}) as c:
        return c.post("/key", json={"id": ui.revision if screen_id is None else screen_id, "key": key})


def first_state(ui):
    with client(ui) as c, c.stream("GET", "/events", params={"t": ui.token}) as response:
        assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("data: "):
                return json.loads(line[6:])


def png():
    out = io.BytesIO()
    Image.new("RGB", (56, 170), "#777").save(out, format="PNG")
    return out.getvalue()


def test_listens_only_on_this_computer(ui):
    assert ui.server.server_address[0] == "127.0.0.1"


@pytest.mark.parametrize("path", ["/events", "/strip/1"])
def test_content_needs_the_token(ui, path):
    ui.view({"screen": "consent", "text": "t", "plain": "t"})
    with client(ui) as c:
        assert c.get(path).status_code == 403 and c.get(path, params={"t": "wrong"}).status_code == 403


def test_keys_need_the_token(ui):
    ui.view({"screen": "consent", "text": "t", "plain": "t"})
    with client(ui) as c:
        assert c.post("/key", json={"id": ui.revision, "key": "enter"}).status_code == 403
    assert not ui.pending


@pytest.mark.parametrize("host", ["evil.example", "192.168.1.5:8765", "127.0.0.1"])
def test_other_host_names_are_refused(ui, host):
    with client(ui, Host=host, **{"X-Photobox-Token": ui.token}) as c:
        assert c.get("/").status_code == 403 and c.post("/key", json={"id": 0, "key": "enter"}).status_code == 403


@pytest.mark.parametrize("path", ["/prototype.html", "/prototype.js", "/../webui.py", "/fonts/OFL-LICENSE.txt", "/nothing"])
def test_only_the_listed_files_are_served(ui, path):
    with client(ui) as c:
        assert c.get(path, params={"t": ui.token}).status_code == 404


def test_page_and_its_files_are_served_with_strict_headers(ui):
    with client(ui) as c:
        page = c.get("/")
        assert page.status_code == 200 and 'src="app.js"' in page.text
        policy = page.headers["content-security-policy"]
        assert "default-src 'none'" in policy and "unsafe" not in policy and page.headers["cache-control"] == "no-store"
        assert c.get("/fonts/inter-latin-wght-normal.woff2").content[:4] == b"wOF2"
        for name in ("/ui.css", "/screens.js", "/app.js"):
            assert c.get(name).status_code == 200


def test_the_page_receives_the_state_without_the_console_text(ui):
    ui.view({"screen": "message", "de": "Grüße", "en": "Hi", "key": False, "plain": "SECRET-CONSOLE"})
    state = first_state(ui)
    assert state["de"] == "Grüße" and state["id"] == ui.revision and "plain" not in state


def test_keys_reach_the_kiosk_in_order(ui):
    ui.view({"screen": "menu", "text": "x"})
    for key in ("a", "b", "enter"):
        assert press(ui, key).status_code == 200
    assert [ui.read_key(0) for _ in range(3)] == ["a", "b", "enter"]
    assert ui.read_key(0) is None


def test_a_key_for_an_earlier_screen_does_not_count(ui):
    ui.view({"screen": "start", "plain": ""})
    old = ui.revision
    ui.view({"screen": "consent", "text": "Please read", "plain": ""})
    assert press(ui, "enter", screen_id=old).status_code == 409
    assert ui.read_key(0) is None


def test_a_new_screen_discards_waiting_keys(ui):
    ui.view({"screen": "start", "plain": ""})
    press(ui, "enter")
    ui.view({"screen": "consent", "text": "Please read", "plain": ""})
    assert ui.read_key(0) is None


def test_redraws_of_a_live_screen_keep_the_key(ui):
    ui.view({"screen": "making", "clock": "00:01", "plain": ""})
    screen_id = ui.revision
    press(ui, "enter")
    ui.view({"screen": "making", "clock": "00:02", "plain": ""})
    assert ui.revision == screen_id and ui.read_key(0) == "enter"


def test_a_changed_notice_is_a_new_screen(ui):
    ui.view({"screen": "consent", "text": "Old", "plain": ""})
    first = ui.revision
    ui.view({"screen": "consent", "text": "Old", "plain": ""})
    assert ui.revision == first
    ui.view({"screen": "consent", "text": "New", "plain": ""})
    assert ui.revision == first + 1


@pytest.mark.parametrize("body", [{"id": 1, "key": "shutdown"}, {"id": 1, "key": "ab"}, {"id": "1", "key": "a"}, {"id": 1, "key": 7}, {"id": 1, "key": "\x1b"}, {"key": "a"}])
def test_bad_key_requests_are_refused(ui, body):
    ui.view({"screen": "menu", "text": "x"})
    with client(ui, **{"X-Photobox-Token": ui.token}) as c:
        assert c.post("/key", json=body).status_code == 400


def test_an_oversized_request_is_refused(ui):
    with client(ui, **{"X-Photobox-Token": ui.token}) as c:
        assert c.post("/key", content=b"x" * 1000).status_code == 400


def test_pending_keys_are_bounded(ui):
    ui.view({"screen": "menu", "text": "x"})
    for _ in range(MAX_PENDING_KEYS + 10):
        ui.press(ui.revision, "a")
    assert len(ui.pending) == MAX_PENDING_KEYS


def test_wait_ignores_other_keys_and_times_out(ui):
    ui.view({"screen": "menu", "text": "x"})
    ui.press(ui.revision, "x")
    assert ui.wait({ENTER}, 0.05) is None
    threading.Timer(0.05, lambda: ui.press(ui.revision, ENTER)).start()
    assert ui.wait({ENTER}, 2) == ENTER


def test_strip_is_served_only_while_held(ui):
    url = ui.put_image(png())
    with client(ui) as c:
        response = c.get("/" + url)
        assert response.status_code == 200 and response.content == png() and response.headers["cache-control"] == "no-store"
        ui.clear_image()
        assert c.get("/" + url).status_code == 404


def test_damaged_strip_is_refused(ui):
    with pytest.raises(ValueError):
        ui.put_image(b"not an image")


def test_setup_menu_text_becomes_a_plain_screen(ui):
    ui.show("1  WiFi")
    assert first_state(ui)["text"] == "1  WiFi"


class Api:
    def __init__(self, data):
        self.data, self.states = data, ["complete"]

    def presence(self):
        return None

    def notice(self):
        return "v", "NOTICE", None

    def start(self, version, pool=None):
        return "sid", "CODE-1"

    def upload_and_process(self, sid, image):
        pass

    def status(self, sid):
        return {"status": "complete", "job": None}

    def strip(self, sid):
        return self.data


def visitor(ui, *keys, screens):
    """Press each key once the kiosk is showing the expected screen, as a person would."""
    def run():
        for key, name in zip(keys, screens):
            deadline = time.monotonic() + 5
            while (ui.state is None or ui.state["screen"] != name) and time.monotonic() < deadline:
                time.sleep(0.01)
            ui.press(ui.revision, key)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def test_a_whole_visit_through_the_browser_screen(ui):
    shown = []
    real_view = ui.view

    def recording(state):
        shown.append(state["screen"])
        real_view(state)

    ui.view = recording
    kiosk = Kiosk(Api(png()), lambda: b"jpeg", ui, ui, sleep=lambda _s: time.sleep(0.01))
    visitor(ui, ENTER, ENTER, ENTER, screens=["start", "consent", "done"])
    assert kiosk.run_once() == "complete"
    assert shown == ["start", "consent", "countdown", "countdown", "countdown", "flash", "strip", "done"]
    assert ui.image is None  # gone once it has left the screen


def test_cancelling_through_the_browser_screen(ui):
    kiosk = Kiosk(Api(png()), lambda: b"jpeg", ui, ui, sleep=lambda _s: None)
    visitor(ui, ENTER, ESC, ENTER, screens=["start", "consent", "message"])
    assert kiosk.run_once() == "declined"


def test_a_damaged_strip_is_skipped_and_the_visit_ends_normally(ui):
    kiosk = Kiosk(Api(b"broken"), lambda: b"jpeg", ui, ui, sleep=lambda _s: None)
    visitor(ui, ENTER, ENTER, ENTER, screens=["start", "consent", "done"])
    assert kiosk.run_once() == "complete"
