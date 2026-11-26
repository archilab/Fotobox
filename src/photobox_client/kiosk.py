"""Keyboard kiosk for the box's screen (ADR-020).

ENTER starts and shows the server's consent notice, with the pool question if one is offered. A second
ENTER confirms both, and only then is consent recorded and the photo taken, after a countdown. Doing nothing
for 45 seconds cancels. While the strip
is generated, a timer runs on screen. At the end the finished strip is shown on the screen for 20
seconds, and then the withdrawal code for 10 seconds; ENTER gives more time, up to a minute. When the server
has printing switched on and a printer is connected, the strip is printed instead of shown (ADR-035); if that
fails, it is shown as before.

    python -m photobox_client.kiosk

Server address and camera come from ~/.photobox-kiosk.env, written by the setup menu (setup_menu.py),
which opens on Ctrl+Alt+S, on M during the first seconds after start, or by itself if the box cannot work.

Keys pressed before a question appears are discarded, so a double press can never count as consent.
"""
from __future__ import annotations
import argparse
import atexit
import os
import select
import shutil
import signal
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
from .camera import CameraError, CaptureSettings, capture_jpeg, reset_usb
from .display import FramebufferDisplay, result_screen
from .cli import idempotency_header, post_with_retry, read_token
from . import views
from .browser import BROWSER_FAILED, CONNECT_SECONDS, BrowserWatch
from .conversation import Conversation, speech_talker
from .health import Counters, Heartbeat, collect
from .live_feed import PersonReporter, PresenceFeed
from .printer import NoPaper, PrinterError, UsbPrinter, find_printer
from .button import Button, open_button
from .lights import light_for, open_lights

