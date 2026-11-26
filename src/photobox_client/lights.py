"""RGB status lights on the box (ADR-036).

Measured on the box: red, green and blue on GPIO18, 23 and 24 light when the pin is high, each through
its own resistor. They show what the booth is doing, in the colours the owner chose. A small thread
does the blinking; a change of state takes effect at once.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Protocol

CHIP = "/dev/gpiochip0"
SLOW, FAST = 1.0, 0.15  # seconds on and off
RED, GREEN, BLUE = (True, False, False), (False, True, False), (False, False, True)
WHITE, VIOLET, CYAN, DARK = (True, True, True), (True, False, True), (False, True, True), (False, False, False)

# name: (colour, seconds on, seconds off); no "off" time means steady.
PATTERNS: dict[str, tuple[tuple[bool, bool, bool], float, float]] = {
    "ready": (GREEN, 1, 0),            # the start screen
    "ready_live": (VIOLET, 1, 0),      # the start screen, while someone is live at another booth
    "invite": (VIOLET, FAST, FAST),    # another booth invites this one to a shared strip (ADR-037)
    "press": (GREEN, FAST, FAST),      # the booth waits for a press, such as agreeing
    "photo": (WHITE, 1, 0),            # countdown and photo
    "making": (BLUE, SLOW, SLOW),      # the strip is being made
    "live": (VIOLET, 1, 0),            # paired with someone at another booth
    "printing": (CYAN, 1, 0),
    "done": (GREEN, SLOW, SLOW),       # the strip and the withdrawal code
    "done_live": (VIOLET, SLOW, SLOW), # the same after a live pairing with another booth
    "rest": (GREEN, 0.3, 2.7),         # the resting screen: a short flash every 3 s (ADR-041)
    "rest_live": (VIOLET, 0.3, 2.7),   # the same while someone is live at another booth
    "error": (RED, 1, 0),              # not ready, or something failed
    "off": (DARK, 1, 0),               # the setup menu, and when the kiosk stops
}

# Most screens say which light they need; message screens are named by the kiosk (kiosk.py).
SCREEN_LIGHTS = {"start": "ready", "invite": "invite", "consent": "press", "countdown": "photo", "flash": "photo", "making": "making",
                 "live_moment": "live", "listening": "live", "converting": "live", "strip": "done", "done": "done"}


def light_for(state: dict, live_visit: bool = False) -> str | None:
    """The light for a screen, or None to leave it as it is.

    `live_visit`: this visitor was paired live with someone at another booth (ADR-024).
    """
    screen = state.get("screen", "")
    if screen == "making" and (state.get("encounter") or {}).get("kind") == "live":
        return "live"
    if screen == "start" and (state.get("badge") or {}).get("kind") == "live":
        return "ready_live"  # the server reports someone live at another booth
    if screen == "resting":
        return "rest_live" if state.get("live") else "rest"
    name = SCREEN_LIGHTS.get(screen)
    return "done_live" if name == "done" and live_visit else name


class Output(Protocol):
    def set(self, colour: tuple[bool, bool, bool]) -> None: ...
    def close(self) -> None: ...


class GpiodOutput:
    """Three output lines through the kernel's GPIO interface, dark at the start."""

    def __init__(self, lines: tuple[int, int, int], chip: str = CHIP) -> None:
        import gpiod
        from gpiod.line import Direction, Value
        self.lines, self.on, self.off = lines, Value.ACTIVE, Value.INACTIVE
        self.request = gpiod.request_lines(chip, consumer="photobox-lights", config={
            lines: gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.INACTIVE)})

    def set(self, colour: tuple[bool, bool, bool]) -> None:
        self.request.set_values({line: self.on if lit else self.off for line, lit in zip(self.lines, colour, strict=True)})

    def close(self) -> None:
        self.set(DARK)
        self.request.release()


class Lights:
    def __init__(self, output: Output) -> None:
        self.output = output
        self.name = "off"
        self.changed = threading.Event()
        self.stopping = False
        self.thread = threading.Thread(target=self._run, name="photobox-lights", daemon=True)

    def start(self) -> Lights:
        self.thread.start()
        return self

    def show(self, name: str | None) -> None:
        if name is None or name == self.name or name not in PATTERNS:
            return
        self.name = name
        self.changed.set()

    def stop(self) -> None:
        self.stopping = True
        self.changed.set()
        self.thread.join(timeout=2)
        try:
            self.output.close()
        except OSError:
            pass

    def _run(self) -> None:
        while not self.stopping:
            self.changed.clear()
            colour, on, off = PATTERNS[self.name]
            try:
                self.output.set(colour)
                if not off:
                    self.changed.wait()
                    continue
                if self.changed.wait(on):
                    continue
                self.output.set(DARK)
                self.changed.wait(off)
            except OSError:
                self.changed.wait(1)  # failing lights must never stop the booth


def open_lights(spec: str, make: Callable[[tuple[int, int, int]], Output] = GpiodOutput) -> Lights | None:
    """Lights on 'red,green,blue' GPIO numbers, such as '18,23,24', or None."""
    if not spec.strip():
        return None
    try:
        red, green, blue = (int(part) for part in spec.split(","))
        return Lights(make((red, green, blue))).start()
    except (ImportError, OSError, ValueError):
        return None
