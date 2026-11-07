"""Session state machine. docs/ARCHITECTURE.md describes the same table in words."""
from __future__ import annotations
import sqlite3

AWAITING_CONSENT = "awaiting_consent"
AWAITING_PORTRAIT = "awaiting_portrait"
UPLOADED = "uploaded"
PROCESSING = "processing"
COMPLETE = "complete"
FAILED = "failed"

TRANSITIONS: frozenset[tuple[str, str]] = frozenset({
    (AWAITING_CONSENT, AWAITING_PORTRAIT),
    (AWAITING_PORTRAIT, UPLOADED),
    (UPLOADED, PROCESSING),
    (PROCESSING, COMPLETE),
    (PROCESSING, FAILED),
    (FAILED, PROCESSING),
})


def transition(db: sqlite3.Connection, session_id: str, source: str, target: str) -> bool:
    """Atomically move a session between states.

    Returns False if the session is no longer in `source`. Of several concurrent requests, exactly
    one gets True, because SQLite applies the conditional UPDATE under its write lock. This also
    takes that lock for the rest of the transaction.
    """
    if (source, target) not in TRANSITIONS:
        raise ValueError(f"transition {source} -> {target} is not allowed")
    cursor = db.execute("UPDATE sessions SET status=? WHERE id=? AND status=?", (target, session_id, source))
    return cursor.rowcount == 1


def recover_interrupted(db: sqlite3.Connection) -> int:
    """At startup nothing can still be running, so a session left in `processing` was interrupted.

    It can be processed again. This assumes one server process, which the docs require.
    """
    cursor = db.execute("UPDATE sessions SET status=?, error='interrupted by restart' WHERE status=?", (FAILED, PROCESSING))
    return cursor.rowcount
