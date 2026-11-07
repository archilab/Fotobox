"""Keyboard kiosk for the box's screen (ADR-020).

ENTER starts and shows the server's consent notice. A second ENTER confirms it, and only then is
consent recorded and the photo taken, after a countdown. ESC cancels at the notice. While the strip
is generated, a timer runs on screen, and at the end the participant sees their withdrawal code.

    python -m photobox_client.kiosk --server http://MAC:8000 --token-file ~/.photobox.token --camera NAME

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
from .cli import idempotency_header, post_with_retry, read_token

ENTER, ESC = "enter", "esc"
NOTICE_TIMEOUT_SECONDS = 90
RESULT_SECONDS = 60
MESSAGE_SECONDS = 10
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


class TerminalScreen:
    def __init__(self, stream=sys.stdout) -> None:
        self.stream = stream

    def show(self, text: str) -> None:
        columns, rows = shutil.get_terminal_size((80, 24))
        # Keep clear of the edges, which many TVs cut off, and limit line length for reading.
        left, top = " " * max(2, columns // 12), max(1, rows // 10)
        width = max(30, min(columns - 2 * len(left), 90))
        lines = []
        for line in text.splitlines() or [""]:
            lines.extend(textwrap.wrap(line, width) or [""])
        self.stream.write("\x1b[2J\x1b[H" + "\n" * top + "\n".join(left + line for line in lines) + "\n")
        self.stream.flush()


class TerminalKeys:
    """Reads single key presses from a terminal in raw mode."""

    def __init__(self, fd: int) -> None:
        self.fd = fd

    def __enter__(self):
        self.saved = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        return self

    def __exit__(self, *_exc) -> None:
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)

    def flush(self) -> None:
        termios.tcflush(self.fd, termios.TCIFLUSH)

    def _read(self, timeout: float | None) -> bytes:
        ready, _, _ = select.select([self.fd], [], [], timeout)
        return os.read(self.fd, 32) if ready else b""

    def wait(self, accepted: set[str], timeout: float | None) -> str | None:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            if remaining == 0.0:
                return None
            data = self._read(remaining)
            if not data:
                continue
            # A lone ESC is the Escape key; ESC followed by more bytes is an arrow or function key.
            # Fast typing can arrive in one read, so other input is searched for an ENTER.
            if data == b"\x1b":
                key = ESC
            elif not data.startswith(b"\x1b") and (b"\r" in data or b"\n" in data):
                key = ENTER
            else:
                continue
            if key in accepted:
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

    def status(self, session_id: str) -> dict:
        response = self.client.get(f"/api/v1/sessions/{session_id}")
        response.raise_for_status()
        return response.json()


def big(number: int) -> str:
    return "\n\n" + "\n".join("        " + row.replace("#", "██") for row in BIG_DIGITS[number])


class Kiosk:
    def __init__(self, api: Api, camera: Callable[[], bytes], screen: Screen, keys: Keys, *,
                 wait_seconds: int = 1800, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, test_mode: bool = False) -> None:
        self.api, self.camera, self.screen, self.keys = api, camera, screen, keys
        self.wait_seconds, self.sleep, self.clock = wait_seconds, sleep, clock
        self.banner = "TEST MODE - no camera\n\n" if test_mode else ""

    def ask(self, text: str, accepted: set[str], timeout: float | None) -> str | None:
        self.keys.flush()
        self.screen.show(self.banner + text)
        return self.keys.wait(accepted, timeout)

    def message(self, text: str) -> None:
        self.ask(text + "\n\nENTER", {ENTER}, MESSAGE_SECONDS)

    def run_once(self) -> str:
        """One participant from start to finish. Returns how it ended."""
        self.ask("FOTOBOX\n\nDruecke ENTER zum Starten.\nPress ENTER to start.", {ENTER}, None)
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
            for number in COUNTDOWN:
                self.screen.show(self.banner + "Schau in die Kamera / Look at the camera" + big(number))
                self.sleep(1)
            self.screen.show(self.banner + "\n\n        * KLICK *")
            jpeg = self.camera()
            self.api.upload_and_process(session_id, jpeg)
        except CameraError:
            self.message("Die Kamera hat nicht funktioniert. Bitte melde dich bei der Betreuung.\nThe camera failed. Please ask the staff.")
            return "camera failed"
        except httpx.HTTPError:
            self.message("Verbindung zum Server fehlgeschlagen. Bitte melde dich bei der Betreuung.\nCould not reach the server. Please ask the staff.")
            return "network failed"
        return self.follow(session_id, code)

    def follow(self, session_id: str, code: str | None) -> str:
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
        withdraw = (f"Dein Widerrufscode / Your withdrawal code:\n\n        {code}\n\nNotiere ihn, um deine Bilder loeschen zu lassen.\nWrite it down to have your images deleted."
                    if code else "Zum Loeschen deiner Bilder wende dich an die Betreuung.\nTo have your images deleted, ask the staff.")
        self.ask(f"Fertig! Done!\n\n{withdraw}\n\nENTER", {ENTER}, RESULT_SECONDS)
        return "complete"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Photo booth keyboard kiosk")
    parser.add_argument("--server", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--camera", default="0")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--rotate", type=int, default=0)
    parser.add_argument("--wait-seconds", type=int, default=1800)
    parser.add_argument("--test-image", type=Path, help="Use this file instead of the camera, for tests without people")
    parser.add_argument("--once", action="store_true", help="Serve one participant, then exit")
    args = parser.parse_args(argv)
    token = read_token(args.token_file)
    if token is None:
        parser.error("the token file is empty")
    if args.test_image:
        camera = lambda: args.test_image.read_bytes()
    else:
        settings = CaptureSettings(width=args.width, height=args.height, rotation=args.rotate)
        camera = lambda: capture_jpeg(args.camera, settings)
    with httpx.Client(base_url=args.server, timeout=60, headers={"Authorization": f"Bearer {token}"}) as client, TerminalKeys(sys.stdin.fileno()) as keys:
        kiosk = Kiosk(Api(client), camera, TerminalScreen(), keys, wait_seconds=args.wait_seconds, test_mode=bool(args.test_image))
        while True:
            kiosk.run_once()
            if args.once:
                return


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
