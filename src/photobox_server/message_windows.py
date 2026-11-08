"""Connects the message relay to real live pairs (ADR-027, milestone M-VOICE-2).

The relay lives in the booth server's memory. A window opens when a live pair's strip starts, for the
two sessions the database says are paired, and closes with the job. Withdrawal can come from another
process, so the database is checked again on every send and every poll: the relay alone is never trusted
to know that a pair still exists.
"""
from __future__ import annotations
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from .core import db_connect
from .message_filter import is_blocked, load_words
from .messaging import MessagingError, Participant, Relay
from .presence.projection import OFFLINE_SECONDS

PREFIX = "live-"
ACTIVE = ("queued", "processing")
STATS_DAYS = 30
_relay: Relay | None = None


def enabled() -> bool:
    return os.getenv("PHOTOBOX_MESSAGING", "").strip().lower() in {"1", "true", "yes"}


def relay() -> Relay:
    global _relay
    if _relay is None:
        _relay = Relay()
    return _relay


def reset() -> None:
    global _relay
    _relay = None


def match_id(leader_id: str) -> str:
    return PREFIX + leader_id


def _pair(db: sqlite3.Connection, leader_id: str) -> tuple[sqlite3.Row, sqlite3.Row] | None:
    """The leader and follower, if the database still says they are paired and their strip is unfinished."""
    lead = db.execute("SELECT * FROM sessions WHERE id=? AND live_role='lead'", (leader_id,)).fetchone()
    if lead is None or lead["status"] not in ACTIVE:
        return None
    follow = db.execute("SELECT * FROM sessions WHERE id=? AND live_role='follow' AND live_partner=?", (lead["live_partner"], leader_id)).fetchone()
    return None if follow is None or follow["status"] not in ACTIVE else (lead, follow)


def _label(db: sqlite3.Connection, box_id: str) -> str | None:
    row = db.execute("SELECT public_label FROM devices WHERE id=?", (box_id,)).fetchone()
    return row["public_label"] if row else None


def count(db: sqlite3.Connection, *, windows: int = 0, accepted: int = 0, blocked: int = 0) -> None:
    """Add to today's totals, and forget totals older than STATS_DAYS. Counts only."""
    now = datetime.now(timezone.utc)
    db.execute(
        """INSERT INTO message_stats(day,windows,accepted,blocked) VALUES(?,?,?,?)
           ON CONFLICT(day) DO UPDATE SET windows=windows+excluded.windows, accepted=accepted+excluded.accepted, blocked=blocked+excluded.blocked""",
        (now.date().isoformat(), windows, accepted, blocked),
    )
    db.execute("DELETE FROM message_stats WHERE day < ?", ((now - timedelta(days=STATS_DAYS)).date().isoformat(),))


def open_for(leader_id: str) -> None:
    """Called when the strip starts. Only a live leader has a window; a pool match or seed never does."""
    if not enabled():
        return
    with db_connect() as db:
        pair = _pair(db, leader_id)
        if pair is None:
            return
        people = [Participant(s["id"], s["box_id"], _label(db, s["box_id"])) for s in pair]
    try:
        relay().open_window(match_id(leader_id), "live", people)
    except MessagingError:
        return  # a retry of the same strip keeps its original window and deadline
    with db_connect() as db:
        count(db, windows=1)


def close_for(leader_id: str, reason: str) -> None:
    if enabled():
        relay().close(match_id(leader_id), reason)


def _box_online(db: sqlite3.Connection, box_id: str) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=OFFLINE_SECONDS)).isoformat()
    return db.execute("SELECT 1 FROM device_status WHERE device_id=? AND reported_at>?", (box_id, cutoff)).fetchone() is not None


def sync(db: sqlite3.Connection, box_id: str) -> None:
    """Close this box's windows whose pair has ended or whose other box has gone silent."""
    for match, participants in relay().open_matches(box_id):
        pair = _pair(db, match[len(PREFIX):])
        if pair is None:
            relay().close(match, "participant_left")
        elif not all(_box_online(db, person.box_id) for person in participants):
            relay().close(match, "participant_left")


def send(db: sqlite3.Connection, session: sqlite3.Row, match: str, message_id: str, text: str) -> dict:
    """Relay one message, after checking the pair is still real. Blocked text gets a neutral refusal."""
    sync(db, session["box_id"])
    words = load_words(db)
    event = relay().send(session["box_id"], session["id"], match, message_id, text, blocked=lambda cleaned: is_blocked(cleaned, words))
    if event["type"] == "messaging.message_ack":
        count(db, accepted=1)
    elif event.get("reason") == "blocked":
        count(db, blocked=1)
    return event
