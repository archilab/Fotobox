"""A simulated encounter between two simulated boxes (ADR-025, milestone M-VOICE-0). Developer mode only.

It drives the real message relay and shows each box's screen as spec section 15.5 describes it.
Everything is held in this process's memory and is gone when the dashboard restarts.
"""
from __future__ import annotations
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from ..messaging import MessagingError, Participant, Relay

POOL_TEXT = "Your photo is being created with a portrait from earlier. No one is online in this encounter."
REJECTIONS = {
    "not_in_match": "Not part of this encounter. Nothing was sent.",
    "window_closed": "Messaging has ended. Message not sent.",
    "too_long": "Too long. Please say something shorter.",
    "empty": "I couldn't understand that. Try again.",
    "limit_reached": "No more messages in this encounter.",
    "cooldown": "One moment before the next message.",
    "invalid": "Message not sent.",
    "blocked": "Message not sent.",
}
CLOSED_TEXT = {
    "generation_completed": "Your photo is ready. Messaging has ended.",
    "generation_failed": "The photo could not be made. Messaging has ended.",
    "timeout": "Message time is over. Your shared photo is still being made.",
    "participant_left": "The other visitor has left. Messaging has ended.",
    "cancelled": "Messaging has ended.",
}


@dataclass
class Screen:
    """One box's view, built only from the events delivered to that box."""
    partner: str | None = None
    open: bool = False
    expires_at: str | None = None
    closed: str | None = None
    status: str | None = None
    received: deque = field(default_factory=lambda: deque(maxlen=5))

    def apply(self, event: dict) -> None:
        kind = event["type"]
        if kind == "messaging.window_opened":
            self.partner, self.open, self.expires_at, self.closed = (event["partners"] or [None])[0], True, event["expires_at"], None
        elif kind == "messaging.window_closed":
            self.open, self.closed, self.status = False, CLOSED_TEXT.get(event["reason"], "Messaging has ended."), None
            self.received.clear()
        elif kind == "messaging.message_received":
            self.received.append(event)
        elif kind == "messaging.message_ack":
            self.status = f"SENT TO {self.partner}"
        elif kind == "messaging.message_rejected":
            self.status = REJECTIONS.get(event["reason"], "Message not sent.")

    def view(self, now: str) -> dict:
        shown = [{"from": m["sender_label"], "text": m["text"]} for m in self.received if m["expires_at"] > now]
        return {"partner": self.partner, "open": self.open, "closed": self.closed, "status": self.status, "received": shown,
                "seconds_left": max(0, int((datetime.fromisoformat(self.expires_at) - datetime.fromisoformat(now)).total_seconds())) if self.open and self.expires_at else None}


class Encounter:
    def __init__(self, relay: Relay | None = None) -> None:
        self.relay = relay
        self.match_id: str | None = None
        self.kind: str | None = None
        self.members: list[str] = []
        self.screens: dict[str, Screen] = {}

    def _relay(self) -> Relay:
        self.relay = self.relay or Relay()
        return self.relay

    def _sync(self, boxes: list[str]) -> None:
        relay = self._relay()
        for box in boxes:
            screen = self.screens.setdefault(box, Screen())
            for event in relay.drain(box):
                screen.apply(event)

    def _end_current(self) -> None:
        if self.match_id is not None:
            self._relay().close(self.match_id, "cancelled")
        self.screens, self.match_id, self.kind, self.members = {}, None, None, []
        self.relay = None

    def start_live(self, first, second) -> None:
        """`first` and `second` are simulated boxes with a running session."""
        for box in (first, second):
            if not box["connected"] or not box["session_id"]:
                raise MessagingError("both boxes need to be connected with a session running: press start on each")
        if first["box_id"] == second["box_id"]:
            raise MessagingError("a message window needs visitors at two different boxes")
        self._end_current()
        self.match_id, self.kind, self.members = f"sim-{uuid.uuid4()}", "live", [first["box_id"], second["box_id"]]
        self._relay().open_window(self.match_id, "live", [Participant(b["session_id"], b["box_id"], b["label"]) for b in (first, second)])

    def start_pool(self, box) -> None:
        """A pool match has nobody present, so no window can open."""
        self._end_current()
        self.kind, self.members = "pool", [box["box_id"]]

    def send(self, box, text: str) -> None:
        self._sync([box["box_id"]])
        screen = self.screens.setdefault(box["box_id"], Screen())
        match = self.match_id or "none"
        event = self._relay().send(box["box_id"], box["session_id"] or "none", match, str(uuid.uuid4()), text)
        screen.apply(event)

    def close(self, reason: str) -> None:
        if self.match_id is None or not self._relay().close(self.match_id, reason):
            raise MessagingError("no message window is open")

    def view(self, boxes: list[dict]) -> dict:
        ids = [box["box_id"] for box in boxes]
        self._sync(ids)
        now = datetime.now(timezone.utc).isoformat()
        cards = []
        for box in boxes:
            view = self.screens.setdefault(box["box_id"], Screen()).view(now)
            member = box["box_id"] in self.members
            cards.append(view | {"box_id": box["box_id"], "label": box["label"], "member": member,
                                 "pool_text": POOL_TEXT if self.kind == "pool" and member else None})
        return {"kind": self.kind, "cards": cards}
