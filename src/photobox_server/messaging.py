"""Ephemeral Message Relay (spec section 15, ADR-025, milestone M-VOICE-0).

Short text messages between the visitors of one live pair, only while their strip is being made.
This is the contract and its rules; `message_windows.py` connects it to real live pairs (ADR-027),
and the dashboard's developer-mode simulator uses its own instance.

Everything is held in memory and bounded. Text is never written to the database or a log, and an
unread message disappears when it expires or its window closes, so nothing is replayed later.
The server decides who may send and who receives: a caller names a match, never a destination.
What may be said is decided by the caller's `blocked` check (`message_filter.py`).
"""
from __future__ import annotations
import re
import threading
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from .config import int_setting
from .presence.messages import clean

MESSAGE_ID = re.compile(r"[A-Za-z0-9-]{1,64}")
INBOX_LIMIT = 20
RAW_TEXT_LIMIT = 2000  # checked before any work is done on the text
CLOSED_WINDOW_SECONDS = 60  # a closed window is remembered this long, to answer late messages
UNNAMED = "ANOTHER PHOTOBOX"
CLOSE_REASONS = frozenset({"generation_completed", "generation_failed", "timeout", "participant_left", "cancelled"})


class MessagingError(ValueError):
    """The request cannot be carried out; the message says why."""


@dataclass(frozen=True)
class Limits:
    window_seconds: int = 45
    max_chars: int = 160
    max_per_sender: int = 3
    cooldown_seconds: int = 3
    display_seconds: int = 8

    @classmethod
    def from_env(cls) -> "Limits":
        return cls(
            int_setting("PHOTOBOX_MESSAGE_WINDOW_SECONDS", 45, 10, 120),
            int_setting("PHOTOBOX_MESSAGE_MAX_CHARS", 160, 20, 500),
            int_setting("PHOTOBOX_MESSAGE_MAX_PER_SENDER", 3, 1, 10),
            int_setting("PHOTOBOX_MESSAGE_COOLDOWN_SECONDS", 3, 0, 30),
            int_setting("PHOTOBOX_MESSAGE_DISPLAY_SECONDS", 8, 3, 30),
        )


@dataclass(frozen=True)
class Participant:
    session_id: str
    box_id: str
    label: str | None

    @property
    def sender_label(self) -> str:
        return clean(self.label).upper() if self.label else UNNAMED


@dataclass
class _Window:
    match_id: str
    participants: dict[str, Participant]
    deadline: datetime
    closed_at: datetime | None = None
    sent: dict[str, int] = field(default_factory=dict)
    last_sent: dict[str, datetime] = field(default_factory=dict)
    # Acknowledgements of accepted messages, without their text, to answer a repeated send.
    accepted: dict[tuple[str, str], dict] = field(default_factory=dict)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _rejected(message_id: str, reason: str) -> dict:
    return {"type": "messaging.message_rejected", "message_id": message_id, "reason": reason}


