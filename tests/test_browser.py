"""Starting and watching the browser (ADR-028, milestone U3). No real browser."""
from pathlib import Path
import httpx
from photobox_client.browser import BROWSER_FAILED, MAX_RESTARTS, STABLE_SECONDS, BrowserWatch
from photobox_client.webui import WebUI

PI = Path(__file__).parent.parent / "deploy" / "pi"


class Process:
    def __init__(self):
        self.running = False

    def wait(self):
        return 1

    def poll(self):
        return None if self.running else 1

    def terminate(self):
        self.running = False


def watch(popen, clock=lambda: 0.0):
    failures = []
    w = BrowserWatch("/usr/bin/browser", "http://127.0.0.1:1/?t=x", popen=popen, on_failure=lambda: failures.append(1), sleep=lambda _s: None, clock=clock)
    return w, failures


def test_a_browser_that_keeps_stopping_is_restarted_then_given_up():
    calls = []
    w, failures = watch(lambda *a, **k: calls.append(a) or Process())
    w._watch()
    assert len(calls) == MAX_RESTARTS + 1 and failures == [1]


def test_a_browser_that_cannot_start_is_given_up():
    def missing(*_a, **_k):
        raise FileNotFoundError()
    w, failures = watch(missing)
    w._watch()
    assert failures == [1]


def test_the_browser_gets_the_address_and_its_output_is_not_kept():
    seen = {}

    def popen(command, **options):
        seen.update(command=command, **options)
        w.stopping = True
        return Process()

    w, _ = watch(popen)
    w._watch()
    assert seen["command"] == ["/usr/bin/browser", "http://127.0.0.1:1/?t=x"]
    assert seen["stdout"] == seen["stderr"] == seen["stdin"] == -3  # subprocess.DEVNULL


def test_a_long_run_starts_the_count_again():
    times = iter([0.0, STABLE_SECONDS + 1] * 50)
    count = []

    def popen(*_a, **_k):
        count.append(1)
        if len(count) > 12:
            w.stopping = True
        return Process()

    w, failures = watch(popen, clock=lambda: next(times))
    w._watch()
    assert failures == [] and len(count) > MAX_RESTARTS + 1


def test_stopping_does_not_count_as_a_failure():
    def popen(*_a, **_k):
        w.stopping = True
        return Process()

    w, failures = watch(popen)
    w._watch()
    assert failures == []


def test_the_server_knows_when_a_browser_has_opened_it():
    ui = WebUI().start()
    try:
        assert not ui.connected.is_set()
        with httpx.Client(base_url=f"http://127.0.0.1:{ui.port}", timeout=5) as c:
            assert c.get("/events", params={"t": "wrong"}).status_code == 403
            assert not ui.connected.is_set()
            ui.show("x")
            with c.stream("GET", "/events", params={"t": ui.token}) as response:
                next(response.iter_lines())
        assert ui.connected.is_set()
    finally:
        ui.stop()


def test_fallback_script_starts_the_text_kiosk_on_the_failure_code():
    script = (PI / "run-kiosk.sh").read_text(encoding="utf-8")
    assert f'"$code" -eq {BROWSER_FAILED}' in script and "--ui text" in script and "--ui web" in script


def test_launcher_keeps_the_screen_private_and_at_1080p():
    script = (PI / "start-browser.sh").read_text(encoding="utf-8")
    assert "--mode 1920x1080" in script and "--incognito" in script and "XDG_RUNTIME_DIR" in script
    assert "cage" in script and "--remote-debugging" not in script


def test_scripts_are_executable():
    assert all((PI / name).stat().st_mode & 0o100 for name in ("run-kiosk.sh", "start-browser.sh"))


def test_service_runs_a_login_session_with_the_fallback_script():
    unit = (PI / "photobox-kiosk.service").read_text(encoding="utf-8")
    assert "run-kiosk.sh" in unit and "PAMName=login" in unit and "TTYPath=/dev/tty1" in unit
