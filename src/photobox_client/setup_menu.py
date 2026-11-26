"""Setup menu on the box's screen: WiFi, server address, pairing and camera (ADR-021).

Operated with the keyboard: digits choose, ENTER confirms, ESC goes back. It is protected by a PIN that
is set the first time the menu opens. The kiosk opens it on Ctrl+Alt+S, after M at boot, or by itself
when the box cannot work.
"""
from __future__ import annotations
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import httpx
from .camera import CameraError, CaptureSettings, capture_jpeg, list_stable_paths
from .display import FramebufferDisplay
from .kiosk import BACKSPACE, ENTER, ESC, Keys, Screen
from .network import Wifi
from .settings import Pin, Settings, normalize_server, save_token

MESSAGE_SECONDS = 8
CAMERA_PREVIEW_SECONDS = 5
TOF_RANGES_MM = (500, 700, 900, 1200, 1500)  # the distance sensor reaches about 1200 mm (ADR-046)
TOF_READOUT_SECONDS = 0.25
SOUND_LEVELS = (("Off", 0), ("Quiet", 30), ("Medium", 60), ("Loud", 90))  # ADR-047
SOUND_SAMPLE_SECONDS = 1.5
HttpFactory = Callable[..., httpx.Client]


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    # The box cannot take part in a session at all without this.
    essential: bool = True


def diagnose(settings: Settings, token: str | None, wifi: Wifi, http: HttpFactory) -> list[Check]:
    if getattr(wifi, "managed", lambda: True)():
        checks = [Check("Network", wifi.online(), wifi.current() or "connected" if wifi.online() else "not connected")]
    else:
        # Without NetworkManager, as on a Mac, the computer handles its own network; the server check below still counts.
        checks = [Check("Network", True, "managed by this computer", essential=False)]
    if not settings.server:
        return checks + [Check("Server", False, "no address set")]
    try:
        with http(base_url=settings.server, timeout=5) as client:
            health = client.get("/api/v1/health")
            checks.append(Check("Server", health.status_code == 200, settings.server))
            if not token:
                return checks + [Check("Pairing", False, "not paired")]
            headers = {"Authorization": f"Bearer {token}"}
            # An unknown session is 404 with a valid token and 401 without one.
            probe = client.get("/api/v1/sessions/setup-check", headers=headers)
            checks.append(Check("Pairing", probe.status_code == 404, "paired" if probe.status_code == 404 else "token not accepted"))
            notice = client.get("/api/v1/consent-notice", headers=headers)
            detail = "ready" if notice.status_code == 200 else _detail(notice)
            checks.append(Check("Consent notice", notice.status_code == 200, detail, essential=False))
    except httpx.HTTPError:
        checks.append(Check("Server", False, f"{settings.server} not reachable"))
    return checks


def _detail(response: httpx.Response) -> str:
    try:
        return str(response.json().get("detail", response.status_code))
    except ValueError:
        return str(response.status_code)


