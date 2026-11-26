"""The browser screen's local server (ADR-028, milestone U2).

Standard library only, because the Pi's client should not depend on the server's web framework. It listens
on 127.0.0.1 alone, refuses any other Host header, and needs a token made fresh at every start for anything
that carries content or accepts a key. The browser only draws; every decision stays in the kiosk.

A key press names the screen it was pressed on. A press meant for an earlier screen is dropped, and a new
screen discards keys still waiting, so a double press can never count as agreeing to a notice the visitor
has not seen. The strip is held in memory only, while it is on screen.
"""
from __future__ import annotations
import io
import json
import secrets
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from PIL import Image

UI = Path(__file__).parent / "ui"
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/ui.css": ("ui.css", "text/css; charset=utf-8"),
    "/screens.js": ("screens.js", "text/javascript; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/video.js": ("video.js", "text/javascript; charset=utf-8"),
    "/sounds.js": ("sounds.js", "text/javascript; charset=utf-8"),
    "/fonts/inter-latin-wght-normal.woff2": ("fonts/inter-latin-wght-normal.woff2", "font/woff2"),
    # The logos on the resting screen (ADR-041), from the footer of midap.space.
    **{f"/logos/{name}.svg": (f"logos/{name}.svg", "image/svg+xml") for name in ("midap", "stil", "folkwang", "udk")},
}
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
# These redraw often. A key pressed a moment ago still belongs to what the visitor is looking at.
UPDATING = frozenset({"start", "invite", "resting", "making", "listening", "menu"})
NAMED_KEYS = frozenset({"enter", "esc", "backspace", "setup", "video"})
MAX_PENDING_KEYS = 32
MAX_BODY = 256
MAX_SIGNAL_BODY = 24_000  # one WebRTC setup message (V2, ADR-045)
SIGNAL_KINDS = frozenset({"offer", "answer", "candidate"})
KEEPALIVE_SECONDS = 15


class WebUI:
    """A screen and a keyboard for the kiosk, both reached through the browser."""

    def __init__(self, port: int = 0, token: str | None = None) -> None:
        self.port, self.token = port, token or secrets.token_urlsafe(24)
        self.changed = threading.Condition()
        self.state: dict | None = None
        self.revision = 0
        self.version = 0
        self.stopped = False
        self.pending: deque[str] = deque(maxlen=MAX_PENDING_KEYS)
        self.image: bytes | None = None
        self.image_number = 0
        self.server: ThreadingHTTPServer | None = None
        self.connected = threading.Event()  # a browser has opened the screen
        self.alive_at: float | None = None
        self.button_presses = 0  # ADR-036
        self.button_screen = 0
        self.signals_in: list[dict] = []  # from the other box, for the page (ADR-045)
        self.signals_out: deque[dict] = deque(maxlen=200)  # from the page, for the other box

    # Lifecycle

    def start(self) -> "WebUI":
        """Start listening. Raises OSError if the port is taken."""
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), _handler(self))
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, name="photobox-ui", daemon=True).start()
        return self

    def stop(self) -> None:
        with self.changed:
            self.stopped = True
            self.changed.notify_all()
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?t={self.token}"

    # The kiosk's screen

    def view(self, state: dict) -> None:
        public = {key: value for key, value in state.items() if key != "plain"}
        with self.changed:
            previous = self.state
            same = previous is not None and previous["screen"] == public["screen"]
            content_changed = previous is not None and {k: v for k, v in previous.items() if k != "id"} != public
            if not same or (public["screen"] not in UPDATING and content_changed):
                self.revision += 1
                self.pending.clear()
            self.state = public | {"id": self.revision}
            self.version += 1
            self.changed.notify_all()

    def show(self, text: str) -> None:
        """Plain text, used by the setup menu and the start-up messages."""
        self.view({"screen": "menu", "text": text})

    def put_image(self, data: bytes) -> str:
        """Hold the strip for the page. Returns its address. Raises ValueError if it is not a usable image."""
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
        except Exception as exc:
            raise ValueError("not a usable image") from exc
        with self.changed:
            self.image, self.image_number = data, self.image_number + 1
            return f"strip/{self.image_number}?t={self.token}"

    def clear_image(self) -> None:
        with self.changed:
            self.image = None

    # The kiosk's keyboard

    def button(self) -> None:
        """The push button. The page decides what it means, like ENTER: turn the notice's page or agree."""
        with self.changed:
            self.button_presses += 1
            self.button_screen = self.revision
            self.version += 1
            self.changed.notify_all()

    def hold(self) -> None:
        """The button held for 5 s on the start screen (ADR-045): a key of its own, not the page's to decide."""
        with self.changed:
            self.pending.append("video")
            self.changed.notify_all()

    # The video link's setup messages (ADR-045, V2). Picture and sound go between the browsers themselves.

    def push_signal(self, signal: dict) -> None:
        with self.changed:
            self.signals_in.append(signal)
            self.version += 1
            self.changed.notify_all()

    def take_signals(self) -> list[dict]:
        with self.changed:
            out = list(self.signals_out)
            self.signals_out.clear()
            return out

    def clear_signals(self) -> None:
        with self.changed:
            self.signals_in.clear()
            self.signals_out.clear()
            self.version += 1
            self.changed.notify_all()

    def press(self, screen_id: int, key: str) -> bool:
        """A key from the page. False if it was meant for a screen that is no longer showing."""
        with self.changed:
            if screen_id != self.revision:
                return False
            self.pending.append(key)
            self.changed.notify_all()
            return True

    def alive_age(self) -> float | None:
        """Seconds since the page last said it was running, or None if it never has."""
        return None if self.alive_at is None else time.monotonic() - self.alive_at

    def flush(self) -> None:
        with self.changed:
            self.pending.clear()

    def read_key(self, timeout: float | None) -> str | None:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self.changed:
            while not self.pending:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return None
                self.changed.wait(remaining)
            return self.pending.popleft()

    def wait(self, accepted: set[str], timeout: float | None) -> str | None:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            key = self.read_key(remaining)
            if key is None or key in accepted:
                return key