class Relay:
    """Thread-safe and bounded. `now` can be replaced so tests need not wait."""

    def __init__(self, limits: Limits | None = None, now: Callable[[], datetime] | None = None) -> None:
        self.limits = limits or Limits.from_env()
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._windows: dict[str, _Window] = {}
        self._inboxes: dict[str, deque[dict]] = {}

    def _inbox(self, box_id: str) -> deque[dict]:
        return self._inboxes.setdefault(box_id, deque(maxlen=INBOX_LIMIT))

    def _close(self, window: _Window, reason: str) -> None:
        window.closed_at = self.now()
        for participant in window.participants.values():
            inbox = self._inbox(participant.box_id)
            kept = [e for e in inbox if not (e["type"] == "messaging.message_received" and e["match_id"] == window.match_id)]
            inbox.clear()
            inbox.extend(kept)
            inbox.append({"type": "messaging.window_closed", "match_id": window.match_id, "reason": reason})

    def _expire(self) -> None:
        now = self.now()
        for match_id, window in list(self._windows.items()):
            if window.closed_at is None and now >= window.deadline:
                self._close(window, "timeout")
            if window.closed_at is not None and (now - window.closed_at).total_seconds() > CLOSED_WINDOW_SECONDS:
                del self._windows[match_id]
        stamp = _iso(now)
        for inbox in self._inboxes.values():
            kept = [e for e in inbox if e["type"] != "messaging.message_received" or e["expires_at"] > stamp]
            inbox.clear()
            inbox.extend(kept)

    def open_window(self, match_id: str, kind: str, participants: Iterable[Participant]) -> dict:
        """Open a pair's window. Only a live match has anyone present to talk to."""
        if kind != "live":
            raise MessagingError("only a live match can open a message window")
        people = {p.session_id: p for p in participants}
        if len(people) < 2 or len({p.box_id for p in people.values()}) != len(people):
            raise MessagingError("a message window needs visitors at two different boxes")
        with self._lock:
            self._expire()
            if match_id in self._windows:
                raise MessagingError("this match already has a window")
            deadline = self.now() + timedelta(seconds=self.limits.window_seconds)
            self._windows[match_id] = _Window(match_id, people, deadline)
            for person in people.values():
                self._inbox(person.box_id).append({
                    "type": "messaging.window_opened", "match_id": match_id, "expires_at": _iso(deadline),
                    "partners": [p.sender_label for p in people.values() if p.box_id != person.box_id],
                })
            return {"match_id": match_id, "expires_at": _iso(deadline)}

    def close(self, match_id: str, reason: str) -> bool:
        if reason not in CLOSE_REASONS:
            raise MessagingError(f"unknown close reason {reason!r}")
        with self._lock:
            self._expire()
            window = self._windows.get(match_id)
            if window is None or window.closed_at is not None:
                return False
            self._close(window, reason)
            return True

    def leave(self, box_id: str) -> None:
        """A box disconnected: its windows close and nothing waits for a visitor who may have gone."""
        with self._lock:
            self._expire()
            for window in self._windows.values():
                if window.closed_at is None and any(p.box_id == box_id for p in window.participants.values()):
                    self._close(window, "participant_left")

    def open_matches(self, box_id: str) -> list[tuple[str, list[Participant]]]:
        """The windows this box is part of that are still open."""
        with self._lock:
            self._expire()
            return [(w.match_id, list(w.participants.values())) for w in self._windows.values()
                    if w.closed_at is None and any(p.box_id == box_id for p in w.participants.values())]

    def send(self, box_id: str, session_id: str, match_id: str, message_id: str, text: str,
             blocked: Callable[[str], bool] | None = None) -> dict:
        """Accept or reject one message. `box_id` is the authenticated box, never a claim by the caller.

        An unknown match and someone else's match get the same answer, so ids cannot be probed.
        Text that is too long is rejected, never cut, which could change its meaning.
        """
        message_id = str(message_id)[:64]
        with self._lock:
            self._expire()
            window = self._windows.get(match_id)
            sender = window.participants.get(session_id) if window else None
            if window is None or sender is None or sender.box_id != box_id:
                return _rejected(message_id, "not_in_match")
            if not MESSAGE_ID.fullmatch(message_id):
                return _rejected(message_id, "invalid")
            key = (session_id, message_id)
            if key in window.accepted:
                return window.accepted[key]
            if window.closed_at is not None:
                return _rejected(message_id, "window_closed")
            if len(text) > RAW_TEXT_LIMIT:
                return _rejected(message_id, "too_long")
            cleaned = clean(text, limit=RAW_TEXT_LIMIT)
            if not cleaned:
                return _rejected(message_id, "empty")
            if len(cleaned) > self.limits.max_chars:
                return _rejected(message_id, "too_long")
            if blocked is not None and blocked(cleaned):
                return _rejected(message_id, "blocked")
            if window.sent.get(session_id, 0) >= self.limits.max_per_sender:
                return _rejected(message_id, "limit_reached")
            now = self.now()
            last = window.last_sent.get(session_id)
            if last is not None and (now - last).total_seconds() < self.limits.cooldown_seconds:
                return _rejected(message_id, "cooldown")
            window.sent[session_id] = window.sent.get(session_id, 0) + 1
            window.last_sent[session_id] = now
            expires = _iso(now + timedelta(seconds=self.limits.display_seconds))
            for person in window.participants.values():
                if person.session_id != session_id:
                    self._inbox(person.box_id).append({
                        "type": "messaging.message_received", "message_id": message_id, "match_id": match_id,
                        "sender_label": sender.sender_label, "text": cleaned, "expires_at": expires,
                    })
            # Accepted by the server. That is not the same as displayed or read.
            ack = {"type": "messaging.message_ack", "message_id": message_id, "status": "accepted"}
            window.accepted[key] = ack
            return ack

    def drain(self, box_id: str) -> list[dict]:
        """Hand over a box's waiting events, once."""
        with self._lock:
            self._expire()
            inbox = self._inbox(box_id)
            events = list(inbox)
            inbox.clear()
            return events
