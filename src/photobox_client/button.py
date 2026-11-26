"""A push button that works like ENTER in booth mode (ADR-036).

The button connects a GPIO line to ground, read with the internal pull-up, so pressed reads low. Measured
on the box: it bounces for under a millisecond when pressed. A press counts once, when the button goes
down after having been up; holding it gives no further presses, so a jammed button cannot agree to
anything after the screen it was pressed on. It works only while the booth serves visitors, never in
the setup menu.

Where the press goes depends on the screen: the browser screen gets it as its own event, tied to the
screen showing at that moment, so the page's rule that a notice must be read to its last page applies
to the button too (webui.py, ui/app.js). The text screen takes it as ENTER.

On the start screen the kiosk sets `holding`: a press then counts when the button is let go, and holding
it for HOLD_SECONDS calls `on_hold` instead, once, with no press when it is let go (ADR-045).
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Protocol

DEBOUNCE_SECONDS = 0.05
STUCK_SECONDS = 5.0
HOLD_SECONDS = 5.0  # the hidden long press on the start screen (ADR-045)
CHIP = "/dev/gpiochip0"


class Line(Protocol):
    def wait(self, timeout: float) -> bool: ...
    def edges(self) -> list[tuple[bool, float]]: ...  # (pressed, monotonic seconds)
    def pressed(self) -> bool: ...
    def close(self) -> None: ...


class PressFilter:
    """Turns the line's raw edges into presses."""

    def __init__(self) -> None:
        self.down = False
        self.changed_at = float("-inf")
        self.down_since: float | None = None

    def edge(self, pressed: bool, at: float) -> bool:
        """True if this edge is a new press."""
        if at - self.changed_at < DEBOUNCE_SECONDS or pressed == self.down:
            return False  # bounce, or no change
        self.down, self.changed_at = pressed, at
        self.down_since = at if pressed else None
        return pressed

    def settle(self, pressed: bool, at: float) -> None:
        """Take the line's level once it has been quiet, without counting a press.

        A very short tap can end inside the bounce window, and the release would otherwise be missed.
        """
        if pressed != self.down and at - self.changed_at >= DEBOUNCE_SECONDS:
            self.down, self.changed_at = pressed, at
            self.down_since = at if pressed else None

    def stuck(self, now: float) -> bool:
        return self.down_since is not None and now - self.down_since >= STUCK_SECONDS


class GpiodLine:
    """One input line through the kernel's GPIO interface, with the internal pull-up."""

    def __init__(self, line: int, chip: str = CHIP) -> None:
        import gpiod
        from gpiod.line import Bias, Direction, Edge, Value
        self.line, self.low = line, Value.INACTIVE
        self.request = gpiod.request_lines(chip, consumer="photobox-button", config={
            line: gpiod.LineSettings(direction=Direction.INPUT, edge_detection=Edge.BOTH, bias=Bias.PULL_UP)})
        self.falling = gpiod.EdgeEvent.Type.FALLING_EDGE

    def wait(self, timeout: float) -> bool:
        return self.request.wait_edge_events(timeout)

    def edges(self) -> list[tuple[bool, float]]:
        # The kernel stamps edge events with CLOCK_MONOTONIC by default, the clock of time.monotonic().
        return [(event.event_type == self.falling, event.timestamp_ns / 1e9) for event in self.request.read_edge_events()]

    def pressed(self) -> bool:
        return self.request.get_value(self.line) == self.low

    def close(self) -> None:
        self.request.release()


class Button:
    """Watches the line in a thread and calls `on_press` for each press while `enabled` is set.

    While `holding` is also set, presses count on release and a long hold calls `on_hold` instead.
    """

    def __init__(self, line: Line, on_press: Callable[[], None], clock: Callable[[], float] = time.monotonic,
                 on_hold: Callable[[], None] | None = None) -> None:
        self.line, self.on_press, self.clock, self.on_hold = line, on_press, clock, on_hold
        self.filter = PressFilter()
        self.enabled = threading.Event()
        self.holding = threading.Event()
        self.stopping = threading.Event()
        self.down_at: float | None = None  # a press waiting for its release, in holding mode
        self.held = False
        self.thread = threading.Thread(target=self._run, name="photobox-button", daemon=True)

    def start(self) -> Button:
        self.thread.start()
        return self

    def stop(self) -> None:
        self.stopping.set()
        self.thread.join(timeout=2)
        self.line.close()

    @property
    def stuck(self) -> bool:
        return self.filter.stuck(self.clock())

    def _pressed(self, at: float) -> None:
        if not self.enabled.is_set():
            return
        if self.holding.is_set() and self.on_hold is not None:
            self.down_at, self.held = at, False  # wait: a press or a hold
        else:
            self.on_press()

    def _check_hold(self, now: float) -> None:
        if self.down_at is None:
            return
        if not (self.enabled.is_set() and self.holding.is_set()):
            self.down_at = None  # the screen changed while the button was down: neither a press nor a hold
        elif not self.filter.down:
            if not self.held:
                self.on_press()  # let go before the hold: an ordinary press
            self.down_at = None
        elif not self.held and now - self.down_at >= HOLD_SECONDS:
            self.held = True
            self.on_hold()

    def _run(self) -> None:
        while not self.stopping.is_set():
            try:
                if self.line.wait(DEBOUNCE_SECONDS):
                    for pressed, at in self.line.edges():
                        if self.filter.edge(pressed, at):
                            self._pressed(at)
                else:
                    self.filter.settle(self.line.pressed(), self.clock())
                self._check_hold(self.clock())
            except OSError:
                time.sleep(1)  # a failing line only means no button; the keyboard still works


def open_button(gpio: str, on_press: Callable[[], None], on_hold: Callable[[], None] | None = None) -> Button | None:
    """The button on this GPIO number, or None if there is none or it cannot be used."""
    if not gpio.strip():
        return None
    try:
        return Button(GpiodLine(int(gpio)), on_press, on_hold=on_hold).start()
    except (ImportError, OSError, ValueError):
        return None
