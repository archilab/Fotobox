"""Keyboard kiosk for the box's screen (ADR-020).

ENTER starts and shows the server's consent notice. A second ENTER confirms it, and only then is
consent recorded and the photo taken, after a countdown. ESC cancels at the notice. While the strip
is generated, a timer runs on screen. At the end the finished strip is shown on the screen for 20
seconds with the withdrawal code beside it, and then the code alone. Printing is not implemented yet:
it needs the printer's specifications (docs/MILESTONES.md), so for now the strip is always shown.

    python -m photobox_client.kiosk

Server address and camera come from ~/.photobox-kiosk.env, written by the setup menu (setup_menu.py),
which opens on Ctrl+Alt+S, on M during the first seconds after start, or by itself if the box cannot work.

Keys pressed before a question appears are discarded, so a double press can never count as consent.
"""
from __future__ import annotations
import argparse
import os
import select
import shutil
import sys
import termios
import textwrap
import time
import tty
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
import httpx
from .camera import CameraError, CaptureSettings, capture_jpeg
from .display import FramebufferDisplay, result_screen
from .cli import idempotency_header, post_with_retry, read_token

ENTER, ESC, BACKSPACE, SETUP = "enter", "esc", "backspace", "setup"
# Ctrl+Alt+S, as the console sends it with Alt as an ESC prefix or as the eighth bit, and Ctrl+S alone.
SETUP_SEQUENCES = (b"\x1b\x13", b"\x93", b"\x13")
NOTICE_TIMEOUT_SECONDS = 90
RESULT_SECONDS = 60
STRIP_SECONDS = 20
MESSAGE_SECONDS = 10
# How often the start screen asks the server what is happening at the other boxes (ADR-023).
PRESENCE_REFRESH_SECONDS = 10
PRESENCE_TILES = 3
START_TEXT = "Druecke ENTER zum Starten.\nPress ENTER to start."
COUNTDOWN = (3, 2, 1)
BIG_DIGITS = {
    3: ("#####", "    #", " ####", "    #", "#####"),
    2: ("#####", "    #", "#####", "#    ", "#####"),
    1: ("  #  ", " ##  ", "  #  ", "  #  ", " ### "),
}


class Screen(Protocol):
    def show(self, text: str) -> None: ...


class Keys(Protocol):
    def flush(self) -> None: ...
    def wait(self, accepted: set[str], timeout: float | None) -> str | None: ...
    def read_key(self, timeout: float | None) -> str | None: ...


class TerminalScreen:
    def __init__(self, stream=sys.stdout) -> None:
        self.stream = stream

    def show(self, text: str) -> None:
        columns, rows = shutil.get_terminal_size((80, 24))
        # Hide the cursor, which would blink on top of an image, and keep clear of the edges, which
        # many TVs cut off. Line length is limited for reading.
        left, top = " " * max(2, columns // 12), max(1, rows // 10)
        width = max(30, min(columns - 2 * len(left), 90))
        lines = []
        for line in text.splitlines() or [""]:
            lines.extend(textwrap.wrap(line, width) or [""])
        self.stream.write("\x1b[?25l\x1b[2J\x1b[H" + "\n" * top + "\n".join(left + line for line in lines) + "\n")
        self.stream.flush()


def parse_keys(data: bytes) -> list[str]:
    """Turn one read from the terminal into keys: ENTER, ESC, BACKSPACE, SETUP, or a printable character.

    A lone ESC is the Escape key; ESC followed by more bytes is an arrow or function key and is ignored.
    Fast typing can arrive in one read, so every byte is looked at.
    """
    if data == b"\x1b":
        return [ESC]
    if data in SETUP_SEQUENCES:
        return [SETUP]
    if data.startswith(b"\x1b"):
        return []
    keys = []
    for byte in data:
        if byte in (0x0D, 0x0A):
            keys.append(ENTER)
        elif byte in (0x7F, 0x08):
            keys.append(BACKSPACE)
        elif byte in (0x13, 0x93):
            keys.append(SETUP)
        elif 0x20 <= byte < 0x7F:
            keys.append(chr(byte))
    return keys


class TerminalKeys:
    """Reads single key presses from a terminal in raw mode."""

    def __init__(self, fd: int) -> None:
        self.fd = fd
        self.pending: list[str] = []

    def __enter__(self):
        self.saved = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        attributes = termios.tcgetattr(self.fd)
        # Without this, Ctrl+S would pause the screen (XOFF) instead of reaching the kiosk.
        attributes[0] &= ~(termios.IXON | termios.IXOFF)
        termios.tcsetattr(self.fd, termios.TCSANOW, attributes)
        return self

    def __exit__(self, *_exc) -> None:
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)

    def flush(self) -> None:
        self.pending.clear()
        termios.tcflush(self.fd, termios.TCIFLUSH)

    def _read(self, timeout: float | None) -> bytes:
        ready, _, _ = select.select([self.fd], [], [], timeout)
        return os.read(self.fd, 32) if ready else b""

    def read_key(self, timeout: float | None) -> str | None:
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.pending:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            if remaining == 0.0:
                return None
            self.pending.extend(parse_keys(self._read(remaining)))
        return self.pending.pop(0)

    def wait(self, accepted: set[str], timeout: float | None) -> str | None:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            key = self.read_key(remaining)
            if key is None or key in accepted:
                return key


