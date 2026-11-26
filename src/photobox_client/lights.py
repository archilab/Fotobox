"""RGB status lights on the box (ADR-036, ADR-044).

Measured on the box: red, green and blue on GPIO18, 23 and 24 light when the pin is high, each through
its own resistor. They show what the booth is doing, in the colours the owner chose. A small thread
does the blinking and fading; a change of state takes effect at once.

With the system's lgpio (Raspberry Pi OS package python3-lgpio) each colour is dimmed by software PWM in
lgpio's own C thread: patterns fade, and violet is mixed as the owner picked it at the booth. Without it,
the lines are switched through gpiod, fades become blinks and violet is red and blue at full.
"""
from __future__ import annotations

import math
import threading
from collections.abc import Callable
from typing import Protocol

CHIP = "/dev/gpiochip0"
PWM_HZ = 800  # fast enough not to flicker to the eye
FADE_STEP = 0.025  # seconds between brightness steps while fading
SLOW, FAST = 1.0, 0.15  # seconds on and off
Colour = tuple[float, float, float]  # red, green, blue, each 0 to 1
RED, GREEN, BLUE = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
# Violet: red at a quarter and blue full, picked by the owner at newbox on 2026-11-26 from four mixes;
# red and blue both full looked pink.
WHITE, VIOLET, CYAN, DARK = (1.0, 1.0, 1.0), (0.25, 0.0, 1.0), (0.0, 1.0, 1.0), (0.0, 0.0, 0.0)

# name: (colour, seconds on, seconds off); no "off" time means steady.
PATTERNS: dict[str, tuple[Colour, float, float]] = {
    "ready": (GREEN, 1, 0),            # the start screen
    "ready_live": (VIOLET, 1, 0),      # the start screen, while someone is live at another booth
    "invite": (VIOLET, FAST, FAST),    # another booth invites this one to a shared strip (ADR-037): blinks, it is urgent
    "press": (GREEN, 0.8, 0.8),        # the booth waits for a press, such as agreeing: in step with the button on screen
    "photo": (WHITE, 1, 0),            # countdown and photo
    "making": (BLUE, SLOW, SLOW),      # the strip is being made
    "live": (VIOLET, 1, 0),            # paired with someone at another booth
    "printing": (CYAN, 1, 0),
    "done": (GREEN, SLOW, SLOW),       # the strip and the withdrawal code
    "done_live": (VIOLET, SLOW, SLOW), # the same after a live pairing with another booth
    "rest": (GREEN, 2, 2),             # the resting screen: slow breathing (ADR-041)
    "rest_live": (VIOLET, 2, 2),       # the same while someone is live at another booth
    "error": (RED, 1, 0),              # not ready, or something failed
    "off": (DARK, 1, 0),               # the setup menu, and when the kiosk stops
}
# These fade in and out over on + off seconds where the lights can dim, and blink where they cannot (ADR-044).
FADING = frozenset({"press", "making", "done", "done_live", "rest", "rest_live"})


def fade_level(t: float, period: float) -> float:
    """Brightness from 0 to 1 at time t of a fade: dark at the start, full halfway; squared, so it looks even."""
    wave = (1 - math.cos(2 * math.pi * (t % period) / period)) / 2
    return wave * wave


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
    dims: bool  # True when levels between 0 and 1 are shown as such

    def set(self, colour: Colour) -> None: ...
    def close(self) -> None: ...


class GpiodOutput:
    """Three output lines through the kernel's GPIO interface, dark at the start. On or off only."""

    dims = False

    def __init__(self, lines: tuple[int, int, int], chip: str = CHIP) -> None:
        import gpiod
        from gpiod.line import Direction, Value
        self.lines, self.on, self.off = lines, Value.ACTIVE, Value.INACTIVE
        self.request = gpiod.request_lines(chip, consumer="photobox-lights", config={
            lines: gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.INACTIVE)})

    def set(self, colour: Colour) -> None:
        self.request.set_values({line: self.on if level > 0 else self.off for line, level in zip(self.lines, colour, strict=True)})

    def close(self) -> None:
        self.set(DARK)
        self.request.release()


class LgpioOutput:
    """Three lines dimmed by lgpio's software PWM, dark at the start."""

    dims = True

    def __init__(self, lines: tuple[int, int, int], chip: str = CHIP, lgpio=None) -> None:
        if lgpio is None:
            import lgpio
        self.lgpio, self.lines = lgpio, lines
        self.handle = lgpio.gpiochip_open(int(chip.rsplit("gpiochip", 1)[1]))
        self.duty: dict[int, float] = {}
        try:
            for line in lines:
                lgpio.gpio_claim_output(self.handle, line, 0)
        except Exception:
            lgpio.gpiochip_close(self.handle)
            raise
        self.set(DARK)

    def set(self, colour: Colour) -> None:
        for line, level in zip(self.lines, colour, strict=True):
            duty = round(max(0.0, min(1.0, level)) * 100, 1)
            before = self.duty.get(line)
            if before == duty:
                continue  # only changes, so a steady light is never restarted
            if duty in (0.0, 100.0):
                # Fully on or off needs no PWM, which costs CPU in lgpio's threads: switch the line itself.
                if before not in (None, 0.0, 100.0):
                    self.lgpio.tx_pwm(self.handle, line, 0, 0)
                self.lgpio.gpio_write(self.handle, line, 1 if duty else 0)
            else:
                self.lgpio.tx_pwm(self.handle, line, PWM_HZ, duty)
            self.duty[line] = duty

    def close(self) -> None:
        try:
            self.set(DARK)
            for line in self.lines:
                self.lgpio.gpio_free(self.handle, line)
        finally:
            self.lgpio.gpiochip_close(self.handle)


def default_output(lines: tuple[int, int, int]) -> Output:
    """lgpio where the system has it, otherwise gpiod."""
    try:
        return LgpioOutput(lines)
    except Exception:  # not installed, or lgpio's own error (lgpio.error) such as a line in use
        return GpiodOutput(lines)


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
        except Exception:
            pass

    def _run(self) -> None:
        import time
        while not self.stopping:
            self.changed.clear()
            name = self.name
            colour, on, off = PATTERNS[name]
            try:
                if not off:
                    self.output.set(colour)
                    self.changed.wait()
                    continue
                if name in FADING and getattr(self.output, "dims", False):
                    started = time.monotonic()
                    while not self.changed.wait(FADE_STEP):
                        level = fade_level(time.monotonic() - started, on + off)
                        self.output.set(tuple(c * level for c in colour))
                    continue
                self.output.set(colour)
                if self.changed.wait(on):
                    continue
                self.output.set(DARK)
                self.changed.wait(off)
            except Exception:  # OSError from gpiod, lgpio.error from lgpio
                self.changed.wait(1)  # failing lights must never stop the booth


def open_lights(spec: str, make: Callable[[tuple[int, int, int]], Output] = default_output) -> Lights | None:
    """Lights on 'red,green,blue' GPIO numbers, such as '18,23,24', or None."""
    if not spec.strip():
        return None
    try:
        red, green, blue = (int(part) for part in spec.split(","))
        return Lights(make((red, green, blue))).start()
    except (ImportError, OSError, ValueError):
        return None
