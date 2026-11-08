"""Setup menu, pairing, WiFi and key input (ADR-021). Fake nmcli, synthetic images, no real network changes."""
import io
import json
import os
import stat
import subprocess
from datetime import datetime, timedelta, timezone
import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from photobox_client.kiosk import BACKSPACE, ENTER, ESC, SETUP, TerminalKeys, parse_keys
from photobox_client.network import Network, Wifi, split_terse
from photobox_client.settings import Pin, Settings, normalize_server, save_token
from photobox_client.setup_menu import Check, SetupMenu, blocking_problem, diagnose
from photobox_server import admin
from photobox_server.app import app
from photobox_server.core import db_connect
from photobox_server.pairing import MAX_WRONG_GUESSES, complete_pairing, start_pairing


# Server: pairing

def pair(client, code):
    return client.post("/api/v1/pair", json={"code": code})


def test_pairing_creates_a_device_whose_token_works(anonymous_client):
    with db_connect() as db:
        code = start_pairing(db, "box-1")
    assert len(code) == 6 and code.isdigit()
    response = pair(anonymous_client, code)
    assert response.status_code == 200 and response.json()["device_id"] == "box-1"
    token = response.json()["token"]
    assert anonymous_client.post("/api/v1/sessions", headers={"Authorization": f"Bearer {token}"}).status_code == 201


def test_pairing_endpoint_needs_no_token_but_everything_else_still_does(anonymous_client):
    assert pair(anonymous_client, "000000").status_code == 403
    assert anonymous_client.post("/api/v1/sessions").status_code == 401


def test_a_code_works_only_once(anonymous_client):
    with db_connect() as db:
        code = start_pairing(db, "box-1")
    assert pair(anonymous_client, code).status_code == 200
    assert pair(anonymous_client, code).status_code == 403


def test_a_new_pairing_cancels_the_previous_code(anonymous_client):
    with db_connect() as db:
        old = start_pairing(db, "box-1")
        new = start_pairing(db, "box-2")
    assert old == new or pair(anonymous_client, old).status_code == 403
    assert pair(anonymous_client, new).json()["device_id"] == "box-2"


def test_wrong_guesses_end_the_pairing(anonymous_client):
    with db_connect() as db:
        code = start_pairing(db, "box-1")
    wrong = f"{(int(code) + 1) % 10 ** 6:06d}"
    for _ in range(MAX_WRONG_GUESSES):
        assert pair(anonymous_client, wrong).status_code == 403
    assert pair(anonymous_client, code).status_code == 403


def test_expired_code_is_refused(anonymous_client):
    with db_connect() as db:
        code = start_pairing(db, "box-1")
        db.execute("UPDATE device_pairings SET expires_at=?", ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),))
    assert pair(anonymous_client, code).status_code == 403


def test_pairing_again_replaces_the_old_token(anonymous_client):
    tokens = []
    for _ in range(2):
        with db_connect() as db:
            code = start_pairing(db, "box-1")
        tokens.append(pair(anonymous_client, code).json()["token"])
    old, new = ({"Authorization": f"Bearer {t}"} for t in tokens)
    assert anonymous_client.post("/api/v1/sessions", headers=old).status_code == 401
    assert anonymous_client.post("/api/v1/sessions", headers=new).status_code == 201


def test_pairing_attempts_are_rate_limited(anonymous_client):
    codes = [pair(anonymous_client, f"{n:06d}").status_code for n in range(12)]
    assert codes[:10] == [403] * 10 and codes[10:] == [429, 429]


def test_only_a_hash_of_the_code_is_stored_and_the_audit_has_no_code(anonymous_client):
    with db_connect() as db:
        code = start_pairing(db, "box-1")
        stored = db.execute("SELECT code_hash FROM device_pairings").fetchone()[0]
    assert code not in stored
    pair(anonymous_client, code)
    with db_connect() as db:
        events = [tuple(r) for r in db.execute("SELECT event, device_id, detail FROM audit_events")]
    assert ("pairing_started", "box-1", None) in events and ("device_paired", "box-1", None) in events
    assert code not in json.dumps(events)


@pytest.mark.parametrize("bad", ["", "has space", "x" * 65])
def test_invalid_device_names_are_refused(anonymous_client, bad):
    with db_connect() as db:
        with pytest.raises(ValueError):
            start_pairing(db, bad)