class Api:
    def __init__(self, client: httpx.Client) -> None:
        self.client = client

    def notice(self) -> tuple[str, str]:
        response = self.client.get("/api/v1/consent-notice")
        response.raise_for_status()
        body = response.json()
        return body["version"], body["text"]

    def start(self, notice_version: str) -> tuple[str, str | None]:
        """Create a session and record the participant's consent. Returns (session id, withdrawal code)."""
        response = post_with_retry(self.client, "/api/v1/sessions", headers=idempotency_header())
        response.raise_for_status()
        session_id = response.json()["id"]
        consent = {"processing": True, "pool": False, "method": "participant_screen", "notice_version": notice_version}
        response = post_with_retry(self.client, f"/api/v1/sessions/{session_id}/consent", json=consent)
        response.raise_for_status()
        return session_id, response.json().get("withdrawal_code")

    def upload_and_process(self, session_id: str, jpeg: bytes) -> None:
        response = post_with_retry(self.client, f"/api/v1/sessions/{session_id}/portrait", files={"image": ("capture.jpg", jpeg, "image/jpeg")}, headers=idempotency_header())
        response.raise_for_status()
        response = post_with_retry(self.client, f"/api/v1/sessions/{session_id}/process")
        response.raise_for_status()

    def presence(self) -> dict | None:
        response = self.client.get("/api/v1/presence/snapshot", timeout=5)
        response.raise_for_status()
        return response.json()

    def strip(self, session_id: str) -> bytes:
        response = self.client.get(f"/api/v1/sessions/{session_id}/result")
        response.raise_for_status()
        return response.content

    def status(self, session_id: str) -> dict:
        response = self.client.get(f"/api/v1/sessions/{session_id}")
        response.raise_for_status()
        return response.json()


# The console font is kept to plain letters, as on every other kiosk screen.
PLAIN_LETTERS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})


def presence_text(snapshot: dict | None) -> str:
    """The start screen's lines about the other boxes, in German and English. Empty if unknown.

    The server chooses the message from real state (ADR-023); the kiosk only shows it.
    """
    if not snapshot:
        return ""
    texts = snapshot.get("message", {}).get("texts", {})
    lines = []
    for language in ("de", "en"):
        text = texts.get(language)
        if text:
            lines.append(f"{text['hero']}\n{text['support']}")
    tiles = []
    for location in snapshot.get("locations", [])[:PRESENCE_TILES]:
        status = location.get("status", {})
        words = " / ".join(dict.fromkeys(word for word in (status.get("de"), status.get("en")) if word))
        tiles.append(f"  {location.get('label') or 'Fotobox'}  ·  {words}")
    more = snapshot.get("more_locations", 0) + max(0, len(snapshot.get("locations", [])) - PRESENCE_TILES)
    if more:
        tiles.append(f"  +{more}")
    return ("\n\n".join(lines) + ("\n\n" + "\n".join(tiles) if tiles else "")).translate(PLAIN_LETTERS)


def big(number: int) -> str:
    return "\n\n" + "\n".join("        " + row.replace("#", "██") for row in BIG_DIGITS[number])


class StatusReporter:
    """Tells the server what the box is doing, on every change and every 30 s, without ever blocking the kiosk."""

    INTERVAL_SECONDS = 30

    def __init__(self, post: Callable[[dict], None]) -> None:
        import threading
        self.post = post
        self.state, self.detail = "idle", None
        self.changed = threading.Event()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="photobox-status", daemon=True)

    def start(self) -> "StatusReporter":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.stopped.set()
        self.changed.set()

    def __call__(self, state: str, detail: str | None = None) -> None:
        if (state, detail) != (self.state, self.detail):
            self.state, self.detail = state, detail
            self.changed.set()

    def _loop(self) -> None:
        while not self.stopped.is_set():
            try:
                self.post({"state": self.state, "detail": self.detail})
            except Exception:
                pass  # the dashboard is informational; the kiosk carries on without it
            self.changed.wait(self.INTERVAL_SECONDS)
            self.changed.clear()


