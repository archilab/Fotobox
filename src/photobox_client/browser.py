"""Starts the browser that draws the box's screen, and keeps it running (ADR-028, milestone U3).

If it keeps failing, the kiosk exits with BROWSER_FAILED and deploy/pi/run-kiosk.sh starts the text kiosk,
so a broken browser never leaves visitors with a dead booth.
"""
from __future__ import annotations
import os
import subprocess
import threading
import time
from collections.abc import Callable

BROWSER_FAILED = 75
CONNECT_SECONDS = 60
MAX_RESTARTS = 3
RESTART_DELAY_SECONDS = 2
STABLE_SECONDS = 600  # one that ran this long and then stopped is a new problem, not the same one
FROZEN_SECONDS = 30  # a page that has not said it is alive for this long has frozen
START_GRACE_SECONDS = 60
CHECK_SECONDS = 5


class BrowserWatch:
    def __init__(self, command: str, url: str, *, popen: Callable[..., subprocess.Popen] = subprocess.Popen,
                 on_failure: Callable[[], None] = lambda: os._exit(BROWSER_FAILED),
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 alive_age: Callable[[], float | None] | None = None, on_restart: Callable[[], None] = lambda: None) -> None:
        self.command, self.url, self.popen, self.on_failure, self.sleep, self.clock = command, url, popen, on_failure, sleep, clock
        self.alive_age, self.on_restart = alive_age, on_restart
        self.process: subprocess.Popen | None = None
        self.stopping = False
        self.restarts = 0

    def _launch(self) -> bool:
        try:
            # The address holds the token, so nothing the browser prints is kept either.
            self.process = self.popen([self.command, self.url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            self.process = None
            return False
        return True

    def start(self) -> "BrowserWatch":
        threading.Thread(target=self._watch, name="photobox-browser", daemon=True).start()
        return self

    def _watch(self) -> None:
        while not self.stopping:
            started = self.clock()
            if self._launch():
                self._supervise(started)
            if self.stopping:
                return
            self.on_restart()
            self.restarts = 1 if self.clock() - started >= STABLE_SECONDS else self.restarts + 1
            if self.restarts > MAX_RESTARTS:
                self.on_failure()
                return
            self.sleep(RESTART_DELAY_SECONDS)

    def _supervise(self, started: float) -> None:
        """Until the browser ends. A page that has frozen is ended here, and started again by the caller."""
        while not self.stopping and self.process.poll() is None:
            if self.alive_age is not None and self.clock() - started > START_GRACE_SECONDS:
                age = self.alive_age()
                if age is not None and age > FROZEN_SECONDS:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                    return
            self.sleep(CHECK_SECONDS)

    def stop(self) -> None:
        self.stopping = True
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
