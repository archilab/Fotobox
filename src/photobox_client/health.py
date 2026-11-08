"""Keeps the box observable and restartable when nobody is watching it (ADR-032).

A small file in memory says the kiosk is alive and what it is doing; a timer restarts the kiosk if the file
goes stale (deploy/pi/check-kiosk.sh). Health figures go to the server with each status report. They describe
the machine only: never anything about a visitor.
"""
from __future__ import annotations
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

HEARTBEAT = Path("/dev/shm/photobox-heartbeat")
WRITE_EVERY_SECONDS = 5
HOLD_EVERY_SECONDS = 10


class Heartbeat:
    """`beat` is called from the kiosk's own loop, so a hung kiosk stops beating."""

    def __init__(self, path: Path = HEARTBEAT, clock: Callable[[], float] = time.monotonic) -> None:
        self.path, self.clock = path, clock
        self.state = "idle"
        self.last = -WRITE_EVERY_SECONDS
        self.holding = threading.Event()

    def _write(self) -> None:
        try:
            self.path.write_text(self.state, encoding="utf-8")
        except OSError:
            pass  # a missing heartbeat must never be why the booth stops

    def beat(self) -> None:
        if self.clock() - self.last >= WRITE_EVERY_SECONDS:
            self.last = self.clock()
            self._write()

    def set_state(self, state: str) -> None:
        self.state = state
        self._write()

    def hold(self, during: Callable[[], None]) -> None:
        """Keep beating while someone uses the setup menu, which may rightly wait for a long time."""
        stop = threading.Event()

        def keep_beating() -> None:
            while not stop.wait(HOLD_EVERY_SECONDS):
                self._write()

        threading.Thread(target=keep_beating, name="photobox-heartbeat", daemon=True).start()
        self._write()
        try:
            during()
        finally:
            stop.set()


class Counters:
    """Trouble since the kiosk started."""

    def __init__(self, screen: str = "text") -> None:
        self.screen, self.browser_restarts, self.camera_failures = screen, 0, 0


def _read_number(path: str) -> float | None:
    try:
        return float(Path(path).read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _throttled(runner: Callable[..., subprocess.CompletedProcess]) -> bool | None:
    """Whether the Pi has lacked power or overheated since it started."""
    try:
        output = runner(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=3).stdout
        return int(output.strip().split("=")[1], 16) != 0
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None


def _available_memory_mb() -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def collect(counters: Counters, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> dict:
    temperature = _read_number("/sys/class/thermal/thermal_zone0/temp")
    uptime = _read_number("/proc/uptime")
    try:
        disk_free = shutil.disk_usage("/").free // (1024 * 1024)
    except OSError:
        disk_free = None
    return {
        "temperature_c": round(temperature / 1000, 1) if temperature is not None else None,
        "throttled": _throttled(runner),
        "disk_free_mb": disk_free,
        "memory_available_mb": _available_memory_mb(),
        "uptime_seconds": int(uptime) if uptime is not None else None,
        "screen": counters.screen,
        "browser_restarts": counters.browser_restarts,
        "camera_failures": counters.camera_failures,
    }
