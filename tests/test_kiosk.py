"""The keyboard kiosk and on-screen consent (ADR-020). Synthetic images only; no real camera or terminal."""
import io
import os
import pytest
import httpx
from PIL import Image
from conftest import process
from photobox_client import kiosk as kiosk_module
from photobox_client.camera import CameraError
from photobox_client.kiosk import ENTER, ESC, Api, Kiosk, TerminalKeys, TerminalScreen
from photobox_server import jobs
from photobox_server.core import db_connect
from photobox_server.pool import import_seed

NOTICE = "FOTOBOX NOTICE\nYour photo is kept for one hour.\nENTER = agree, ESC = cancel"


def jpeg(color="#878787"):
    out = io.BytesIO()
    Image.new("RGB", (640, 480), color).save(out, format="JPEG")
    return out.getvalue()


@pytest.fixture
def notice(tmp_path, monkeypatch):
    path = tmp_path / "consent-notice.txt"
    path.write_text(NOTICE)
    monkeypatch.setenv("PHOTOBOX_CONSENT_NOTICE_FILE", str(path))
    return path


def current_version(client):
    return client.get("/api/v1/consent-notice").json()["version"]


# Server: the notice

def test_notice_is_served_with_a_version_from_its_text(client, notice):
    first = client.get("/api/v1/consent-notice").json()
    assert first["text"] == NOTICE and first["version"].startswith("notice-")
    notice.write_text(NOTICE + "\nchanged")
    assert client.get("/api/v1/consent-notice").json()["version"] != first["version"]


@pytest.mark.parametrize("content,message", [(None, "no consent notice"), ("", "empty"), ("Responsible: TODO-OPERATOR", "TODO-OPERATOR"), ("x" * 4001, "longer")])
def test_unusable_notice_is_refused(client, notice, content, message):
    if content is None:
        notice.unlink()
    else:
        notice.write_text(content)
    response = client.get("/api/v1/consent-notice")
    assert response.status_code == 503 and message in response.json()["detail"]


def test_example_notice_cannot_be_used_unedited(client, monkeypatch):
    example = os.path.join(os.path.dirname(__file__), "..", "consent-notice.example.txt")
    monkeypatch.setenv("PHOTOBOX_CONSENT_NOTICE_FILE", example)
    assert client.get("/api/v1/consent-notice").status_code == 503


def test_notice_needs_a_device_token(anonymous_client, notice):
    assert anonymous_client.get("/api/v1/consent-notice").status_code == 401


# Server: consent given on the screen

def screen_consent(client, sid, version, **extra):
    body = {"processing": True, "method": "participant_screen", "notice_version": version, **extra}
    return client.post(f"/api/v1/sessions/{sid}/consent", json=body)


def test_screen_consent_records_the_notice_version_and_method(client, notice):
    sid = client.post("/api/v1/sessions").json()["id"]
    version = current_version(client)
    response = screen_consent(client, sid, version)
    assert response.status_code == 200 and response.json()["withdrawal_code"]
    with db_connect() as db:
        record = db.execute("SELECT * FROM consent_records WHERE session_id=?", (sid,)).fetchone()
    assert record["provenance"] == "participant_screen" and record["policy_version"] == version and record["purpose"] == "processing"


def test_screen_consent_needs_no_operator_attestation(client, notice):
    sid = client.post("/api/v1/sessions").json()["id"]
    assert screen_consent(client, sid, current_version(client), documented_externally=False).status_code == 200


def test_consent_to_an_outdated_notice_is_refused(client, notice):
    sid = client.post("/api/v1/sessions").json()["id"]
    old = current_version(client)
    notice.write_text(NOTICE + "\nnew paragraph")
    assert screen_consent(client, sid, old).status_code == 409
    assert client.get(f"/api/v1/sessions/{sid}").json()["status"] == "awaiting_consent"


@pytest.mark.parametrize("change,code", [({"processing": False}, 403), ({"pool": True}, 403), ({"notice_version": None}, 409)])
def test_incomplete_screen_consent_is_refused(client, notice, change, code):
    sid = client.post("/api/v1/sessions").json()["id"]
    assert screen_consent(client, sid, current_version(client), **change).status_code == code
    with db_connect() as db:
        assert db.execute("SELECT COUNT(*) FROM consent_records").fetchone()[0] == 0


