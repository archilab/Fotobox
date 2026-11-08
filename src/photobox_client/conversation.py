"""Short spoken messages between a live pair while their strip is made (spec section 15, ADR-027).

Only the server opens a window, and only for a live pair, so nothing here can record or send on a
pool match, a seed, or any other screen. ENTER counts only while a window is open. A failure of the
microphone, the transcriber or the network only means no message; the strip carries on regardless.
"""
from __future__ import annotations
import uuid
from collections import deque
from collections.abc import Callable
import httpx

ENTER = "enter"
POLL_SECONDS = 1.0
DISPLAY_SECONDS = 8
LOST = "Connection lost. Message not sent."
NOT_SENT = {
    "too_long": "Too long. Please say something shorter.",
    "limit_reached": "No more messages in this encounter.",
    "cooldown": "One moment before the next message.",
    "window_closed": "Messaging has ended. Message not sent.",
}
CLOSED = {
    "generation_completed": "Your photo is ready. Messaging has ended.",
    "generation_failed": "Messaging has ended.",
    "timeout": "Message time is over. Your photo is still being made.",
    "participant_left": "The other visitor has left. Messaging has ended.",
    "cancelled": "Messaging has ended.",
}


class Conversation:
    def __init__(self, api, session_id: str, keys, make_talker: Callable[[Callable[[], bool]], object] | None,
                 clock: Callable[[], float], limit_seconds: int = 8) -> None:
        self.api, self.session_id, self.keys, self.make_talker, self.clock = api, session_id, keys, make_talker, clock
        self.limit_seconds = limit_seconds
        self.enabled = True
        self.open = False
        self.match_id: str | None = None
        self.partner: str | None = None
        self.closed: str | None = None
        self.status: str | None = None
        self.waiting: deque[dict] = deque(maxlen=5)
        self.current: tuple[dict, float] | None = None
        self.next_poll = 0.0

    def apply(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "messaging.window_opened":
            self.open, self.match_id, self.closed = True, event["match_id"], None
            self.partner = (event.get("partners") or ["ANOTHER PHOTOBOX"])[0]
            self.keys.flush()  # a key pressed before the invitation appeared is not a request to talk
        elif kind == "messaging.window_closed" and event.get("match_id") == self.match_id:
            self.open, self.status, self.current = False, None, None
            self.closed = CLOSED.get(event.get("reason"), "Messaging has ended.")
            self.waiting.clear()
        elif kind == "messaging.message_received" and event.get("match_id") == self.match_id:
            self.waiting.append(event)
        elif kind == "messaging.message_ack":
            self.status = f"SENT TO {self.partner}"
        elif kind == "messaging.message_rejected":
            self.status = NOT_SENT.get(event.get("reason"), "Message not sent.")

    def poll(self, force: bool = False) -> None:
        if not self.enabled or (not force and self.clock() < self.next_poll):
            return
        self.next_poll = self.clock() + POLL_SECONDS
        try:
            events = self.api.messaging_events()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                self.enabled = False  # messaging is not switched on
            elif self.open:
                self._lost()
            return
        except httpx.HTTPError:
            if self.open:
                self._lost()
            return
        for event in events:
            self.apply(event)

    def _lost(self) -> None:
        self.open, self.status, self.current, self.closed = False, None, None, LOST
        self.waiting.clear()

    def _window_open(self) -> bool:
        self.poll()
        return self.open

    def text(self) -> str:
        """The lines for the generating screen. Empty unless the server opened or closed a window."""
        now = self.clock()
        if self.current is not None and now >= self.current[1]:
            self.current = None
        if self.current is None and self.waiting:
            self.current = (self.waiting.popleft(), now + DISPLAY_SECONDS)
        if self.closed:
            return self.closed
        if not self.open:
            return ""
        lines = [f"CONNECTED TO {self.partner} · LIVE"]
        if self.make_talker is not None:
            lines.append(f"PRESS ENTER TO TALK · up to {self.limit_seconds} seconds")
        if self.current is not None:
            lines.append(f"\n{self.current[0]['sender_label']}: {self.current[0]['text']}")
        if self.status:
            lines.append(f"\n{self.status}")
        return "\n".join(lines)

    def wait(self, seconds: float) -> None:
        """Pass the time between screen updates, acting on ENTER only while a window is open."""
        key = self.keys.read_key(seconds)
        if key == ENTER and self.open and self.make_talker is not None:
            self.speak()

    def speak(self) -> None:
        text = self.make_talker(self._window_open).talk()
        self.keys.flush()
        if text is None or not self.open:
            return
        try:
            self.apply(self.api.send_message(self.session_id, self.match_id, str(uuid.uuid4()), text))
        except httpx.HTTPError:
            self.status = LOST


def speech_talker(device: str, keys, screen, language: str = "auto") -> Callable[[Callable[[], bool]], object] | None:
    """A way to record on this box, or None if it has no microphone or speech recognition."""
    from .speech import DEFAULT_COMMAND, DEFAULT_MODEL, Microphone, PushToTalk, WhisperCppTranscriber
    if not device or not DEFAULT_COMMAND.is_file() or not DEFAULT_MODEL.is_file():
        return None
    microphone, transcriber = Microphone(device), WhisperCppTranscriber()

    def make(window_open: Callable[[], bool]) -> PushToTalk:
        return PushToTalk(microphone, transcriber, keys, screen, window_open, language=language, enter=ENTER)

    return make