ENTER, ESC, BACKSPACE, SETUP = "enter", "esc", "backspace", "setup"
VIDEO = "video"  # the hidden 5 s hold on the start screen (ADR-045)
VIDEO_REQUEST = "video request"  # another booth asks this one for a video link
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
VIDEO_ASK_SECONDS = 30  # to read the video notice and agree; doing nothing cancels
VIDEO_POLL_SECONDS = 1.0
VIDEO_SIGNAL_SECONDS = 0.25  # how often setup messages are carried while a link starts
IDLE_MINUTES = 5  # the start screen rests after this long without a press (ADR-041)
REST_REFRESH_SECONDS = 5  # how often the resting screen looks whether someone is live elsewhere, for the light
RESTING = "resting"  # the detail the dashboard shows while the booth rests
CANCELLED = "Abgebrochen. Es wurde kein Foto gemacht.\nCancelled. No photo was taken."
START_TEXT = "Druecke ENTER zum Starten.\nPress ENTER to start."
PRINTING = "Dein Fotostreifen wird gedruckt.\nYour photo strip is being printed."
PRINT_SECONDS = 5  # time to take the strip from the printer before the code appears
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
        self.wake_read, self.wake_write = os.pipe()  # the button wakes a waiting read (ADR-036)
        os.set_blocking(self.wake_read, False)

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

    def button(self) -> None:
        """The push button: the text screen has no pages, so it is ENTER."""
        self.pending.append(ENTER)
        os.write(self.wake_write, b"!")

    def hold(self) -> None:
        """The button held for 5 s on the start screen (ADR-045)."""
        self.pending.append(VIDEO)
        os.write(self.wake_write, b"!")

    def _read(self, timeout: float | None) -> bytes:
        ready, _, _ = select.select([self.fd, self.wake_read], [], [], timeout)
        if self.wake_read in ready:
            try:
                os.read(self.wake_read, 64)
            except BlockingIOError:
                pass
        return os.read(self.fd, 32) if self.fd in ready else b""

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
        # Printing is switched on at the server only once the notices say that strips are printed (ADR-035).
        self.printing = bool(body.get("printing"))
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

    # The hidden video link (ADR-045). Each answer is {"link": ..., "now": the server's time}.

    def video_seconds(self) -> int | None:
        """How long a link may last, or None when the server has it switched off or cannot be reached."""
        try:
            response = self.client.get("/api/v1/video/current", timeout=5)
            return int(response.json()["link_seconds"]) if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return None

    def video_call(self) -> dict | None:
        """Ask a free box, after the visitor here has agreed. None if no box is free."""
        response = self.client.post("/api/v1/video/calls", json={"agreed": True}, timeout=5)
        if response.status_code == 409:
            return None
        response.raise_for_status()
        return response.json()

    def video_link(self, link_id: str) -> dict:
        response = self.client.get(f"/api/v1/video/calls/{link_id}", timeout=5)
        response.raise_for_status()
        return response.json()

    def video_accept(self, link_id: str) -> dict | None:
        """Someone here agrees to a request. None if it has ended meanwhile."""
        response = self.client.post(f"/api/v1/video/calls/{link_id}/accept", json={"agreed": True}, timeout=5)
        if response.status_code == 409:
            return None
        response.raise_for_status()
        return response.json()

    def video_end(self, link_id: str) -> None:
        self.client.post(f"/api/v1/video/calls/{link_id}/end", timeout=5)

    def video_send(self, link_id: str, kind: str, data: str) -> None:
        """A message that sets up picture and sound, for the other box (V2)."""
        self.client.post(f"/api/v1/video/calls/{link_id}/signals", json={"kind": kind, "data": data}, timeout=5).raise_for_status()

    def video_signals(self, link_id: str, after: int) -> list[dict]:
        response = self.client.get(f"/api/v1/video/calls/{link_id}/signals", params={"after": after}, timeout=5)
        response.raise_for_status()
        return response.json()["signals"]

    def messaging_events(self) -> list[dict]:
        response = self.client.get("/api/v1/messaging/events", timeout=5)
        response.raise_for_status()
        return response.json()["events"]

    def send_message(self, session_id: str, match_id: str, message_id: str, text: str) -> dict:
        response = self.client.post(f"/api/v1/sessions/{session_id}/messages", json={"message_id": message_id, "match_id": match_id, "text": text}, timeout=5)
        response.raise_for_status()
        return response.json()

    def strip(self, session_id: str, profile: str = "color") -> bytes:
        response = self.client.get(f"/api/v1/sessions/{session_id}/result", params={"profile": profile})
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

    def __init__(self, post: Callable[[dict], None], health: Callable[[], dict] | None = None) -> None:
        import threading
        self.post, self.health = post, health
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
                body = {"state": self.state, "detail": self.detail}
                if self.health is not None:
                    body["health"] = self.health()
                self.post(body)
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
                 detector: "PersonDetector | None" = None, feed: "PresenceFeed | None" = None,
                 heartbeat: Callable[[], None] = lambda: None, on_camera_error: Callable[[], None] = lambda: None,
                 printer: Callable[[], UsbPrinter | None] = lambda: None,
                 on_print: Callable[[str], None] = lambda _state: None, button: bool = False,
                 light: Callable[[str | None], None] = lambda _name: None, idle_seconds: float = 0,
                 idle_address: str | None = None, hold_mode: Callable[[bool], None] = lambda _on: None) -> None:
        self.api, self.camera, self.screen, self.keys, self.report = api, camera, screen, keys, report
        self.display, self.strip_seconds = display, strip_seconds
        self.wait_seconds, self.sleep, self.clock = wait_seconds, sleep, clock
        self.banner = "TEST MODE - no camera\n\n" if test_mode else ""
        self.pooled_until: str | None = None
        self.make_conversation = make_conversation
        self.detector, self.feed = detector, feed
        self.heartbeat, self.on_camera_error = heartbeat, on_camera_error
        self.printer, self.on_print = printer, on_print
        self.button = button  # a push button works like ENTER, and the screens say so (ADR-036)
        self.light = light  # the status lights follow the screens (ADR-036)
        self.live_visit = False  # this visitor was paired live with someone at another booth
        self.label: str | None = None
        self.idle_seconds = idle_seconds  # 0: the booth never rests (ADR-041)
        self.idle_address = idle_address  # the website the resting screen shows as a QR code
        self.hold_mode = hold_mode  # the button tells a 5 s hold from a press, on the start screen only (ADR-045)
        self.video_incoming: dict | None = None

    def _camera_looks(self) -> bool:
        """Whether the camera watches for people now, which the start screen must say; the distance sensor sees no picture."""
        return self.detector is not None and getattr(self.detector, "uses_camera", True)

    def present(self, state: dict, light: str | None = None) -> None:
        """Show a screen. Every screen carries the box's name and whether this is a test.

        `light` names the status light where the screen alone does not say, as for messages.
        """
        self.light(light or light_for(state, self.live_visit))
        self.label = state.get("label", self.label)
        self.heartbeat()
        plain = self.banner + state["plain"]
        if self.button:
            plain = plain.replace("ENTER", "KNOPF/ENTER")
        views.present(self.screen, state | {"label": self.label, "test_mode": bool(self.banner), "button": self.button, "plain": plain})

    def ask(self, state: dict, accepted: set[str], timeout: float | None, light: str | None = None) -> str | None:
        self.keys.flush()
        self.present(state, light)
        return self.keys.wait(accepted, timeout)

    def message(self, text: str, light: str = "error") -> None:
        self.ask(views.message(text, key=True), {ENTER}, MESSAGE_SECONDS, light)

    def start_screen(self) -> str:
        """Wait for ENTER or the setup keys, refreshing what is happening at the other boxes.

        After `idle_seconds` on this screen without a press, the booth rests (ADR-041): it shows the resting
        screen, the camera stops looking, and only a press brings the start screen back. That press is never
        consent; agreeing still takes its own press on the notice.
        """
        self.keys.flush()
        # Only here, never during a visit, and the camera is free again before anything else happens (ADR-029).
        if self.detector is not None:
            self.detector.resume()
        shown_version = None
        since = self.clock()
        try:
            while True:
                self.heartbeat()
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
                    self.present(views.start(snapshot, "FOTOBOX\n\n" + (presence + "\n\n" if presence else "") + START_TEXT, detecting=self._camera_looks()))
                    shown_version = version if live else None
                    if views.invitation_seconds(snapshot):
                        since = self.clock()  # never rest while another booth invites this one
                timeout = FEED_CHECK_SECONDS if live else PRESENCE_REFRESH_SECONDS
                if self.idle_seconds:
                    left = self.idle_seconds - (self.clock() - since)
                    if left <= 0:
                        if self.rest() == SETUP:
                            return SETUP
                        self.keys.flush()
                        shown_version, since = None, self.clock()
                        continue
                    timeout = min(timeout, left)
                incoming = (snapshot or {}).get("video") if (snapshot or {}).get("video") else None
                if incoming and incoming.get("role") == "callee" and incoming.get("status") == "requested":
                    self.video_incoming = {"link": incoming, "now": (snapshot or {}).get("generated_at")}
                    return VIDEO_REQUEST
                self.hold_mode(True)
                key = self.keys.wait({ENTER, SETUP, VIDEO}, timeout)
                if key is not None:
                    return key
        finally:
            self.hold_mode(False)
            if self.detector is not None:
                self.detector.pause()

    def rest(self) -> str:
        """The resting screen, until a press (ENTER or the button) or the setup keys. Returns which."""
        self.hold_mode(False)  # any press wakes the booth at once
        if self.detector is not None:
            self.detector.pause()  # the resting screen does not say that the camera looks, so it must not
        self.report("idle", RESTING)
        shown = None
        try:
            while True:
                self.heartbeat()
                try:
                    snapshot = self.feed.latest()[1] if self.feed is not None and self.feed.connected else self.api.presence()
                except (httpx.HTTPError, ValueError, KeyError, TypeError):
                    snapshot = None
                state = views.resting(views.someone_live(snapshot), self.idle_address)
                if state != shown:
                    self.keys.flush()
                    self.present(state)
                    shown = state
                key = self.keys.wait({ENTER, SETUP}, REST_REFRESH_SECONDS)
                if key is not None:
                    return key
        finally:
            self.report("idle")
            if self.detector is not None:
                self.detector.resume()

    # The hidden video link (ADR-045)

    def video_call(self) -> str:
        """This booth's visitor held the button for 5 s: agree, then call a free booth."""
        seconds = self.api.video_seconds()
        if seconds is None:
            return "start"  # switched off: the hold does nothing, and nothing gives the secret away
        self.report("consent")
        if self.ask(views.video_ask(VIDEO_ASK_SECONDS, seconds), {ENTER}, VIDEO_ASK_SECONDS) != ENTER:
            return "video declined"
        try:
            answer = self.api.video_call()
        except httpx.HTTPError:
            self.message("Das geht gerade nicht.\nThat is not possible right now.", light="ready")
            return "video failed"
        if answer is None:
            self.message("Gerade ist keine andere Fotobox frei.\nNo other photo booth is free right now.", light="ready")
            return "video busy"
        link = answer["link"]
        self.report("showing", "video link")
        state = views.video_calling(link.get("place"))
        while True:
            self.present(state)
            if self.keys.wait({ENTER}, VIDEO_POLL_SECONDS) == ENTER:
                self.api.video_end(link["id"])
                return "video cancelled"
            try:
                answer = self.api.video_link(link["id"])
            except httpx.HTTPError:
                continue
            link = answer["link"]
            if link["status"] == "connected":
                return self.video_live(answer)
            if link["status"] == "ended":
                self.message("Niemand hat geantwortet.\nNobody answered.", light="ready")
                return "video unanswered"

    def video_answer(self) -> str:
        """Another booth asks this one: someone here agrees with one press, or the request runs out."""
        answer, self.video_incoming = self.video_incoming, None
        link = answer["link"]
        seconds = views.seconds_until(link, answer["now"])
        if not seconds:
            return "video unanswered"
        self.report("consent")
        if self.ask(views.video_request(link.get("place"), seconds, link.get("link_seconds", 120)), {ENTER}, seconds) != ENTER:
            return "video unanswered"
        try:
            accepted = self.api.video_accept(link["id"])
        except httpx.HTTPError:
            accepted = None
        if accepted is None:
            self.message("Die Anfrage ist schon vorbei.\nThe request has already ended.", light="ready")
            return "video too late"
        self.report("showing", "video link")
        return self.video_live(accepted)

    def video_live(self, answer: dict) -> str:
        """The link, until either side ends it or its time is up.

        Picture and sound go directly between the two boxes' browsers; this only carries the messages that set
        them up, between this box's page and the server (V2). The camera is free: the start screen let it go.
        """
        link = answer["link"]
        seconds = views.seconds_until(link, answer["now"])
        ends = self.clock() + seconds
        page = self.screen if hasattr(self.screen, "take_signals") else None
        if page is not None:
            page.clear_signals()
        self.present(views.video_link(link.get("place"), seconds, link["id"], link["role"], media=page is not None))
        received, next_check = 0, 0.0
        try:
            while True:
                now = self.clock()
                left = ends - now
                if left <= 0:
                    return "video done"
                if self.keys.wait({ENTER}, min(VIDEO_SIGNAL_SECONDS, left)) == ENTER:
                    self.api.video_end(link["id"])
                    return "video ended"
                try:
                    if page is not None:
                        for signal in page.take_signals():
                            self.api.video_send(link["id"], signal["kind"], signal["data"])
                        for signal in self.api.video_signals(link["id"], received):
                            page.push_signal({"kind": signal["kind"], "data": signal["data"]})
                            received = max(received, signal["n"])
                    if now >= next_check:
                        next_check = now + VIDEO_POLL_SECONDS
                        if self.api.video_link(link["id"])["link"]["status"] == "ended":
                            return "video done"
                except httpx.HTTPError:
                    pass  # the time limit still ends it
        finally:
            if page is not None:
                page.clear_signals()

    def run_once(self) -> str:
        """One participant from start to finish. Returns how it ended."""
        self.report("idle")
        key = self.start_screen()
        if key == SETUP:
            return "setup"
        if key == VIDEO:
            return self.video_call()
        if key == VIDEO_REQUEST:
            return self.video_answer()
        self.report("consent")
        try:
            version, text, pool = self.api.notice()
        except httpx.HTTPError:
            self.message("Die Fotobox ist gerade nicht bereit.\nThe photo booth is not ready right now.")
            return "not ready"
        # One question and one key, for the photo and the pool together; doing nothing cancels (ADR-030).
        if self.ask(views.consent(text, pool, CONSENT_SECONDS), {ENTER}, CONSENT_SECONDS) != ENTER:
            self.message(CANCELLED, light="ready")
            return "declined"
        self.pooled_until = pool["until_text"] if pool else None
        self.live_visit = False
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
            self.on_camera_error()
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
        # Time left: from when pictures started to be made and how long the finished ones took (ADR-040).
        running_since, last_done, last_change = None, 0, None
        while True:
            try:
                state = self.api.status(session_id)
            except httpx.HTTPError:
                state = {"status": "unknown", "job": None}
            elapsed = int(self.clock() - started)
            job = state.get("job") or {}
            if (state.get("match") or {}).get("kind") == "live":
                self.live_visit = True  # also when the first answer already says complete
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
            now = self.clock()
            done = job.get("frames_done", 0) or 0
            if job.get("status") == "running" and running_since is None:
                running_since = now
            if done != last_done:
                last_done, last_change = done, now
            remaining = None
            if running_since is not None and done and job.get("frames_total"):
                per_picture = (last_change - running_since) / done
                remaining = max(0.0, per_picture * (job["frames_total"] - done) - (now - last_change))
            encounter = encounter_text(state, waited)
            spoken = None
            if conversation is not None:
                conversation.poll()
                talk = conversation.text().translate(PLAIN_LETTERS)
                encounter = f"{encounter}\n\n{talk}" if talk and encounter else (talk or encounter)
                spoken = conversation.view()
            self.present(views.making(elapsed, job, views.encounter(state, waited, spoken), remaining=remaining, plain=(
                "Dein Fotostreifen wird erstellt.\nYour photo strip is being made.\n\n"
                + (encounter + "\n\n" if encounter else "")
                + f"        {elapsed // 60:02d}:{elapsed % 60:02d}\n\n        {frames}"
            )))
            if conversation is not None:
                conversation.wait(1)
            else:
                self.sleep(1)
        if not self.print_strip(session_id):
            self.show_strip(session_id, code)
        withdraw = (f"Dein Widerrufscode / Your withdrawal code:\n\n        {code}\n\nNotiere ihn, um deine Bilder loeschen zu lassen.\nWrite it down to have your images deleted."
                    if code else "Zum Loeschen deiner Bilder wende dich an die Betreuung.\nTo have your images deleted, ask the staff.")
        if self.pooled_until:
            withdraw += (f"\n\nDein Foto bleibt bis {self.pooled_until} im Pool. Der Code loescht es auch dort."
                         f"\nYour photo stays in the pool until {self.pooled_until}. The code deletes it there too.")
        # The code is on a public screen: briefly, and longer only if the visitor asks (at most a minute).
        for _ in range(1 + CODE_MORE_TIMES):
            state = views.done(code, self.pooled_until, f"Fertig! Done!\n\n{withdraw}\n\nENTER = mehr Zeit / more time", CODE_SECONDS) | {"live": self.live_visit}
            if self.ask(state, {ENTER}, CODE_SECONDS) != ENTER:
                break
        return "complete"


    def print_strip(self, session_id: str) -> bool:
        """Print the strip once, if the server allows printing and a printer is connected (ADR-035).

        Returns False when nothing was printed, so the strip is shown on the screen instead.
        """
        if not getattr(self.api, "printing", False):
            return False
        printer = self.printer()
        if printer is None:
            return False
        self.report("showing")
        self.present(views.message(PRINTING), light="printing")
        try:
            if printer.status() == "no paper":
                raise NoPaper("no paper")
            printer.print_strip(self.api.strip(session_id, profile="thermal"))
        except NoPaper:
            self.on_print("no paper")
            return False
        except (PrinterError, httpx.HTTPError):
            self.on_print("error")
            return False
        self.on_print("ready")
        self.sleep(PRINT_SECONDS)
        return True

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