def test_screen_consent_without_a_notice_is_refused(client, notice):
    sid = client.post("/api/v1/sessions").json()["id"]
    version = current_version(client)
    notice.unlink()
    assert screen_consent(client, sid, version).status_code == 503


def test_repeated_screen_consent_succeeds_but_a_different_method_does_not(client, notice):
    sid = client.post("/api/v1/sessions").json()["id"]
    version = current_version(client)
    screen_consent(client, sid, version)
    assert screen_consent(client, sid, version).status_code == 200
    operator = {"processing": True, "documented_externally": True}
    assert client.post(f"/api/v1/sessions/{sid}/consent", json=operator).status_code == 409


def test_operator_attestation_still_works(client):
    sid = client.post("/api/v1/sessions").json()["id"]
    assert client.post(f"/api/v1/sessions/{sid}/consent", json={"processing": True, "documented_externally": True}).status_code == 200


# Kiosk flow, with fakes

class FakeScreen:
    def __init__(self):
        self.shown = []

    def show(self, text):
        self.shown.append(text)


class FakeKeys:
    """Answers each question in turn and records how often pending keys were discarded."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.flushes = 0
        self.questions = []

    def flush(self):
        self.flushes += 1

    def wait(self, accepted, timeout):
        self.questions.append((accepted, timeout))
        return self.answers.pop(0) if self.answers else None


class FakeApi:
    def __init__(self, states=("queued", "processing", "complete"), fail=None):
        self.calls = []
        self.states = list(states)
        self.fail = fail

    def presence(self):
        return None

    def notice(self):
        self.calls.append("notice")
        if self.fail == "notice":
            raise httpx.ConnectError("down")
        return "notice-abc", "NOTICE TEXT", self.pool

    pool = None

    def start(self, version, pool=None):
        self.calls.append(("start", version) if pool is None else ("start", version, pool["version"]))
        return "sid", "CODE-1"

    def upload_and_process(self, sid, image):
        self.calls.append(("upload", sid, len(image)))
        if self.fail == "upload":
            raise httpx.ConnectError("down")

    def status(self, sid):
        status = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {"status": status, "job": {"frames_done": 2, "frames_total": 4} if status == "processing" else None}


def make(api, keys, camera=lambda: b"jpeg-bytes", **kwargs):
    screen = FakeScreen()
    clock = iter(range(0, 10_000))
    return Kiosk(api, camera, screen, keys, sleep=lambda _s: None, clock=lambda: next(clock), **kwargs), screen


def test_two_presses_record_consent_then_take_the_photo_and_show_the_code():
    taken = []
    api = FakeApi()
    kiosk, screen = make(api, FakeKeys(ENTER, ENTER, ENTER), camera=lambda: taken.append(1) or b"jpeg-bytes")
    assert kiosk.run_once() == "complete"
    assert api.calls == ["notice", ("start", "notice-abc"), ("upload", "sid", 10)]
    assert taken == [1]
    assert "NOTICE TEXT" in screen.shown[1]
    assert any("Look at the camera" in s for s in screen.shown)
    assert "CODE-1" in screen.shown[-1]


def test_nothing_happens_before_the_notice_is_confirmed():
    api = FakeApi()
    kiosk, _ = make(api, FakeKeys(ENTER, ESC, ENTER), camera=lambda: pytest.fail("photo taken"))
    assert kiosk.run_once() == "declined"
    assert api.calls == ["notice"]


def test_notice_timeout_counts_as_no():
    api = FakeApi()
    keys = FakeKeys(ENTER, None)
    kiosk, screen = make(api, keys, camera=lambda: pytest.fail("photo taken"))
    assert kiosk.run_once() == "declined"
    assert keys.questions[1] == ({ENTER}, kiosk_module.CONSENT_SECONDS)
    assert "No photo was taken" in screen.shown[-1]


def test_pending_presses_are_discarded_before_every_question():
    keys = FakeKeys(ENTER, ENTER)
    kiosk, _ = make(FakeApi(), keys)
    kiosk.run_once()
    assert keys.flushes == len(keys.questions) == 3


def test_generation_timer_counts_up_with_frame_progress():
    kiosk, screen = make(FakeApi(states=("queued", "processing", "processing", "complete")), FakeKeys(ENTER, ENTER, ENTER))
    kiosk.run_once()
    timers = [s for s in screen.shown if "being made" in s]
    assert len(timers) == 3
    assert "00:0" in timers[0] and "Queued" in timers[0] and "Bild 2 von 4" in timers[1]
    assert timers[0] != timers[1]


@pytest.mark.parametrize("ending", ["failed", "withdrawn"])
def test_failed_generation_is_explained(ending):
    kiosk, screen = make(FakeApi(states=("processing", ending)), FakeKeys(ENTER, ENTER, ENTER))
    assert kiosk.run_once() == "generation failed"
    assert "ask the staff" in screen.shown[-1]


def test_long_generation_stops_waiting():
    kiosk, screen = make(FakeApi(states=("processing",)), FakeKeys(ENTER, ENTER, ENTER), wait_seconds=5)
    assert kiosk.run_once() == "generation failed"


def test_camera_failure_is_explained():
    def broken():
        raise CameraError("no frame")

    kiosk, screen = make(FakeApi(), FakeKeys(ENTER, ENTER, ENTER), camera=broken)
    assert kiosk.run_once() == "camera failed"
    assert "camera failed" in screen.shown[-1]


@pytest.mark.parametrize("where,ending", [("notice", "not ready"), ("upload", "network failed")])
def test_server_problems_are_explained(where, ending):
    kiosk, screen = make(FakeApi(fail=where), FakeKeys(ENTER, ENTER, ENTER))
    assert kiosk.run_once() == ending


def test_test_mode_is_visible_on_every_screen():
    kiosk, screen = make(FakeApi(), FakeKeys(ENTER, ENTER, ENTER), test_mode=True)
    kiosk.run_once()
    assert all(s.startswith("TEST MODE") for s in screen.shown)


# Kiosk against the real server

def test_kiosk_runs_a_whole_session_against_the_server(client, notice):
    with db_connect() as db:
        import_seed(db, jpeg("#222222"), "SYNTHETIC")
    api = Api(client)

    def status_then_run_jobs(sid, _real=api.status):
        jobs.run_pending()
        return _real(sid)

    api.status = status_then_run_jobs
    kiosk, screen = make(api, FakeKeys(ENTER, ENTER, ENTER), camera=jpeg)
    assert kiosk.run_once() == "complete"
    code = screen.shown[-1].split("code:\n\n")[1].split()[0]
    with db_connect() as db:
        record = db.execute("SELECT provenance, policy_version FROM consent_records").fetchone()
        sid = db.execute("SELECT id FROM sessions").fetchone()[0]
    assert tuple(record) == ("participant_screen", current_version(client))
    assert client.get(f"/api/v1/sessions/{sid}").json()["status"] == "complete"
    from photobox_server.withdrawal import session_for_code
    with db_connect() as db:
        assert session_for_code(db, code) == sid


# Terminal parts

def test_terminal_keys_read_enter_and_esc_and_ignore_other_keys():
    leader, follower = os.openpty()
    with TerminalKeys(follower) as keys:
        os.write(leader, b"x")
        os.write(leader, b"\r")
        assert keys.wait({ENTER, ESC}, 1) == ENTER
        os.write(leader, b"\x1b[A")
        assert keys.wait({ENTER, ESC}, 0.3) is None
        os.write(leader, b"\x1b")
        assert keys.wait({ENTER, ESC}, 1) == ESC
        os.write(leader, b"\r")
        assert keys.wait({ESC}, 0.3) is None
    os.close(leader)
    os.close(follower)


def test_terminal_flush_discards_earlier_presses():
    leader, follower = os.openpty()
    with TerminalKeys(follower) as keys:
        os.write(leader, b"\r\r")
        keys.flush()
        assert keys.wait({ENTER}, 0.3) is None
    os.close(leader)
    os.close(follower)


def test_terminal_screen_clears_and_wraps():
    out = io.StringIO()
    TerminalScreen(out).show("word " * 60)
    text = out.getvalue()
    assert text.startswith("\x1b[?25l\x1b[2J\x1b[H") and text.count("\n") > 2


def test_terminal_screen_keeps_clear_of_the_edges_on_a_large_console(monkeypatch):
    monkeypatch.setattr(kiosk_module.shutil, "get_terminal_size", lambda _default: os.terminal_size((240, 67)))
    out = io.StringIO()
    TerminalScreen(out).show("FOTOBOX\n" + "word " * 60)
    lines = out.getvalue().removeprefix("\x1b[?25l\x1b[2J\x1b[H").split("\n")
    assert lines[:6] == [""] * 6
    text_lines = [line for line in lines if line.strip()]
    assert all(line.startswith(" " * 20) for line in text_lines)
    assert max(len(line) for line in text_lines) <= 20 + 90


# The finished strip on the screen

class FakeDisplay:
    size = (1920, 1080)

    def __init__(self):
        self.shown = []

    def show(self, image):
        self.shown.append(image)


def png(size=(560, 1703)):
    out = io.BytesIO()
    Image.linear_gradient("L").resize(size).convert("RGB").save(out, "PNG")
    return out.getvalue()


class StripApi(FakeApi):
    def __init__(self, strip=None, **kwargs):
        super().__init__(**kwargs)
        self.strip_png = strip

    def strip(self, sid):
        self.calls.append(("strip", sid))
        if self.strip_png is None:
            raise httpx.ConnectError("down")
        return self.strip_png


def test_finished_strip_is_shown_for_twenty_seconds_before_the_code_screen():
    display = FakeDisplay()
    pauses = []
    api = StripApi(strip=png())
    screen = FakeScreen()
    kiosk = Kiosk(api, lambda: b"jpeg", screen, FakeKeys(ENTER, ENTER, ENTER), sleep=pauses.append, clock=iter(range(10_000)).__next__, display=display)
    assert kiosk.run_once() == "complete"
    assert ("strip", "sid") in api.calls and len(display.shown) == 1
    assert display.shown[0].size == (1920, 1080)
    assert 20 in pauses
    assert "CODE-1" in screen.shown[-1]


def test_without_a_display_the_flow_still_ends_with_the_code():
    api = StripApi(strip=png())
    kiosk, screen = make(api, FakeKeys(ENTER, ENTER, ENTER))
    assert kiosk.run_once() == "complete"
    assert ("strip", "sid") not in api.calls and "CODE-1" in screen.shown[-1]


@pytest.mark.parametrize("strip", [None, b"not an image"])
def test_a_strip_that_cannot_be_shown_is_skipped(strip):
    display = FakeDisplay()
    kiosk, screen = make(StripApi(strip=strip), FakeKeys(ENTER, ENTER, ENTER), display=display)
    assert kiosk.run_once() == "complete"
    assert display.shown == [] and "CODE-1" in screen.shown[-1]


def test_strip_time_can_be_configured():
    pauses = []
    kiosk = Kiosk(StripApi(strip=png()), lambda: b"jpeg", FakeScreen(), FakeKeys(ENTER, ENTER, ENTER), sleep=pauses.append, clock=iter(range(10_000)).__next__, display=FakeDisplay(), strip_seconds=7)
    kiosk.run_once()
    assert 7 in pauses and 20 not in pauses


# The withdrawal code on a public screen

def finishing(answers):
    api = FakeApi(states=("complete",))
    keys = FakeKeys(ENTER, ENTER, *answers)
    kiosk, screen = make(api, keys)
    assert kiosk.run_once() == "complete"
    return keys.questions[2:], screen


def test_the_code_stays_for_ten_seconds_without_a_key():
    questions, screen = finishing([None])
    assert questions == [({ENTER}, 10)] and "CODE-1" in screen.shown[-1]


def test_enter_gives_the_visitor_more_time():
    questions, _ = finishing([ENTER, ENTER, None])
    assert questions == [({ENTER}, 10)] * 3


def test_the_code_never_stays_longer_than_a_minute():
    questions, _ = finishing([ENTER] * 20)
    assert len(questions) == 6  # ten seconds, then five more of them


def test_the_strip_screen_does_not_show_the_code():
    from photobox_client import views
    assert views.strip("strip/1", None)["code"] is None
    api = StripApi(png(), states=("complete",))
    display = FakeDisplay()
    keys = FakeKeys(ENTER, ENTER, None)
    kiosk, _ = make(api, keys, display=display)
    captured = {}
    original = kiosk_module.result_screen
    kiosk_module.result_screen = lambda strip, code, size: captured.setdefault("code", code) or original(strip, None, size)
    try:
        kiosk.run_once()
    finally:
        kiosk_module.result_screen = original
    assert captured == {"code": None}
