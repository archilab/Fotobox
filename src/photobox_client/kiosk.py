"""Keyboard kiosk for the box's screen (ADR-020).

ENTER starts and shows the server's consent notice, with the pool question if one is offered. A second
ENTER confirms both, and only then is consent recorded and the photo taken, after a countdown. Doing nothing
for 45 seconds cancels. While the strip
is generated, a timer runs on screen. At the end the finished strip is shown on the screen for 20
seconds, and then the withdrawal code for 10 seconds; ENTER gives more time, up to a minute. Printing is not implemented yet:
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
from contextlib import nullcontext
from pathlib import Path
from typing import Protocol
import httpx
from .camera import CameraError, CaptureSettings, capture_jpeg
from .display import FramebufferDisplay, result_screen
from .cli import idempotency_header, post_with_retry, read_token
from . import views
from .browser import BROWSER_FAILED, CONNECT_SECONDS, BrowserWatch
from .conversation import Conversation, speech_talker
from .live_feed import PersonReporter, PresenceFeed

ENTER, ESC, BACKSPACE, SETUP = "enter", "esc", "backspace", "setup"
# Ctrl+Alt+S, as the console sends it with Alt as an ESC prefix or as the eighth bit, and Ctrl+S alone.
SETUP_SEQUENCES = (b"\x1b\x13", b"\x93", b"\x13")
CONSENT_SECONDS = 45
CODE_SECONDS = 10
CODE_MORE_TIMES = 5  # each ENTER gives another CODE_SECONDS: at most a minute on screen
STRIP_SECONDS = 20
LIVE_MOMENT_SECONDS = 3
MESSAGE_SECONDS = 10
# How often the start screen asks the server what is happening at the other boxes (ADR-023).
PRESENCE_REFRESH_SECONDS = 3
FEED_CHECK_SECONDS = 0.2  # how often the start screen looks at the live feed
PRESENCE_TILES = 3
CANCELLED = "Abgebrochen. Es wurde kein Foto gemacht.\nCancelled. No photo was taken."
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

    def notice(self) -> tuple[str, str, dict | None]:
        """The consent notice's version and text, and the separate pool question or None (ADR-024)."""
        response = self.client.get("/api/v1/consent-notice")
        response.raise_for_status()
        body = response.json()
        # The notice covers live pairing when the server has it switched on (ADR-024).
        self.live_matching = bool(body.get("live_matching"))
        return body["version"], body["text"], body.get("pool")

    def start(self, notice_version: str, pool: dict | None = None) -> tuple[str, str | None]:
        """Create a session and record the participant's consent. Returns (session id, withdrawal code).

        `pool` is the pool question the participant agreed to, or None if they did not see or agree to it.
        """
        response = post_with_retry(self.client, "/api/v1/sessions", headers=idempotency_header())
        response.raise_for_status()
        session_id = response.json()["id"]
        consent = {"processing": True, "pool": pool is not None, "live": getattr(self, "live_matching", False),
                   "method": "participant_screen", "notice_version": notice_version}
        if pool is not None:
            consent |= {"pool_notice_version": pool["version"], "pool_until": pool["until"]}
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

    def messaging_events(self) -> list[dict]:
        response = self.client.get("/api/v1/messaging/events", timeout=5)
        response.raise_for_status()
        return response.json()["events"]

    def send_message(self, session_id: str, match_id: str, message_id: str, text: str) -> dict:
        response = self.client.post(f"/api/v1/sessions/{session_id}/messages", json={"message_id": message_id, "match_id": match_id, "text": text}, timeout=5)
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


def _places(label: str | None) -> tuple[str, str]:
    return (f"in {label}", f"in {label}") if label else ("an einer anderen Fotobox", "at another photo booth")


def live_moment_text(state: dict) -> str:
    """The screen for the moment two visitors are paired (ADR-024)."""
    de, en = _places((state.get("match") or {}).get("partner_label"))
    return (f"* * *   L I V E   * * *\n\nDu und jemand {de}:\nein gemeinsamer Fotostreifen!\n\n"
            f"You and someone {en}:\none shared photo strip!").translate(PLAIN_LETTERS)