class SetupMenu:
    def __init__(self, screen: Screen, keys: Keys, *, settings_path: Path, token_path: Path, pin: Pin,
                 wifi: Wifi, http: HttpFactory = httpx.Client, display: FramebufferDisplay | None = None,
                 cameras: Callable[[], list] = list_stable_paths,
                 capture: Callable[[str, CaptureSettings], bytes] = capture_jpeg,
                 sleep: Callable[[float], None] = time.sleep, open_tof: Callable[[], object] | None = None) -> None:
        self.screen, self.keys, self.pin = screen, keys, pin
        self.open_tof = open_tof  # the distance sensor, for its live readout; None opens the real one
        self.settings_path, self.token_path = settings_path, token_path
        self.wifi, self.http, self.display = wifi, http, display
        self.cameras, self.capture, self.sleep = cameras, capture, sleep

    # Input

    def show(self, text: str) -> None:
        self.screen.show("SETUP / EINRICHTUNG\n\n" + text)

    def message(self, text: str) -> None:
        self.keys.flush()
        self.show(text + "\n\nENTER")
        self.keys.wait({ENTER, ESC}, MESSAGE_SECONDS)

    def read_text(self, prompt: str, *, initial: str = "", secret: bool = False, digits_only: bool = False, max_length: int = 64) -> str | None:
        """Type a line. ENTER accepts, ESC cancels (None), BACKSPACE deletes."""
        value = initial
        self.keys.flush()
        while True:
            shown = "*" * len(value) if secret else value
            self.show(f"{prompt}\n\n  > {shown}_\n\nENTER = OK    ESC = back")
            key = self.keys.read_key(None)
            if key == ENTER:
                return value
            if key == ESC:
                return None
            if key == BACKSPACE:
                value = value[:-1]
            elif key and len(key) == 1 and len(value) < max_length and (key.isdigit() or not digits_only):
                value += key

    def choose(self, title: str, options: list[str]) -> int | None:
        """Pick one of up to nine options by its digit. ESC returns None."""
        lines = "\n".join(f"  {number}  {option}" for number, option in enumerate(options[:9], start=1))
        self.keys.flush()
        self.show(f"{title}\n\n{lines}\n\nESC = back")
        while True:
            key = self.keys.read_key(None)
            if key == ESC:
                return None
            if key and key.isdigit() and 1 <= int(key) <= min(9, len(options)):
                return int(key) - 1

    # Access

    def unlock(self) -> bool:
        if not self.pin.is_set():
            return self.create_pin()
        while True:
            wait = self.pin.wait_seconds()
            if wait:
                self.message(f"Too many wrong PINs. Try again in {wait} s.")
                return False
            entered = self.read_text("Enter the setup PIN", secret=True, digits_only=True, max_length=8)
            if entered is None:
                return False
            if self.pin.check(entered):
                return True

    def create_pin(self) -> bool:
        first = self.read_text("Choose a setup PIN of 4 to 8 digits.\nIt protects this menu from visitors.", secret=True, digits_only=True, max_length=8)
        if first is None:
            return False
        if not Pin.valid(first):
            self.message("The PIN must be 4 to 8 digits.")
            return False
        if self.read_text("Enter the PIN again", secret=True, digits_only=True, max_length=8) != first:
            self.message("The PINs did not match. Nothing was changed.")
            return False
        self.pin.set(first)
        return True

    # Menu

    def token(self) -> str | None:
        try:
            return self.token_path.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    def run(self, reason: str | None = None) -> None:
        if not self.unlock():
            return
        while True:
            settings = Settings.load(self.settings_path)
            status = "\n".join(f"  {'OK ' if c.ok else '-- '} {c.name}: {c.detail}" for c in diagnose(settings, self.token(), self.wifi, self.http))
            header = f"{reason}\n\n" if reason else ""
            choice = self.choose(
                f"{header}{status}",
                ["WiFi", "Server address", "Pair with server", "Camera", "Test everything", "Start photo booth", "Microphone (optional)", "Person detection (optional)", "Sounds"],
            )
            reason = None
            if choice in (None, 5):
                return
            [self.setup_wifi, self.setup_server, self.pair, self.setup_camera, self.test, None, self.setup_microphone, self.setup_person, self.setup_sounds][choice]()

    def setup_wifi(self) -> None:
        self.show("Searching for WiFi networks ...")
        networks = self.wifi.scan()
        if not networks:
            self.message("No WiFi networks found.")
            return
        labels = [f"{n.ssid}  ({n.signal}%{', locked' if n.secured else ''}{', connected' if n.in_use else ''})" for n in networks]
        choice = self.choose("Choose a WiFi network", labels)
        if choice is None:
            return
        network = networks[choice]
        password = None
        if network.secured:
            password = self.read_text(f"Password for {network.ssid}", secret=True)
            if password is None:
                return
        self.show(f"Connecting to {network.ssid} ...\nThis can take up to a minute.")
        ok, detail = self.wifi.connect(network.ssid, password)
        self.message(f"{'Connected to' if ok else 'Not connected to'} {network.ssid}: {detail}")

    def setup_server(self) -> None:
        settings = Settings.load(self.settings_path)
        entered = self.read_text("Server address, for example 192.168.1.24", initial=settings.server.removeprefix("http://").removesuffix(":8880"))
        if entered is None:
            return
        try:
            settings.server = normalize_server(entered)
        except ValueError as exc:
            self.message(str(exc))
            return
        settings.save(self.settings_path)
        server = next((c for c in diagnose(settings, self.token(), self.wifi, self.http) if c.name == "Server"), None)
        self.message(f"Saved: {settings.server}\n{'Server reachable.' if server and server.ok else 'Server not reachable yet.'}")

    def pair(self) -> None:
        settings = Settings.load(self.settings_path)
        if not settings.server:
            self.message("Set the server address first.")
            return
        code = self.read_text("On the server, run:\n  admin device pair NAME\nthen enter the 6-digit code here.", digits_only=True, max_length=6)
        if code is None:
            return
        try:
            with self.http(base_url=settings.server, timeout=10) as client:
                response = client.post("/api/v1/pair", json={"code": code})
        except httpx.HTTPError:
            self.message("The server is not reachable.")
            return
        if response.status_code != 200:
            self.message(f"Pairing failed: {_detail(response)}")
            return
        body = response.json()
        save_token(self.token_path, body["token"])
        self.message(f"Paired as {body['device_id']}.")

    def setup_microphone(self) -> None:
        """Only chooses the device. Nothing is recorded here."""
        from .speech import list_microphones
        microphones = list_microphones()
        if not microphones:
            self.message("No microphone found. Voice messages stay off; photos work as usual.")
            return
        choice = self.choose("Choose the microphone", [m.name for m in microphones])
        if choice is None:
            return
        settings = Settings.load(self.settings_path)
        settings.microphone = microphones[choice].device
        settings.save(self.settings_path)
        self.message(f"Microphone saved: {microphones[choice].name}")

    def setup_person(self) -> None:
        """Switches person detection on or off: with the camera, limited to part of the picture (ADR-029), or
        with the distance sensor, which sees no picture (ADR-046)."""
        from . import tof
        from .person import MODEL_PATH
        choices = [("Off", False, "camera", "")]
        if tof.bus_present():
            choices.append(("On, distance sensor", True, "tof", ""))
        if MODEL_PATH.is_file():
            choices += [("On, camera, whole picture", True, "camera", ""), ("On, camera, middle two thirds", True, "camera", "0.17,0,0.83,1"),
                        ("On, camera, middle half", True, "camera", "0.25,0,0.75,1")]
        if len(choices) == 1:
            self.message("The person detection model is not installed and no distance sensor is connected. Photos work as usual.")
            return
        choice = self.choose(
            "Person detection. The camera looks before anyone has agreed, so switch it\n"
            "on only with the sign up and a checked legal basis; no image is stored or sent.\n"
            "The distance sensor measures only how far away something is.",
            [label for label, *_rest in choices],
        )
        if choice is None:
            return
        settings = Settings.load(self.settings_path)
        settings.person_detection, settings.person_sensor, settings.person_area = choices[choice][1:]
        settings.save(self.settings_path)
        if settings.person_sensor == "tof" and settings.person_detection:
            self.setup_tof_range()
            return
        self.message(f"Person detection: {choices[choice][0]}")

    def setup_tof_range(self) -> None:
        """Choose how close someone must stand for the distance sensor, with its live reading (ADR-046).

        The kiosk does not read the sensor while this menu is open, so the two never read it at once.
        Nothing is kept: the distances are only shown.
        """
        from . import tof
        settings = Settings.load(self.settings_path)
        chosen = settings.tof_range_mm
        try:
            sensor = (self.open_tof or tof.open_vl53l0x)()
        except Exception:  # the range can still be set; the screen says that there is no reading
            sensor = None
        self.keys.flush()
        try:
            while True:
                if sensor is None:
                    now = "no reading: the sensor did not answer"
                else:
                    try:
                        distance = sensor.measure()
                    except Exception:
                        distance = None
                    if distance is None:
                        now = "no reading"
                    elif distance >= tof.NOTHING_MM:
                        now = "nothing in reach (beyond about 120 cm)  ->  nobody"
                    else:
                        now = f"{distance / 10:.0f} cm  ->  {'someone there' if distance < chosen else 'nobody'}"
                options = "   ".join(f"{n} = {mm // 10} cm" for n, mm in enumerate(TOF_RANGES_MM, start=1))
                self.show("Distance sensor range\n"
                          "Stand where a visitor stands, then where people walk past,\nand choose a range between the two.\n\n"
                          f"  Now:    {now}\n  Range:  {chosen // 10} cm\n\n  {options}\n\nENTER = save    ESC = back")
                key = self.keys.read_key(TOF_READOUT_SECONDS)
                if key == ESC:
                    self.message(f"Person detection: distance sensor, range {settings.tof_range_mm // 10} cm (unchanged)")
                    return
                if key == ENTER:
                    settings = Settings.load(self.settings_path)
                    settings.tof_range_mm = chosen
                    settings.save(self.settings_path)
                    self.message(f"Person detection: distance sensor, range {chosen // 10} cm")
                    return
                if key and key.isdigit() and 1 <= int(key) <= len(TOF_RANGES_MM):
                    chosen = TOF_RANGES_MM[int(key) - 1]
        finally:
            if sensor is not None:
                sensor.close()

    def setup_sounds(self) -> None:
        """Switches the soft sounds off or sets their volume, with a sample (ADR-047)."""
        settings = Settings.load(self.settings_path)
        current = f"now {settings.sound_volume} of 100" if settings.sounds else "now off"
        choice = self.choose(f"Sounds ({current})", [label for label, _volume in SOUND_LEVELS])
        if choice is None:
            return
        label, volume = SOUND_LEVELS[choice]
        settings.sounds = volume > 0
        if volume:
            settings.sound_volume = volume
        settings.save(self.settings_path)
        view = getattr(self.screen, "view", None)  # the browser plays a sample; the text screen cannot
        if view is not None and volume:
            view({"screen": "menu", "text": f"SETUP / EINRICHTUNG\n\nSounds: {label}", "sound": "ready", "volume": volume})
            self.sleep(SOUND_SAMPLE_SECONDS)
        self.message(f"Sounds: {label}")

    def setup_camera(self) -> None:
        # The camera list holds paths; settings and the capture call need text.
        cameras = [str(path) for path in self.cameras()]
        if not cameras:
            self.message("No camera found. Check the USB cable.")
            return
        choice = self.choose("Choose the camera", [path.rsplit("/", 1)[-1] for path in cameras])
        if choice is None:
            return
        self.show("Taking a test picture ...")
        try:
            jpeg = self.capture(cameras[choice], CaptureSettings(width=1920, height=1080))
        except CameraError as exc:
            self.message(f"The camera did not work: {exc}")
            return
        settings = Settings.load(self.settings_path)
        settings.camera = cameras[choice]
        settings.save(self.settings_path)
        # The test picture is shown, never stored or sent.
        if self.display is not None:
            import io
            from PIL import Image
            with Image.open(io.BytesIO(jpeg)) as picture:
                self.display.show(picture.convert("RGB"))
            self.sleep(CAMERA_PREVIEW_SECONDS)
        self.message("Camera saved.")

    def test(self) -> None:
        checks = diagnose(Settings.load(self.settings_path), self.token(), self.wifi, self.http)
        lines = "\n".join(f"  {'OK ' if c.ok else '-- '} {c.name}: {c.detail}" for c in checks)
        verdict = "Everything works." if all(c.ok for c in checks) else "Not everything works yet."
        self.message(f"{lines}\n\n{verdict}")


def requires_setup(checks: list[Check]) -> bool:
    """Whether a person has to set something up. A network or server that is away may simply come back."""
    return any(c.essential and not c.ok and (c.name == "Pairing" or c.detail == "no address set") for c in checks)


def blocking_problem(checks: list[Check]) -> str | None:
    """Why the box cannot take photos, or None."""
    failed = [c for c in checks if c.essential and not c.ok]
    return f"{failed[0].name}: {failed[0].detail}" if failed else None
