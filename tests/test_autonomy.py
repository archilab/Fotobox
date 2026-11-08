"""Running unattended on the Pi (ADR-032). No real watchdog, browser or camera."""
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import httpx
import pytest
from fastapi.testclient import TestClient
from photobox_client.browser import FROZEN_SECONDS, START_GRACE_SECONDS, BrowserWatch
from photobox_client.camera import reset_usb
from photobox_client.health import WRITE_EVERY_SECONDS, Counters, Heartbeat, collect
from photobox_client.kiosk import ENTER, Kiosk, StatusReporter
from photobox_client.setup_menu import Check, requires_setup
from photobox_client.webui import WebUI
from photobox_server import dashboard
from photobox_server.core import db_connect
from photobox_server.dashboard.security import Sessions, set_password

PI = Path(__file__).parent.parent / "deploy" / "pi"


# Not waiting at a PIN prompt for nobody

@pytest.mark.parametrize("check,expected", [
    (Check("Network", False, "not connected"), False),
    (Check("Server", False, "http://mac:8880 not reachable"), False),
    (Check("Server", False, "no address set"), True),
    (Check("Pairing", False, "not paired"), True),
    (Check("Pairing", False, "token not accepted"), True),
    (Check("Consent notice", False, "missing", essential=False), False),
    (Check("Pairing", True, "paired"), False),
])
def test_only_what_a_person_must_fix_opens_the_menu(check, expected):
    assert requires_setup([check]) is expected


# Signs of life

class Tick:
    now = 100.0

    def __call__(self):
        return self.now


def test_heartbeat_writes_at_most_every_few_seconds(tmp_path):
    clock, path = Tick(), tmp_path / "beat"
    heartbeat = Heartbeat(path, clock)
    heartbeat.beat()
    assert path.read_text() == "idle"
    path.write_text("old")
    heartbeat.beat()
    assert path.read_text() == "old"
    clock.now += WRITE_EVERY_SECONDS
    heartbeat.beat()
    assert path.read_text() == "idle"


def test_heartbeat_says_what_the_kiosk_is_doing(tmp_path):
    heartbeat = Heartbeat(tmp_path / "beat")
    heartbeat.set_state("generating")
    assert (tmp_path / "beat").read_text() == "generating"


def test_a_failing_heartbeat_does_not_stop_the_booth(tmp_path):
    heartbeat = Heartbeat(tmp_path / "missing" / "beat")
    heartbeat.beat()
    heartbeat.set_state("idle")


def test_hold_keeps_beating_during_the_setup_menu(tmp_path, monkeypatch):
    from photobox_client import health
    monkeypatch.setattr(health, "HOLD_EVERY_SECONDS", 0.01)
    path = tmp_path / "beat"
    heartbeat = Heartbeat(path)

    def menu():
        path.unlink()
        deadline = time.monotonic() + 3
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.005)
        assert path.exists()

    heartbeat.hold(menu)


def test_the_start_screen_beats_even_when_nothing_changes():
    beats = []

    class Keys:
        answers = [None, None, ENTER]

        def flush(self):
            pass

        def wait(self, accepted, timeout):
            return self.answers.pop(0)

    class Feed:
        connected = True

        def latest(self):
            return 1, {}

    class Screen:
        def show(self, text):
            pass

    Kiosk(object(), lambda: b"", Screen(), Keys(), feed=Feed(), heartbeat=lambda: beats.append(1)).start_screen()
    assert len(beats) >= 3


# A frozen browser

class Process:
    def __init__(self):
        self.running, self.terminated = True, False

    def poll(self):
        return None if self.running else 0

    def terminate(self):
        self.running, self.terminated = False, True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.running = False


def watching(age, *, started_ago=START_GRACE_SECONDS + 1):
    process, restarts = Process(), []
    clock = Tick()
    w = BrowserWatch("b", "u", popen=lambda *_a, **_k: process, clock=clock, sleep=lambda _s: setattr(clock, "now", clock.now + 1), alive_age=lambda: age, on_restart=lambda: restarts.append(1))
    w.process = process
    return w, process, clock, restarts