def serve(kiosk: Kiosk, button: Button | None) -> str:
    """One visitor, with the push button working only meanwhile: never in the setup menu (ADR-036)."""
    if button is not None:
        button.enabled.set()
    try:
        return kiosk.run_once()
    finally:
        if button is not None:
            button.enabled.clear()


BOOT_SETUP_SECONDS = 10
NOT_READY_RETRY_SECONDS = 30


def main(argv: list[str] | None = None) -> None:
    from .network import Wifi
    from .settings import Pin, Settings
    from .setup_menu import SetupMenu, blocking_problem, diagnose, requires_setup

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

    heartbeat = Heartbeat()
    counters = Counters()

    def note_browser_restart() -> None:
        counters.browser_restarts += 1

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
            watch = BrowserWatch(args.browser, web.url, alive_age=web.alive_age, on_restart=note_browser_restart).start()
            if not web.connected.wait(CONNECT_SECONDS):
                print("The browser did not open the screen in time.", file=sys.stderr, flush=True)
                watch.stop()
                raise SystemExit(BROWSER_FAILED)
            print(f"Browser screen ready after {time.monotonic() - started:.1f} s", file=sys.stderr, flush=True)
        elif web is not None:
            print(f"Open the screen at {web.url}", flush=True)
    counters.screen = "browser" if web is not None else "text"
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
            reporter = StatusReporter(post, health=lambda: collect(counters)).start()
        return reporter
    # The browser draws the strip itself, and has the screen to itself.
    display = None if args.no_display or web is not None else FramebufferDisplay.open()

    def printed(state: str) -> None:
        counters.printer = state
        if state != "ready":
            counters.print_failures += 1

    def camera_trouble(camera_name: str) -> None:
        counters.camera_failures += 1
        reset_usb(camera_name)

    with (nullcontext(web) if web is not None else TerminalKeys(sys.stdin.fileno())) as keys:
        menu = SetupMenu(screen, keys, settings_path=args.config, token_path=args.token_file, pin=Pin(args.pin_file), wifi=wifi, display=display)
        button: Button | None = None
        button_gpio = None
        lights = open_lights(current()[0].lights)
        light = lights.show if lights is not None else (lambda _name: None)
        if lights is not None:
            # systemd stops the kiosk with SIGTERM, and the end of its login session on tty1 sends SIGHUP.
            # Ending through SystemExit lets the lights go dark; a released line keeps its last level.
            for stop in (signal.SIGTERM, signal.SIGHUP):
                signal.signal(stop, lambda *_args: sys.exit(0))
            atexit.register(lights.stop)

        def in_menu(during) -> None:
            light("off")
            heartbeat.hold(during)
        keys.flush()
        screen.show(f"FOTOBOX\n\nStarting ...\n\nM = Einrichtung / setup  ({BOOT_SETUP_SECONDS} s)")
        heartbeat.beat()
        if keys.wait({"m", "M", SETUP}, BOOT_SETUP_SECONDS):
            in_menu(menu.run)
        while True:
            heartbeat.beat()
            settings, token = current()
            base_report = reporting(settings, token) or (lambda *_args: None)

            def report(state: str, detail: str | None = None, send=base_report) -> None:
                heartbeat.set_state(state)
                send(state, detail)

            checks = diagnose(settings, token, wifi, httpx.Client)
            problem = blocking_problem(checks)
            if problem:
                report("not ready", problem[:120])
                light("error")
                # Only a person can fix these. A network or server that is away may come back, so the box
                # must not sit waiting for a PIN when nobody is there.
                if requires_setup(checks):
                    in_menu(lambda reason=f"The photo booth cannot start. {problem}": menu.run(reason=reason))
                    settings, token = current()
                    problem = blocking_problem(diagnose(settings, token, wifi, httpx.Client))
                if problem:
                    keys.flush()
                    screen.show(f"FOTOBOX\n\nNicht bereit / Not ready\n{problem}\n\nCtrl+Alt+S = Einrichtung / setup")
                    if keys.wait({SETUP}, NOT_READY_RETRY_SECONDS) == SETUP:
                        in_menu(menu.run)
                    continue
            if args.test_image:
                camera = lambda: args.test_image.read_bytes()
            else:
                capture = CaptureSettings(width=args.width, height=args.height, rotation=args.rotate)
                camera = lambda: capture_jpeg(settings.camera, capture)
            if settings.button != button_gpio:
                if button is not None:
                    button.stop()
                button, button_gpio = open_button(settings.button, keys.button, on_hold=keys.hold), settings.button
            with httpx.Client(base_url=settings.server, timeout=60, headers={"Authorization": f"Bearer {token}"}) as client:
                api = Api(client)
                talker = speech_talker(settings.microphone, keys, screen)

                def conversation(session_id: str) -> Conversation:
                    return Conversation(api, session_id, keys, talker, time.monotonic)

                # The test image stands in for the camera, so the camera has nothing to look at then; the
                # distance sensor sees no picture and works either way (ADR-046).
                detector, person_reporter = None, None
                use_tof = settings.person_sensor == "tof"
                if settings.person_detection and (use_tof or not args.test_image):
                    def post_person(present: bool, server=settings.server, token=token) -> None:
                        httpx.post(f"{server}/api/v1/devices/person", json={"present": present}, headers={"Authorization": f"Bearer {token}"}, timeout=5).raise_for_status()

                    person_reporter = PersonReporter(post_person).start()
                    if use_tof:
                        from .tof import create_tof_detector
                        detector = create_tof_detector(settings.tof_range_mm, person_reporter)
                    else:
                        from .person import create_detector
                        detector = create_detector(settings.camera, settings.person_area, person_reporter)
                feed = PresenceFeed(settings.server, token).start()
                connected = find_printer()
                counters.printer = "none" if connected is None else {"no paper": "no paper"}.get(connected.status(), "ready")
                kiosk = Kiosk(api, camera, screen, keys, wait_seconds=args.wait_seconds,
                              test_mode=bool(args.test_image), display=display, strip_seconds=args.strip_seconds, report=report,
                              make_conversation=conversation, detector=detector, feed=feed, heartbeat=heartbeat.beat,
                              on_camera_error=lambda camera_name=settings.camera: camera_trouble(camera_name),
                              printer=find_printer, on_print=printed, button=button is not None, light=light,
                              idle_seconds=settings.idle_minutes * 60, idle_address=settings.idle_address,
                              hold_mode=lambda on, b=button: b is not None and (b.holding.set() if on else b.holding.clear()))
                outcome = serve(kiosk, button)
                feed.stop()
                if person_reporter is not None:
                    person_reporter.stop()
                if outcome == "setup":
                    report("setup")
                    in_menu(menu.run)
            if args.once:
                return


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
