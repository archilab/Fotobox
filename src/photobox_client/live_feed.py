"""Real-time links between the box and the server (ADR-029, milestone D3).

`PresenceFeed` receives what is happening at the other boxes the moment it changes. `PersonReporter` tells
the server whether the camera seems to see someone. Both run beside the kiosk, never hold it up, and fail
quietly: without them the start screen simply asks the server every few seconds, as it did before.
"""
from __future__ import annotations
import json
import threading
from collections.abc import Callable
import httpx

RETRY_SECONDS = 3
READ_TIMEOUT_SECONDS = 40  # longer than the server's keep-alive


class PresenceFeed:
    def __init__(self, server: str, token: str, *, connect: Callable[..., httpx.Client] = httpx.Client) -> None:
        self.server, self.token, self.connect = server, token, connect
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.snapshot: dict | None = None
        self.version = 0
        self.connected = False

    def start(self) -> "PresenceFeed":
        threading.Thread(target=self._run, name="photobox-feed", daemon=True).start()
        return self

    def stop(self) -> None:
        self.stopped.set()

    def latest(self) -> tuple[int, dict | None]:
        with self.lock:
            return self.version, self.snapshot

    def _receive(self, line: str) -> None:
        if line.startswith("data: "):
            snapshot = json.loads(line[6:])
            with self.lock:
                self.snapshot, self.version = snapshot, self.version + 1

    def _run(self) -> None:
        while not self.stopped.is_set():
            try:
                with self.connect(base_url=self.server, timeout=httpx.Timeout(5, read=READ_TIMEOUT_SECONDS)) as client:
                    with client.stream("GET", "/api/v1/presence/stream", headers={"Authorization": f"Bearer {self.token}"}) as response:
                        response.raise_for_status()
                        self.connected = True
                        for line in response.iter_lines():
                            if self.stopped.is_set():
                                return
                            self._receive(line)
            except (httpx.HTTPError, ValueError):
                pass
            finally:
                self.connected = False
            self.stopped.wait(RETRY_SECONDS)


class PersonReporter:
    """Sends the newest answer, retrying until it arrives. The camera's thread only sets a value."""

    def __init__(self, post: Callable[[bool], None]) -> None:
        self.post = post
        self.lock = threading.Lock()
        self.wanted = False
        self.dirty = True  # a fresh start says "nobody", so a crash cannot leave a stale "someone"
        self.changed = threading.Event()
        self.changed.set()
        self.stopped = threading.Event()

    def start(self) -> "PersonReporter":
        threading.Thread(target=self._run, name="photobox-person-report", daemon=True).start()
        return self

    def stop(self) -> None:
        self.stopped.set()
        self.changed.set()

    def __call__(self, present: bool) -> None:
        with self.lock:
            self.wanted, self.dirty = present, True
        self.changed.set()

    def _run(self) -> None:
        while not self.stopped.is_set():
            self.changed.wait()
            self.changed.clear()
            while not self.stopped.is_set():
                with self.lock:
                    if not self.dirty:
                        break
                    value, self.dirty = self.wanted, False
                try:
                    self.post(value)
                except Exception:
                    with self.lock:
                        self.dirty = True
                    self.stopped.wait(RETRY_SECONDS)