def test_a_frozen_page_ends_the_browser():
    w, process, clock, _ = watching(FROZEN_SECONDS + 1)
    w._supervise(clock.now - START_GRACE_SECONDS - 1)
    assert process.terminated


def test_a_live_page_is_left_alone():
    w, process, clock, _ = watching(1.0)

    def stop_soon(_seconds):
        w.stopping = True

    w.sleep = stop_soon
    w._supervise(clock.now - START_GRACE_SECONDS - 1)
    assert not process.terminated


def test_a_starting_browser_gets_time_before_it_is_judged():
    w, process, clock, _ = watching(FROZEN_SECONDS + 1)

    def stop_soon(_seconds):
        w.stopping = True

    w.sleep = stop_soon
    w._supervise(clock.now)
    assert not process.terminated


def test_a_page_that_has_never_spoken_is_not_frozen():
    w, process, clock, _ = watching(None)

    def stop_soon(_seconds):
        w.stopping = True

    w.sleep = stop_soon
    w._supervise(clock.now - START_GRACE_SECONDS - 1)
    assert not process.terminated


def test_restarts_are_counted():
    restarts = []
    finished = Process()
    finished.running = False
    w = BrowserWatch("b", "u", popen=lambda *_a, **_k: finished, on_failure=lambda: None, sleep=lambda _s: None, on_restart=lambda: restarts.append(1))
    w._watch()
    assert len(restarts) == 4


def test_the_page_can_say_it_is_alive_only_with_the_token():
    ui = WebUI().start()
    try:
        assert ui.alive_age() is None
        with httpx.Client(base_url=f"http://127.0.0.1:{ui.port}", timeout=5) as c:
            assert c.post("/alive").status_code == 403
            assert ui.alive_age() is None
            assert c.post("/alive", headers={"X-Photobox-Token": ui.token}).status_code == 204
        assert ui.alive_age() < 1
    finally:
        ui.stop()


# The camera

def test_camera_reset_uses_the_cameras_usb_ids(tmp_path, monkeypatch):
    import photobox_client.camera as camera
    (tmp_path / "video0" / "device").mkdir(parents=True)
    usb = tmp_path / "video0"
    (usb / "idVendor").write_text("046d\n")
    (usb / "idProduct").write_text("085e\n")
    real_path = camera.Path

    def fake(*parts):
        path = real_path(*parts)
        return real_path(str(path).replace("/sys/class/video4linux", str(tmp_path))) if str(path).startswith("/sys/") else path

    monkeypatch.setattr(camera, "Path", fake)
    monkeypatch.setattr(camera, "resolve_source", lambda _value, _dir: "/dev/video0")
    calls = []
    assert reset_usb("cam", lambda command, **_k: calls.append(command) or subprocess.CompletedProcess(command, 0)) is True
    assert calls == [["sudo", "-n", "usbreset", "046d:085e"]]


def test_camera_reset_failure_is_harmless():
    assert reset_usb("/dev/does-not-exist") is False


# What the operator sees

def test_health_holds_machine_figures_only():
    def vcgencmd(*_a, **_k):
        return subprocess.CompletedProcess([], 0, stdout="throttled=0x50000\n")

    counters = Counters("browser")
    counters.camera_failures = 2
    health = collect(counters, runner=vcgencmd)
    assert set(health) == {"temperature_c", "throttled", "disk_free_mb", "memory_available_mb", "uptime_seconds", "screen", "browser_restarts", "camera_failures"}
    assert health["throttled"] is True and health["camera_failures"] == 2 and health["screen"] == "browser"


def test_health_works_where_vcgencmd_is_missing():
    def missing(*_a, **_k):
        raise FileNotFoundError()
    assert collect(Counters(), runner=missing)["throttled"] is None


