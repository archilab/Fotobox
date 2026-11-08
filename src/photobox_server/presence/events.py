"""Typed presence events (ADR-023).

Events are written into the caller's transaction, like the audit trail, so an event exists exactly
when the action it describes committed. They hold opaque ids, a type and times: never image data,
names or anything else about a person. They are deleted after RETENTION_SECONDS, so they never
become a history of visits.

Device connection is not an event: a box's online state comes from its latest status report
(`device_status`), and the projection derives "connected" and "disconnected" from its age.
"""
from __future__ import annotations
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, get_args

EventType = Literal[
    "session.started",       # consent was recorded: a visitor explicitly began
    "session.ended",         # cancelled or withdrawn
    "portrait.captured",     # an authorized photo was uploaded (never the image itself)
    "generation.started",
    "generation.completed",
    "generation.failed",
    # Reserved for live matching (milestone L-1). Nothing emits them yet; the projection already
    # accepts them so the simulator (S3) and L-1 share one contract.
    "match.live_created",
    "match.pool_created",
]
EVENT_TYPES: frozenset[str] = frozenset(get_args(EventType))
EMITTED_TODAY: frozenset[str] = EVENT_TYPES - {"match.live_created", "match.pool_created"}
# Longer than the longest a session can stay live (projection.MAX_SESSION_SECONDS).
RETENTION_SECONDS = 3600


@dataclass(frozen=True)
class PresenceEvent:
    event_id: str
    type: str
    box_id: str
    session_id: str | None
    correlation_id: str
    occurred_at: datetime
    simulated: bool = False


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def emit(
    db: sqlite3.Connection,
    event_type: str,
    box_id: str,
    *,
    session_id: str | None = None,
    event_id: str | None = None,
    occurred_at: datetime | None = None,
    simulated: bool = False,
) -> bool:
    """Record an event. A repeated event id is ignored, so redelivery is harmless.

    Returns False for a duplicate. Times are the server's own, never a client's.
    """
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown presence event type {event_type!r}")
    now = datetime.now(timezone.utc)
    moment = min(occurred_at or now, now)  # an event from the future cannot outlast its TTL
    event_id = event_id or str(uuid.uuid4())
    cursor = db.execute(
        "INSERT OR IGNORE INTO presence_events(event_id,type,box_id,session_id,correlation_id,occurred_at,simulated) VALUES(?,?,?,?,?,?,?)",
        (event_id, event_type, box_id, session_id, session_id or event_id, _iso(moment), int(simulated)),
    )
    purge(db, now)
    return cursor.rowcount == 1


def purge(db: sqlite3.Connection, now: datetime | None = None) -> int:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(seconds=RETENTION_SECONDS)
    return db.execute("DELETE FROM presence_events WHERE occurred_at < ?", (_iso(cutoff),)).rowcount


def load(db: sqlite3.Connection, *, include_simulated: bool = False) -> list[PresenceEvent]:
    query = "SELECT * FROM presence_events" + ("" if include_simulated else " WHERE simulated = 0") + " ORDER BY occurred_at, event_id"
    return [
        PresenceEvent(row["event_id"], row["type"], row["box_id"], row["session_id"], row["correlation_id"],
                      datetime.fromisoformat(row["occurred_at"]), bool(row["simulated"]))
        for row in db.execute(query)
    ]