def encounter_text(state: dict, waited: bool = False) -> str:
    """Who this visitor's photo meets, exactly as the server knows it (ADR-024). Empty for a seed nobody waited for."""
    job = state.get("job") or {}
    match = state.get("match") or {}
    if job.get("waiting_for_partner"):
        left = job.get("wait_seconds_left")
        clock = f"  (noch {left} s / {left} s left)" if left else ""
        if job.get("partner_coming"):
            de, en = _places(job.get("partner_coming_label"))
            return (f"Jemand {de} macht auch gerade ein Foto. Wir warten darauf!{clock}\n"
                    f"Someone {en} is taking a photo too. Waiting for it!").translate(PLAIN_LETTERS)
        return f"Wir suchen jemanden an einer anderen Fotobox ...{clock}\nLooking for someone at another photo booth ..."
    if match.get("kind") == "live":
        de, en = _places(match.get("partner_label"))
        return (f"LIVE mit jemandem {de}. Ihr seht beide denselben Fortschritt.\n"
                f"LIVE with someone {en}. You both see the same progress.").translate(PLAIN_LETTERS)
    if match.get("kind") == "pool":
        return "Dein Foto trifft jemanden, der vorhin hier war.\nYour photo meets someone who was here earlier."
    if waited and match.get("kind") == "seed":
        return "Gerade war niemand an einer anderen Fotobox.\nNobody was at another photo booth just now."
    return ""


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
                 report: Callable[..., None] = lambda *_args: None,
                 make_conversation: Callable[[str], "Conversation"] | None = None,
                 detector: "PersonDetector | None" = None, feed: "PresenceFeed | None" = None) -> None:
        self.api, self.camera, self.screen, self.keys, self.report = api, camera, screen, keys, report
        self.display, self.strip_seconds = display, strip_seconds
        self.wait_seconds, self.sleep, self.clock = wait_seconds, sleep, clock
        self.banner = "TEST MODE - no camera\n\n" if test_mode else ""
        self.pooled_until: str | None = None
        self.make_conversation = make_conversation
        self.detector, self.feed = detector, feed
        self.label: str | None = None

    def present(self, state: dict) -> None:
        """Show a screen. Every screen carries the box's name and whether this is a test."""
        self.label = state.get("label", self.label)
        views.present(self.screen, state | {"label": self.label, "test_mode": bool(self.banner), "plain": self.banner + state["plain"]})

    def ask(self, state: dict, accepted: set[str], timeout: float | None) -> str | None:
        self.keys.flush()
        self.present(state)
        return self.keys.wait(accepted, timeout)

    def message(self, text: str) -> None:
        self.ask(views.message(text, key=True), {ENTER}, MESSAGE_SECONDS)

    def start_screen(self) -> str:
        """Wait for ENTER or the setup keys, refreshing what is happening at the other boxes."""
        self.keys.flush()
        # Only here, never during a visit, and the camera is free again before anything else happens (ADR-029).
        if self.detector is not None:
            self.detector.resume()
        shown_version = None
        try:
            while True:
                live = self.feed is not None and self.feed.connected
                if live:
                    version, snapshot = self.feed.latest()
                if not live or version != shown_version:
                    try:
                        if not live:
                            snapshot = self.api.presence()
                        presence = presence_text(snapshot)
                    except (httpx.HTTPError, ValueError, KeyError, TypeError):
                        snapshot, presence = None, ""  # the start screen works without it
                    self.present(views.start(snapshot, "FOTOBOX\n\n" + (presence + "\n\n" if presence else "") + START_TEXT, detecting=self.detector is not None))
                    shown_version = version if live else None
                key = self.keys.wait({ENTER, SETUP}, FEED_CHECK_SECONDS if live else PRESENCE_REFRESH_SECONDS)
                if key is not None:
                    return key
        finally:
            if self.detector is not None:
                self.detector.pause()

    def run_once(self) -> str:
        """One participant from start to finish. Returns how it ended."""
        self.report("idle")
        if self.start_screen() == SETUP:
            return "setup"
        self.report("consent")
        try:
            version, text, pool = self.api.notice()
        except httpx.HTTPError:
            self.message("Die Fotobox ist gerade nicht bereit.\nThe photo booth is not ready right now.")
            return "not ready"
        # One question and one key, for the photo and the pool together; doing nothing cancels (ADR-030).
        if self.ask(views.consent(text, pool, CONSENT_SECONDS), {ENTER}, CONSENT_SECONDS) != ENTER:
            self.message(CANCELLED)
            return "declined"
        self.pooled_until = pool["until_text"] if pool else None
        try:
            session_id, code = self.api.start(version, pool)
            self.report("capturing")
            for number in COUNTDOWN:
                self.present(views.countdown(number, "Schau in die Kamera / Look at the camera" + big(number)))
                self.sleep(1)
            self.present(views.flash())
            jpeg = self.camera()
            # The picture is taken, so the screen need not light the room any longer.
            self.present(views.making(0, {}, None, "Foto aufgenommen.\nPhoto taken."))
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
        waited, announced = False, False
        conversation = self.make_conversation(session_id) if self.make_conversation else None
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
            waited = waited or bool(job.get("waiting_for_partner"))
            if (state.get("match") or {}).get("kind") == "live" and not announced:
                announced = True
                self.present(views.live_moment((state.get("match") or {}).get("partner_label"), live_moment_text(state)))
                self.sleep(LIVE_MOMENT_SECONDS)
            encounter = encounter_text(state, waited)
            spoken = None
            if conversation is not None:
                conversation.poll()
                talk = conversation.text().translate(PLAIN_LETTERS)
                encounter = f"{encounter}\n\n{talk}" if talk and encounter else (talk or encounter)
                spoken = conversation.view()
            self.present(views.making(elapsed, job, views.encounter(state, waited, spoken), (
                "Dein Fotostreifen wird erstellt.\nYour photo strip is being made.\n\n"
                + (encounter + "\n\n" if encounter else "")
                + f"        {elapsed // 60:02d}:{elapsed % 60:02d}\n\n        {frames}"
            )))
            if conversation is not None:
                conversation.wait(1)
            else:
                self.sleep(1)
        self.show_strip(session_id, code)
        withdraw = (f"Dein Widerrufscode / Your withdrawal code:\n\n        {code}\n\nNotiere ihn, um deine Bilder loeschen zu lassen.\nWrite it down to have your images deleted."
                    if code else "Zum Loeschen deiner Bilder wende dich an die Betreuung.\nTo have your images deleted, ask the staff.")
        if self.pooled_until:
            withdraw += (f"\n\nDein Foto bleibt bis {self.pooled_until} im Pool. Der Code loescht es auch dort."
                         f"\nYour photo stays in the pool until {self.pooled_until}. The code deletes it there too.")
        # The code is on a public screen: briefly, and longer only if the visitor asks (at most a minute).
        for _ in range(1 + CODE_MORE_TIMES):
            state = views.done(code, self.pooled_until, f"Fertig! Done!\n\n{withdraw}\n\nENTER = mehr Zeit / more time", CODE_SECONDS)
            if self.ask(state, {ENTER}, CODE_SECONDS) != ENTER:
                break
        return "complete"


    def show_strip(self, session_id: str, code: str | None) -> bool:
        """Show the finished strip on the screen. Returns False if it could not be shown."""
        put_image = getattr(self.screen, "put_image", None)
        if self.display is None and put_image is None:
            return False
        self.report("showing")
        try:
            strip = self.api.strip(session_id)
            if put_image is not None:
                self.present(views.strip(put_image(strip), None))
            else:
                self.display.show(result_screen(strip, None, self.display.size))
        except (httpx.HTTPError, OSError, ValueError):
            return False
        self.sleep(self.strip_seconds)
        if put_image is not None:
            self.screen.clear_image()  # the strip is held only while it is on screen
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
    parser.add_argument("--ui", choices=["text", "web"], default="text", help="Console text, or the browser screen on 127.0.0.1")
    parser.add_argument("--ui-port", type=int, default=8765, help="Port of the browser screen")
    parser.add_argument("--browser", help="Program that shows the screen's address, given as its argument. Without it, the address is printed")
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

    web = None
    if args.ui == "web":
        from .webui import WebUI
        try:
            web = WebUI(args.ui_port).start()
        except OSError:
            if args.browser:
                print("The screen server could not start.", file=sys.stderr, flush=True)
                raise SystemExit(BROWSER_FAILED) from None
            print("The browser screen could not start; using the text screen.", flush=True)
        if web is not None and args.browser:
            started = time.monotonic()
            watch = BrowserWatch(args.browser, web.url).start()
            if not web.connected.wait(CONNECT_SECONDS):
                print("The browser did not open the screen in time.", file=sys.stderr, flush=True)
                watch.stop()
                raise SystemExit(BROWSER_FAILED)
            print(f"Browser screen ready after {time.monotonic() - started:.1f} s", file=sys.stderr, flush=True)
        elif web is not None:
            print(f"Open the screen at {web.url}", flush=True)
    screen, wifi = web or TerminalScreen(), Wifi()
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
    # The browser draws the strip itself, and has the screen to itself.
    display = None if args.no_display or web is not None else FramebufferDisplay.open()
    with (nullcontext(web) if web is not None else TerminalKeys(sys.stdin.fileno())) as keys:
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
                api = Api(client)
                talker = speech_talker(settings.microphone, keys, screen)

                def conversation(session_id: str) -> Conversation:
                    return Conversation(api, session_id, keys, talker, time.monotonic)

                # The test image stands in for the camera, so there is nothing to look at then.
                detector, person_reporter = None, None
                if settings.person_detection and not args.test_image:
                    from .person import create_detector

                    def post_person(present: bool, server=settings.server, token=token) -> None:
                        httpx.post(f"{server}/api/v1/devices/person", json={"present": present}, headers={"Authorization": f"Bearer {token}"}, timeout=5).raise_for_status()

                    person_reporter = PersonReporter(post_person).start()
                    detector = create_detector(settings.camera, settings.person_area, person_reporter)
                feed = PresenceFeed(settings.server, token).start()
                kiosk = Kiosk(api, camera, screen, keys, wait_seconds=args.wait_seconds,
                              test_mode=bool(args.test_image), display=display, strip_seconds=args.strip_seconds, report=report,
                              make_conversation=conversation, detector=detector, feed=feed)
                outcome = kiosk.run_once()
                feed.stop()
                if person_reporter is not None:
                    person_reporter.stop()
                if outcome == "setup":
                    report("setup")
                    menu.run()
            if args.once:
                return


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