def test_status_reports_carry_health():
    sent = threading.Event()
    bodies = []

    def post(body):
        bodies.append(body)
        sent.set()

    reporter = StatusReporter(post, health=lambda: {"screen": "browser"}).start()
    assert sent.wait(3)
    reporter.stop()
    assert bodies[0]["health"] == {"screen": "browser"}


def report(c, **health):
    return c.post("/api/v1/devices/status", json={"state": "idle", "health": health})


@pytest.fixture
def dash(anonymous_client):
    dashboard.app.state.sessions = Sessions()
    browser = TestClient(dashboard.app, base_url="http://127.0.0.1:8001", follow_redirects=False)
    with db_connect() as db:
        set_password(db, "correct horse battery")
    browser.post("/login", data={"password": "correct horse battery"})
    return browser


def test_the_dashboard_shows_how_the_machine_is_doing(client, dash):
    assert report(client, temperature_c=47.4, disk_free_mb=50000, memory_available_mb=3300, uptime_seconds=90000, screen="browser", browser_restarts=0, camera_failures=0).status_code == 204
    page = dash.get("/").text
    assert "47 °C" in page and "49 GB free" in page and "up 1 d 1 h" in page and "Needs attention" not in page


@pytest.mark.parametrize("health,text", [
    ({"temperature_c": 84.0}, "hot: 84"),
    ({"throttled": True}, "lacked power"),
    ({"disk_free_mb": 300}, "disk nearly full"),
    ({"memory_available_mb": 100}, "memory low"),
    ({"screen": "text"}, "text kiosk"),
    ({"browser_restarts": 2}, "browser restarted 2"),
    ({"camera_failures": 3}, "camera failed 3"),
])
def test_trouble_is_listed_on_the_overview(client, dash, health, text):
    report(client, **health)
    page = dash.get("/").text
    assert "Needs attention" in page and text in page


def test_a_box_gone_for_minutes_is_listed(client, dash):
    report(client)
    with db_connect() as db:
        db.execute("UPDATE device_status SET reported_at=?", ((datetime.now(timezone.utc) - timedelta(minutes=12)).isoformat(),))
    assert "offline for 12 minutes" in dash.get("/").text


def test_a_box_not_ready_is_listed(client, dash):
    client.post("/api/v1/devices/status", json={"state": "not ready", "detail": "Server: not reachable"})
    assert "not ready: Server: not reachable" in dash.get("/").text


def test_health_values_are_checked_and_extras_ignored(client):
    assert report(client, temperature_c=9999).status_code == 422
    assert report(client, disk_free_mb=-1).status_code == 422
    assert report(client, screen="hacked").status_code == 422
    assert report(client, temperature_c=40, secret="x").status_code == 204
    with db_connect() as db:
        assert "secret" not in db.execute("SELECT health FROM device_status").fetchone()[0]


def test_a_report_without_health_keeps_the_last_figures(client):
    report(client, temperature_c=40)
    client.post("/api/v1/devices/status", json={"state": "idle"})
    with db_connect() as db:
        assert "40" in db.execute("SELECT health FROM device_status").fetchone()[0]


# What gets installed

def test_the_installer_covers_autostart_watchdogs_and_timers():
    script = (PI / "install-autonomy.sh").read_text(encoding="utf-8")
    assert "systemctl enable photobox-kiosk" in script and "photobox-check.timer" in script and "photobox-nightly.timer" in script and "watchdog.conf" in script
    assert "RuntimeWatchdogSec=15" in (PI / "watchdog.conf").read_text(encoding="utf-8")


def test_the_check_restarts_only_a_silent_kiosk_and_the_night_restart_only_when_idle():
    script = (PI / "check-kiosk.sh").read_text(encoding="utf-8")
    assert '-gt 180' in script and '= "idle"' in script and "pkill -KILL" in script
    assert all((PI / name).stat().st_mode & 0o100 for name in ("check-kiosk.sh", "install-autonomy.sh"))