def _valid_key(key: object) -> bool:
    return isinstance(key, str) and (key in NAMED_KEYS or (len(key) == 1 and key.isprintable()))


def _handler(ui: WebUI) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args) -> None:
            pass  # addresses hold the token, and the browser's traffic is not worth keeping

        def _send(self, status: int, body: bytes = b"", content_type: str = "text/plain; charset=utf-8") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for name, value in SECURITY_HEADERS.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _local_host(self) -> bool:
            # Refuses another site's page that points its own name at this address (DNS rebinding).
            return self.headers.get("Host") in (f"127.0.0.1:{ui.port}", f"localhost:{ui.port}")

        def _authorized(self, query: dict) -> bool:
            token = self.headers.get("X-Photobox-Token") or (query.get("t") or [""])[0]
            return secrets.compare_digest(token.encode(), ui.token.encode())

        def do_GET(self) -> None:
            if not self._local_host():
                return self._send(403)
            parts = urlsplit(self.path)
            query = parse_qs(parts.query)
            if parts.path in STATIC:
                name, content_type = STATIC[parts.path]
                return self._send(200, (UI / name).read_bytes(), content_type)
            if not self._authorized(query):
                return self._send(403)
            if parts.path == "/events":
                return self._events()
            if parts.path.startswith("/strip/"):
                with ui.changed:
                    data = ui.image
                return self._send(200, data, "image/png") if data is not None else self._send(404)
            return self._send(404)

        def do_POST(self) -> None:
            parts = urlsplit(self.path)
            if not self._local_host() or not self._authorized({}):
                return self._send(403)
            if parts.path == "/alive":
                ui.alive_at = time.monotonic()
                return self._send(204)
            if parts.path == "/signal":
                return self._signal()
            if parts.path != "/key":
                return self._send(404)
            length = self.headers.get("Content-Length", "")
            if not length.isdigit() or int(length) > MAX_BODY:
                return self._send(400)
            try:
                body = json.loads(self.rfile.read(int(length)))
                screen_id, key = body["id"], body["key"]
            except (ValueError, KeyError, TypeError):
                return self._send(400)
            if not isinstance(screen_id, int) or not _valid_key(key):
                return self._send(400)
            return self._send(200 if ui.press(screen_id, key) else 409)

        def _signal(self) -> None:
            """A setup message from the page, only while the video link's screen shows."""
            length = self.headers.get("Content-Length", "")
            if not length.isdigit() or int(length) > MAX_SIGNAL_BODY:
                return self._send(400)
            try:
                body = json.loads(self.rfile.read(int(length)))
                kind, data = body["kind"], body["data"]
            except (ValueError, KeyError, TypeError):
                return self._send(400)
            if kind not in SIGNAL_KINDS or not isinstance(data, str):
                return self._send(400)
            with ui.changed:
                if not ui.state or ui.state.get("screen") != "video_link":
                    return self._send(409)
                ui.signals_out.append({"kind": kind, "data": data})
            return self._send(204)

        def _events(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            for name, value in SECURITY_HEADERS.items():
                self.send_header(name, value)
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            ui.connected.set()
            seen, shown_state, buttons, signals_sent = -1, None, None, 0
            try:
                while True:
                    with ui.changed:
                        if ui.version == seen and not ui.stopped:
                            ui.changed.wait(KEEPALIVE_SECONDS)
                        if ui.stopped:
                            return
                        state, changed = ui.state, ui.version != seen
                        seen = ui.version
                        pressed = buttons is not None and ui.button_presses != buttons
                        buttons, button_screen = ui.button_presses, ui.button_screen
                        if len(ui.signals_in) < signals_sent:
                            signals_sent = 0  # cleared for a new link
                        new_signals = ui.signals_in[signals_sent:]
                        signals_sent = len(ui.signals_in)
                    if state is not None and changed and state is not shown_state:
                        self.wfile.write(f"event: state\ndata: {json.dumps(state, ensure_ascii=False)}\n\n".encode())
                        shown_state = state
                    if pressed:
                        self.wfile.write(f"event: button\ndata: {json.dumps({'id': button_screen})}\n\n".encode())
                    for signal in new_signals:
                        self.wfile.write(f"event: signal\ndata: {json.dumps(signal)}\n\n".encode())
                    if not changed:
                        self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
            except OSError:
                return  # the browser went away

    return Handler