def test_admin_pair_prints_a_code(anonymous_client, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert admin.main(["device", "pair", "box-9"]) == 0
    out = capsys.readouterr().out
    digits = "".join(ch for ch in out.split("\n")[1] if ch.isdigit())
    assert len(digits) == 6 and "5 minutes" in out
    with db_connect() as db:
        assert complete_pairing(db, digits)[0] == "box-9"


# Client: keys

@pytest.mark.parametrize("data,keys", [
    (b"\r", [ENTER]), (b"\x1b", [ESC]), (b"\x7f", [BACKSPACE]), (b"\x1b\x13", [SETUP]), (b"\x93", [SETUP]), (b"\x13", [SETUP]),
    (b"12\r", ["1", "2", ENTER]), (b"\x1b[A", []), (b"\x1b[24~", []), (b"\x01", []),
])
def test_parse_keys(data, keys):
    assert parse_keys(data) == keys


def test_terminal_passes_ctrl_s_through_instead_of_pausing_output():
    leader, follower = os.openpty()
    with TerminalKeys(follower):
        import termios
        assert not termios.tcgetattr(follower)[0] & termios.IXON
        os.write(leader, b"\x1b\x13")
    os.close(leader)
    os.close(follower)


def test_terminal_reads_typed_text_key_by_key():
    leader, follower = os.openpty()
    with TerminalKeys(follower) as keys:
        os.write(leader, b"ab\x7fc\r")
        assert [keys.read_key(1) for _ in range(5)] == ["a", "b", BACKSPACE, "c", ENTER]
        assert keys.read_key(0.2) is None
    os.close(leader)
    os.close(follower)


# Client: settings and PIN

def test_settings_round_trip_privately(tmp_path):
    path = tmp_path / "kiosk.env"
    Settings(server="http://10.0.0.5:8000", camera="/dev/v4l/by-id/cam").save(path)
    assert Settings.load(path) == Settings(server="http://10.0.0.5:8000", camera="/dev/v4l/by-id/cam")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_settings_read_the_older_hand_written_file(tmp_path):
    path = tmp_path / "kiosk.env"
    path.write_text("# comment\nPHOTOBOX_SERVER=http://192.168.1.24:8000\nPHOTOBOX_CAMERA=/dev/video0\n")
    assert Settings.load(path) == Settings(server="http://192.168.1.24:8000", camera="/dev/video0")
    assert Settings.load(tmp_path / "missing") == Settings()


@pytest.mark.parametrize("entered,expected", [
    ("192.168.1.24", "http://192.168.1.24:8000"), ("mac.local:9000", "http://mac.local:9000"),
    ("https://box.example:8443/", "https://box.example:8443"), (" http://10.0.0.1 ", "http://10.0.0.1:8000"),
])
def test_server_addresses_are_completed(entered, expected):
    assert normalize_server(entered) == expected


@pytest.mark.parametrize("entered", ["", "ftp://x", "http://", "has space"])
def test_bad_server_addresses_are_refused(entered):
    with pytest.raises(ValueError):
        normalize_server(entered)


def test_token_is_saved_privately(tmp_path):
    path = tmp_path / "token"
    save_token(path, "abc\n")
    assert path.read_text() == "abc\n" and stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_pin_is_stored_as_a_salted_hash(tmp_path):
    pin = Pin(tmp_path / "pin.json")
    pin.set("4711")
    stored = (tmp_path / "pin.json").read_text()
    assert "4711" not in stored and stat.S_IMODE(os.stat(tmp_path / "pin.json").st_mode) == 0o600
    assert pin.check("4711") and not pin.check("1234")


@pytest.mark.parametrize("bad", ["123", "123456789", "12a4", ""])
def test_pin_format(tmp_path, bad):
    with pytest.raises(ValueError):
        Pin(tmp_path / "pin.json").set(bad)


def test_wrong_pins_lock_the_menu_for_longer_each_time(tmp_path):
    now = [100.0]
    pin = Pin(tmp_path / "pin.json", clock=lambda: now[0])
    pin.set("4711")
    assert [pin.check("0000") for _ in range(3)] == [False] * 3
    assert pin.wait_seconds() == 30 and not pin.check("4711")
    now[0] += 31
    assert not pin.check("0000") and pin.wait_seconds() == 60
    now[0] += 61
    assert pin.check("4711") and pin.wait_seconds() == 0


# Client: WiFi with a fake nmcli

class FakeNmcli:
    def __init__(self, scan="", fail=None):
        self.calls = []
        self.scan = scan
        self.fail = fail or set()

    def __call__(self, command, input=None, **_kwargs):
        self.calls.append((command, input))
        verb = " ".join(command[1:4])
        code = 1 if any(word in command for word in self.fail) else 0
        if "list" in command:
            return subprocess.CompletedProcess(command, 0, self.scan, "")
        if "general" in command:
            return subprocess.CompletedProcess(command, 0, "connected\n", "")
        return subprocess.CompletedProcess(command, code, "", "")


def test_terse_output_with_escaped_colons():
    assert split_terse(r"*:Cafe\: Guest:72:WPA2") == ["*", "Cafe: Guest", "72", "WPA2"]
    assert split_terse(r"no:back\\slash") == ["no", "back\\slash"]


def test_scan_lists_named_networks_once_strongest_first():
    fake = FakeNmcli(scan="\n".join([" :Home:40:WPA2", "*:Office:70:WPA2", " :Home:80:WPA2", " ::90:WPA2", " :Open:20:"]))
    networks = Wifi(fake).scan()
    assert networks == [Network("Office", 70, True, True), Network("Home", 80, True, False), Network("Open", 20, False, False)]


def test_connect_keeps_the_password_out_of_every_argument():
    fake = FakeNmcli()
    ok, _ = Wifi(fake).connect("Home", "s3cret-password")
    assert ok
    assert all("s3cret-password" not in " ".join(command) for command, _ in fake.calls)
    assert any(stdin and "s3cret-password" in stdin for _, stdin in fake.calls)
    assert [c[0][1:3] for c in fake.calls] == [["connection", "delete"], ["connection", "add"], ["connection", "edit"], ["--wait", "30"]]


def test_open_network_needs_no_password_step():
    fake = FakeNmcli()
    assert Wifi(fake).connect("Open", None)[0]
    assert not any("edit" in command for command, _ in fake.calls)


def test_failed_connection_removes_the_new_profile():
    fake = FakeNmcli(fail={"up"})
    ok, detail = Wifi(fake).connect("Home", "s3cret-password")
    assert not ok and "password" in detail
    assert fake.calls[-1][0][1:3] == ["connection", "delete"]


@pytest.mark.parametrize("password", ["short", "x" * 64, "two\nlines"])
def test_impossible_passwords_are_refused_before_nmcli(password):
    fake = FakeNmcli()
    assert not Wifi(fake).connect("Home", password)[0]
    assert fake.calls == []


# Client: the menu

class Keys:
    def __init__(self, *keys):
        self.keys = [k for key in keys for k in (list(key) if isinstance(key, str) and len(key) > 1 and key not in (ENTER, ESC, BACKSPACE, SETUP) else [key])]

    def flush(self):
        pass

    def read_key(self, timeout):
        return self.keys.pop(0) if self.keys else ESC

    def wait(self, accepted, timeout):
        while self.keys:
            key = self.keys.pop(0)
            if key in accepted:
                return key
        return None


class Screen:
    def __init__(self):
        self.shown = []

    def show(self, text):
        self.shown.append(text)


class FakeWifi:
    def __init__(self, online=True):
        self._online = online
        self.connected = []

    def online(self):
        return self._online

    def current(self):
        return "Home" if self._online else None

    def scan(self):
        return [Network("Home", 80, True, False), Network("Cafe", 40, False, False)]

    def connect(self, ssid, password):
        self.connected.append((ssid, password))
        return True, "connected"


def server_factory(**kwargs):
    return TestClient(app, base_url=kwargs.get("base_url", "http://testserver"))


@pytest.fixture
def paths(tmp_path, anonymous_client, monkeypatch):
    notice = tmp_path / "notice.txt"
    notice.write_text("Notice text")
    monkeypatch.setenv("PHOTOBOX_CONSENT_NOTICE_FILE", str(notice))
    return {"settings_path": tmp_path / "kiosk.env", "token_path": tmp_path / "token", "pin": Pin(tmp_path / "pin.json")}


def menu(paths, keys, **kwargs):
    screen = Screen()
    options = {"wifi": FakeWifi(), "http": server_factory, **kwargs}
    return SetupMenu(screen, keys, sleep=lambda _s: None, **paths, **options), screen


def test_first_use_sets_a_pin_then_shows_the_menu(paths):
    m, screen = menu(paths, Keys("4711", ENTER, "4711", ENTER, "6"))
    m.run()
    assert paths["pin"].is_set() and paths["pin"].check("4711")
    assert any("Start photo booth" in s for s in screen.shown)


def test_mismatched_pins_change_nothing(paths):
    m, _ = menu(paths, Keys("4711", ENTER, "4712", ENTER))
    m.run()
    assert not paths["pin"].is_set()


def test_menu_stays_locked_without_the_right_pin(paths):
    paths["pin"].set("4711")
    m, screen = menu(paths, Keys("0000", ENTER, "1111", ENTER, "2222", ENTER, ENTER))
    m.run()
    assert not any("Start photo booth" in s for s in screen.shown)
    assert any("Too many wrong PINs" in s for s in screen.shown)
    assert not any("0000" in s for s in screen.shown)


def unlocked(paths, *keys, **kwargs):
    paths["pin"].set("4711")
    return menu(paths, Keys("4711", ENTER, *keys), **kwargs)


def test_server_address_is_completed_and_saved(paths):
    m, screen = unlocked(paths, "2", "testserver", ENTER, ENTER, "6")
    m.run()
    assert Settings.load(paths["settings_path"]).server == "http://testserver:8000"


def test_typing_can_be_corrected_and_cancelled(paths):
    m, _ = unlocked(paths, "2", "10.0.0.9", BACKSPACE, "8", ENTER, ENTER, "2", "ignored", ESC, "6")
    m.run()
    assert Settings.load(paths["settings_path"]).server == "http://10.0.0.8:8000"


def test_pairing_from_the_menu_stores_a_working_token(paths):
    Settings(server="http://testserver").save(paths["settings_path"])
    with db_connect() as db:
        code = start_pairing(db, "box-7")
    m, screen = unlocked(paths, "3", code, ENTER, ENTER, "6")
    m.run()
    token = paths["token_path"].read_text().strip()
    assert any("Paired as box-7" in s for s in screen.shown)
    assert TestClient(app).post("/api/v1/sessions", headers={"Authorization": f"Bearer {token}"}).status_code == 201
    assert not any(token in s for s in screen.shown)


def test_wrong_pairing_code_is_explained(paths):
    Settings(server="http://testserver").save(paths["settings_path"])
    m, screen = unlocked(paths, "3", "123456", ENTER, ENTER, "6")
    m.run()
    assert any("Pairing failed: invalid or expired" in s for s in screen.shown)
    assert not paths["token_path"].exists()


def test_wifi_choice_and_password_reach_networkmanager_and_the_password_is_not_shown(paths):
    wifi = FakeWifi()
    m, screen = unlocked(paths, "1", "1", "geheim123", ENTER, ENTER, "6", wifi=wifi)
    m.run()
    assert wifi.connected == [("Home", "geheim123")]
    assert not any("geheim123" in s for s in screen.shown) and any("*********" in s for s in screen.shown)


def test_open_wifi_asks_for_no_password(paths):
    wifi = FakeWifi()
    m, _ = unlocked(paths, "1", "2", ENTER, "6", wifi=wifi)
    m.run()
    assert wifi.connected == [("Cafe", None)]


class FakeDisplay:
    size = (1920, 1080)

    def __init__(self):
        self.shown = []

    def show(self, image):
        self.shown.append(image)


def jpeg():
    out = io.BytesIO()
    Image.new("RGB", (640, 480), "#336699").save(out, "JPEG")
    return out.getvalue()


def test_camera_is_tested_shown_and_saved(paths):
    display = FakeDisplay()
    taken = []
    m, _ = unlocked(paths, "4", "2", ENTER, "6", display=display,
                    cameras=lambda: ["/dev/v4l/by-id/a-video-index0", "/dev/v4l/by-id/b-video-index0"],
                    capture=lambda cam, _s: taken.append(cam) or jpeg())
    m.run()
    assert taken == ["/dev/v4l/by-id/b-video-index0"] and len(display.shown) == 1
    assert Settings.load(paths["settings_path"]).camera == "/dev/v4l/by-id/b-video-index0"


def test_camera_menu_works_with_the_real_camera_list(paths, tmp_path):
    # The real list_stable_paths returns Path objects, which once crashed the menu.
    from photobox_client.camera import list_stable_paths
    by_id = tmp_path / "by-id"
    by_id.mkdir()
    (by_id / "usb-Cam-video-index0").touch()
    taken = []
    m, _ = unlocked(paths, "4", "1", ENTER, "6", cameras=lambda: list_stable_paths(by_id),
                    capture=lambda cam, _s: taken.append(cam) or jpeg())
    m.run()
    assert taken == [str(by_id / "usb-Cam-video-index0")]
    assert Settings.load(paths["settings_path"]).camera == str(by_id / "usb-Cam-video-index0")


def test_status_and_test_report_each_part(paths):
    Settings(server="http://testserver").save(paths["settings_path"])
    with db_connect() as db:
        token = complete_pairing(db, start_pairing(db, "box-1"))[1]
    save_token(paths["token_path"], token)
    m, screen = unlocked(paths, "5", ENTER, "6")
    m.run()
    assert any("Everything works." in s for s in screen.shown)


def test_diagnose_finds_what_blocks_the_box(paths):
    wifi = FakeWifi()
    assert blocking_problem(diagnose(Settings(), None, wifi, server_factory)) == "Server: no address set"
    settings = Settings(server="http://testserver")
    assert blocking_problem(diagnose(settings, None, wifi, server_factory)) == "Pairing: not paired"
    assert blocking_problem(diagnose(settings, "wrong", wifi, server_factory)) == "Pairing: token not accepted"
    assert blocking_problem(diagnose(settings, None, FakeWifi(online=False), server_factory)).startswith("Network")


def test_unreachable_server_is_reported(paths):
    def down(**_kwargs):
        def fail(request):
            raise httpx.ConnectError("down")
        return httpx.Client(transport=httpx.MockTransport(fail), base_url="http://10.9.9.9:8000")

    checks = diagnose(Settings(server="http://10.9.9.9:8000"), "t", FakeWifi(), down)
    assert blocking_problem(checks) == "Server: http://10.9.9.9:8000 not reachable"


def test_missing_notice_does_not_block_the_menu_checks(paths, monkeypatch):
    monkeypatch.setenv("PHOTOBOX_CONSENT_NOTICE_FILE", "/nonexistent")
    with db_connect() as db:
        token = complete_pairing(db, start_pairing(db, "box-1"))[1]
    checks = diagnose(Settings(server="http://testserver"), token, FakeWifi(), server_factory)
    notice = next(c for c in checks if c.name == "Consent notice")
    assert not notice.ok and not notice.essential and blocking_problem(checks) is None



def test_without_networkmanager_the_network_check_does_not_block(monkeypatch):
    from photobox_client.network import Wifi
    from photobox_client.settings import Settings
    from photobox_client.setup_menu import blocking_problem, diagnose
    import photobox_client.network as network
    monkeypatch.setattr(network.shutil, "which", lambda _name: None)
    checks = diagnose(Settings(), None, Wifi(runner=lambda *a, **k: (_ for _ in ()).throw(AssertionError("nmcli must not run"))), None)
    assert checks[0].ok and checks[0].detail == "managed by this computer"
    assert blocking_problem(checks) == "Server: no address set"


def test_microphone_is_chosen_without_recording_anything(paths, monkeypatch):
    from photobox_client import speech
    monkeypatch.setattr(speech, "list_microphones", lambda: [speech.MicrophoneInfo("plughw:CARD=BRIO,DEV=0", "Logitech BRIO"), speech.MicrophoneInfo("plughw:CARD=Mini,DEV=0", "RODE Mini")])
    monkeypatch.setattr(speech.Microphone, "record", lambda *_a, **_k: pytest.fail("the menu must not record"))
    m, _ = unlocked(paths, "7", "2", ENTER, "6")
    m.run()
    assert Settings.load(paths["settings_path"]).microphone == "plughw:CARD=Mini,DEV=0"


def test_missing_microphone_is_explained(paths, monkeypatch):
    from photobox_client import speech
    monkeypatch.setattr(speech, "list_microphones", lambda: [])
    m, screen = unlocked(paths, "7", ENTER, "6")
    m.run()
    assert any("photos work as usual" in s for s in screen.shown)


@pytest.mark.parametrize("key,on,area", [("1", False, ""), ("2", True, ""), ("3", True, "0.17,0,0.83,1"), ("4", True, "0.25,0,0.75,1")])
def test_person_detection_is_switched_in_the_menu(paths, monkeypatch, tmp_path, key, on, area):
    from photobox_client import person
    model = tmp_path / "nanodet.onnx"
    model.write_bytes(b"x")
    monkeypatch.setattr(person, "MODEL_PATH", model)
    m, screen = unlocked(paths, "8", key, ENTER, "6")
    m.run()
    saved = Settings.load(paths["settings_path"])
    assert (saved.person_detection, saved.person_area) == (on, area)
    assert any("legal basis" in s for s in screen.shown)


def test_person_detection_needs_its_model(paths, monkeypatch, tmp_path):
    from photobox_client import person
    monkeypatch.setattr(person, "MODEL_PATH", tmp_path / "missing.onnx")
    m, screen = unlocked(paths, "8", ENTER, "6")
    m.run()
    assert any("not installed" in s for s in screen.shown)
    assert Settings.load(paths["settings_path"]).person_detection is False


def test_person_detection_stays_off_unless_chosen(paths, monkeypatch, tmp_path):
    from photobox_client import person
    model = tmp_path / "nanodet.onnx"
    model.write_bytes(b"x")
    monkeypatch.setattr(person, "MODEL_PATH", model)
    m, _ = unlocked(paths, "8", ESC, "6")
    m.run()
    assert Settings.load(paths["settings_path"]).person_detection is False