class Kiosk:
    def __init__(self, api: Api, camera: Callable[[], bytes], screen: Screen, keys: Keys, *,
                 wait_seconds: int = 1800, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, test_mode: bool = False,
                 display: FramebufferDisplay | None = None, strip_seconds: int = STRIP_SECONDS,
                 report: Callable[..., None] = lambda *_args: None) -> None:
        self.api, self.camera, self.screen, self.keys, self.report = api, camera, screen, keys, report
        self.display, self.strip_seconds = display, strip_seconds
        self.wait_seconds, self.sleep, self.clock = wait_seconds, sleep, clock
        self.banner = "TEST MODE - no camera\n\n" if test_mode else ""

    def ask(self, text: str, accepted: set[str], timeout: float | None) -> str | None:
        self.keys.flush()
        self.screen.show(self.banner + text)
        return self.keys.wait(accepted, timeout)

    def message(self, text: str) -> None:
        self.ask(text + "\n\nENTER", {ENTER}, MESSAGE_SECONDS)

    def start_screen(self) -> str:
        """Wait for ENTER or the setup keys, refreshing what is happening at the other boxes."""
        self.keys.flush()
        while True:
            try:
                presence = presence_text(self.api.presence())
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                presence = ""  # the start screen works without it
            self.screen.show(self.banner + "FOTOBOX\n\n" + (presence + "\n\n" if presence else "") + START_TEXT)
            key = self.keys.wait({ENTER, SETUP}, PRESENCE_REFRESH_SECONDS)
            if key is not None:
                return key

    def run_once(self) -> str:
        """One participant from start to finish. Returns how it ended."""
        self.report("idle")
        if self.start_screen() == SETUP:
            return "setup"
        self.report("consent")
        try:
            version, text = self.api.notice()
        except httpx.HTTPError:
            self.message("Die Fotobox ist gerade nicht bereit.\nThe photo booth is not ready right now.")
            return "not ready"
        if self.ask(text, {ENTER, ESC}, NOTICE_TIMEOUT_SECONDS) != ENTER:
            self.message("Abgebrochen. Es wurde kein Foto gemacht.\nCancelled. No photo was taken.")
            return "declined"
        try:
            session_id, code = self.api.start(version)
            self.report("capturing")
            for number in COUNTDOWN:
                self.screen.show(self.banner + "Schau in die Kamera / Look at the camera" + big(number))
                self.sleep(1)
            self.screen.show(self.banner + "\n\n        * KLICK *")
            jpeg = self.camera()
            self.api.upload_and_process(session_id, jpeg)
        except CameraError:
            self.report("idle", "camera failed")
            self.message("Die Kamera hat nicht funktioniert. Bitte melde dich bei der Betreuung.\nThe camera failed. Please ask the staff.")
            return "camera failed"
        except httpx.HTTPError:
            self.message("Verbindung zum Server fehlgeschlagen. Bitte melde dich bei der Betreuung.\nCould not reach the server. Please ask the staff.")
            return "network failed"
        return self.follow(session_id, code)

    def follow(self, session_id: str, code: str | None) -> str:
        self.report("generating")
        started = self.clock()
        while True:
            try:
                state = self.api.status(session_id)
            except httpx.HTTPError:
                state = {"status": "unknown", "job": None}
            elapsed = int(self.clock() - started)
            job = state.get("job") or {}
            if state["status"] == "complete":
                break
            if state["status"] in {"failed", "withdrawn", "uploaded"} or elapsed >= self.wait_seconds:
                self.message("Leider hat es nicht geklappt. Bitte melde dich bei der Betreuung.\nSorry, that did not work. Please ask the staff.")
                return "generation failed"
            frames = f"Bild {job.get('frames_done', 0)} von {job.get('frames_total', '?')}" if job else "In der Warteschlange / Queued"
            self.screen.show(
                f"{self.banner}Dein Fotostreifen wird erstellt.\nYour photo strip is being made.\n\n"
                f"        {elapsed // 60:02d}:{elapsed % 60:02d}\n\n        {frames}"
            )
            self.sleep(1)
        self.show_strip(session_id, code)
        withdraw = (f"Dein Widerrufscode / Your withdrawal code:\n\n        {code}\n\nNotiere ihn, um deine Bilder loeschen zu lassen.\nWrite it down to have your images deleted."
                    if code else "Zum Loeschen deiner Bilder wende dich an die Betreuung.\nTo have your images deleted, ask the staff.")
        self.ask(f"Fertig! Done!\n\n{withdraw}\n\nENTER", {ENTER}, RESULT_SECONDS)
        return "complete"


    def show_strip(self, session_id: str, code: str | None) -> bool:
        """Show the finished strip on the screen. Returns False if it could not be shown."""
        if self.display is None:
            return False
        self.report("showing")
        try:
            image = result_screen(self.api.strip(session_id), code, self.display.size)
            self.display.show(image)
        except (httpx.HTTPError, OSError, ValueError):
            return False
        self.sleep(self.strip_seconds)
        return True


