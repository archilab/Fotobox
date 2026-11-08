"""Private withdrawal of consent. Local admin only: there is deliberately no HTTP endpoint."""
from __future__ import annotations
import hashlib
import secrets
import sqlite3
from typing import NamedTuple
from . import audit
from .presence import events as presence_events
from .core import delete_portrait, now_iso, purge_stale_strips
from .jobs import cancel_for_session, discard_frames_using
from .sessions import WITHDRAWN


class WithdrawalResult(NamedTuple):
    session_id: str
    already_withdrawn: bool
    portraits: int
    strips: int


def new_code() -> tuple[str, str]:
    """Return a code for the participant and its hash. Only the hash is stored."""
    code = secrets.token_urlsafe(16)
    return code, hash_code(code)


def hash_code(code: str) -> str:
    # 128-bit random values, so a fast hash is sufficient (unlike passwords).
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def session_for_code(db: sqlite3.Connection, code: str) -> str | None:
    row = db.execute("SELECT id FROM sessions WHERE withdrawal_hash = ?", (hash_code(code),)).fetchone()
    return row["id"] if row else None


def withdraw_session(db: sqlite3.Connection, session_id: str) -> WithdrawalResult | None:
    """Withdraw all consent for a session and delete its images and every strip containing them.

    The consent records stay, marked withdrawn, as the record of what happened. Safe to repeat.
    Generation in progress stops, and cannot complete afterwards: its state change to complete fails.
    """
    row = db.execute("SELECT status, box_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        return None
    already = row["status"] == WITHDRAWN
    db.execute("UPDATE sessions SET status = ? WHERE id = ?", (WITHDRAWN, session_id))
    db.execute("UPDATE consent_records SET withdrawn_at = ? WHERE session_id = ? AND withdrawn_at IS NULL", (now_iso(), session_id))
    cancel_for_session(db, session_id, "consent withdrawn")
    portraits = db.execute("SELECT id, path FROM portraits WHERE session_id = ?", (session_id,)).fetchall()
    discard_frames_using(db, [portrait["id"] for portrait in portraits])
    for portrait in portraits:
        delete_portrait(db, portrait, "withdrawn", session_id)
    strips = purge_stale_strips(db)
    if not already:
        audit.record(db, "consent_withdrawn", session_id=session_id)
        presence_events.emit(db, "session.ended", row["box_id"], session_id=session_id)
    return WithdrawalResult(session_id, already, len(portraits), strips)


def remove_from_pool(db: sqlite3.Connection, session_id: str) -> int | None:
    """Withdraw only a session's pool consent: delete its pool copy and every strip that used it.

    The visitor's own strip stays. Returns the number of strips deleted, or None if the session
    has nothing in the pool.
    """
    portraits = db.execute("SELECT id, path FROM portraits WHERE session_id = ? AND pool_eligible = 1", (session_id,)).fetchall()
    consent = db.execute("SELECT 1 FROM consent_records WHERE session_id = ? AND purpose = 'pool' AND withdrawn_at IS NULL", (session_id,)).fetchone()
    if not portraits and consent is None:
        return None
    db.execute("UPDATE consent_records SET withdrawn_at = ? WHERE session_id = ? AND purpose = 'pool' AND withdrawn_at IS NULL", (now_iso(), session_id))
    discard_frames_using(db, [portrait["id"] for portrait in portraits])
    for portrait in portraits:
        delete_portrait(db, portrait, "removed from pool", session_id)
    strips = purge_stale_strips(db)
    audit.record(db, "pool_consent_withdrawn", session_id=session_id)
    return strips
