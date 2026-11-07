"""Audit trail of consent and image use. Opaque ids only: never image data, names or codes."""
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone


def record(
    db: sqlite3.Connection,
    event: str,
    *,
    session_id: str | None = None,
    portrait_id: str | None = None,
    device_id: str | None = None,
    detail: str | None = None,
) -> None:
    """Add an event to the caller's transaction, so it commits or rolls back with the action."""
    db.execute(
        "INSERT INTO audit_events(occurred_at,event,session_id,portrait_id,device_id,detail) VALUES(?,?,?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(), event, session_id, portrait_id, device_id, detail),
    )