BOOT_SETUP_SECONDS = 10
NOT_READY_RETRY_SECONDS = 30


def main(argv: list[str] | None = None) -> None:
    from .network import Wifi
    from .settings import Pin, Settings
    from .setup_menu import SetupMenu, blocking_problem, diagnose

    home = Path.home()
    parser = argparse.ArgumentParser(description="Photo booth keyboard kiosk")
    parser.add_argument("--config", type=Path, default=home / ".photobox-kiosk.env", help="Server address and camera, written by the setup menu")
    parser.add_argument("--token-file", type=Path, default=home / ".photobox.token")
    parser.add_argument("--pin-file", type=Path, default=home / ".photobox-setup.json")
    parser.add_argument("--server", help="Use this server instead of the one in --config")
    parser.add_argument("--camera", help="Use this camera instead of the one in --config")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--rotate", type=int, default=0)
    parser.add_argument("--wait-seconds", type=int, default=1800)
    parser.add_argument("--test-image", type=Path, help="Use this file instead of the camera, for tests without people")
    parser.add_argument("--once", action="store_true", help="Serve one participant, then exit")
    parser.add_argument("--strip-seconds", type=int, default=STRIP_SECONDS, help="How long the finished strip is shown (default: 20)")
    parser.add_argument("--no-display", action="store_true", help="Do not show the strip on the screen")
    args = parser.parse_args(argv)

    def current() -> tuple[Settings, str | None]:
        settings = Settings.load(args.config)
        settings.server = args.server or settings.server
        settings.camera = args.camera or settings.camera
        try:
            token = read_token(args.token_file)
        except OSError:
            token = None
        return settings, token

    screen, wifi = TerminalScreen(), Wifi()
    reporter = None

    def reporting(settings: Settings, token: str | None) -> StatusReporter | None:
        """A reporter for the current server and token, replacing any earlier one."""
        nonlocal reporter
        if reporter is not None:
            reporter.stop()
        reporter = None
        if settings.server and token:
            def post(body: dict, server=settings.server, token=token) -> None:
                httpx.post(f"{server}/api/v1/devices/status", json=body, headers={"Authorization": f"Bearer {token}"}, timeout=5)
            reporter = StatusReporter(post).start()
        return reporter
    display = None if args.no_display else FramebufferDisplay.open()
    with TerminalKeys(sys.stdin.fileno()) as keys:
        menu = SetupMenu(screen, keys, settings_path=args.config, token_path=args.token_file, pin=Pin(args.pin_file), wifi=wifi, display=display)
        keys.flush()
        screen.show(f"FOTOBOX\n\nStarting ...\n\nM = Einrichtung / setup  ({BOOT_SETUP_SECONDS} s)")
        if keys.wait({"m", "M", SETUP}, BOOT_SETUP_SECONDS):
            menu.run()
        while True:
            settings, token = current()
            report = reporting(settings, token) or (lambda *_args: None)
            problem = blocking_problem(diagnose(settings, token, wifi, httpx.Client))
            if problem:
                report("not ready", problem[:120])
                menu.run(reason=f"The photo booth cannot start. {problem}")
                settings, token = current()
                problem = blocking_problem(diagnose(settings, token, wifi, httpx.Client))
                if problem:
                    keys.flush()
                    screen.show(f"FOTOBOX\n\nNicht bereit / Not ready\n{problem}\n\nCtrl+Alt+S = Einrichtung / setup")
                    if keys.wait({SETUP}, NOT_READY_RETRY_SECONDS) == SETUP:
                        menu.run()
                    continue
            if args.test_image:
                camera = lambda: args.test_image.read_bytes()
            else:
                capture = CaptureSettings(width=args.width, height=args.height, rotation=args.rotate)
                camera = lambda: capture_jpeg(settings.camera, capture)
            with httpx.Client(base_url=settings.server, timeout=60, headers={"Authorization": f"Bearer {token}"}) as client:
                kiosk = Kiosk(Api(client), camera, screen, keys, wait_seconds=args.wait_seconds,
                              test_mode=bool(args.test_image), display=display, strip_seconds=args.strip_seconds, report=report)
                if kiosk.run_once() == "setup":
                    report("setup")
                    menu.run()
            if args.once:
                return


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
