"""Live matching across boxes (ADR-024, milestone L-1).

When a visitor's photo is to be processed, the server first looks for a visitor at another box who
uploaded within the last few seconds and is still waiting. If there is one, the two are paired:
the one who waited first leads, their job runs at once with the newcomer's photo as counterpart,
and the newcomer follows that job and receives the identical strip. If there is none, and another
box is online, the job waits up to PHOTOBOX_LIVE_WAIT_SECONDS for someone to arrive; after that it
runs with a pool portrait or a seed, as before.

Both visitors need an active `live` consent, given with the consent notice when the operator has
switched live matching on (PHOTOBOX_LIVE_MATCHING=true) and the notice says so. A box is never
paired with itself. Pairing happens under the database's write lock, so a visitor is paired once.
"""
from __future__ import annotations
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from . import audit
from .config import int_setting
from .core import now_iso
from .presence import events as presence_events
from .presence.projection import OFFLINE_SECONDS

LEAD, FOLLOW = "lead", "follow"


def enabled() -> bool:
    return os.getenv("PHOTOBOX_LIVE_MATCHING", "").strip().lower() in {"1", "true", "yes"}


def wait_seconds() -> int:
    return int_setting("PHOTOBOX_LIVE_WAIT_SECONDS", 20, 5, 60)


def max_wait_seconds() -> int:
    """The longest a job waits while a consented visitor at another box is on the way."""
    return max(wait_seconds(), int_setting("PHOTOBOX_LIVE_MAX_WAIT_SECONDS", 60, 20, 120))


def consented(db: sqlite3.Connection, session_id: str) -> bool:
    return db.execute(
        "SELECT 1 FROM consent_records WHERE session_id=? AND purpose='live' AND withdrawn_at IS NULL", (session_id,)
    ).fetchone() is not None


def other_box_online(db: sqlite3.Connection, box_id: str) -> bool:
    """Waiting only makes sense if another real box could send someone."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=OFFLINE_SECONDS)).isoformat()
    return db.execute(
        """SELECT 1 FROM device_status s JOIN devices d ON d.id = s.device_id
           WHERE s.device_id != ? AND d.revoked_at IS NULL AND s.reported_at > ? LIMIT 1""",
        (box_id, cutoff),
    ).fetchone() is not None


def coming(db: sqlite3.Connection, box_id: str) -> sqlite3.Row | None:
    """A visitor at another box who has agreed to live pairing and whose photo has not been processed yet."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max_wait_seconds())).isoformat()
    return db.execute(
        """SELECT s.box_id, d.public_label FROM sessions s JOIN devices d ON d.id = s.box_id
           JOIN consent_records c ON c.session_id = s.id AND c.purpose = 'live' AND c.withdrawn_at IS NULL
           WHERE s.box_id != ? AND s.status IN ('awaiting_portrait', 'uploaded') AND s.live_partner IS NULL AND s.created_at > ?
           ORDER BY s.created_at DESC LIMIT 1""",
        (box_id, cutoff),
    ).fetchone()


def wait_for(db: sqlite3.Connection, session: sqlite3.Row) -> int:
    """How long this session's job should wait for a live partner: 0 means start at once.

    Longer only for a real visitor who has already agreed at another box, never on a guess.
    """
    if not enabled() or not consented(db, session["id"]):
        return 0
    if coming(db, session["box_id"]) is not None:
        return max_wait_seconds()
    return wait_seconds() if other_box_online(db, session["box_id"]) else 0


def extend_waiting(db: sqlite3.Connection, box_id: str) -> None:
    """A visitor at `box_id` just agreed: jobs waiting at other boxes keep waiting for them, up to the maximum."""
    if not enabled():
        return
    rows = db.execute(
        """SELECT j.id, j.created_at, j.next_attempt_at FROM generation_jobs j JOIN sessions s ON s.id = j.session_id
           WHERE j.status = 'queued' AND j.live_wait = 1 AND j.attempts = 0 AND j.next_attempt_at > ?
             AND s.box_id != ? AND s.live_partner IS NULL""",
        (now_iso(), box_id),
    ).fetchall()
    for row in rows:
        latest = (datetime.fromisoformat(row["created_at"]) + timedelta(seconds=max_wait_seconds())).isoformat()
        if latest > row["next_attempt_at"]:
            db.execute("UPDATE generation_jobs SET next_attempt_at=? WHERE id=?", (latest, row["id"]))


def waiting_boxes(db: sqlite3.Connection) -> set[str]:
    """Boxes whose visitor is waiting for a live partner right now."""
    if not enabled():
        return set()
    rows = db.execute(
        """SELECT DISTINCT s.box_id FROM generation_jobs j JOIN sessions s ON s.id = j.session_id
           JOIN consent_records c ON c.session_id = s.id AND c.purpose = 'live' AND c.withdrawn_at IS NULL
           WHERE j.status = 'queued' AND j.live_wait = 1 AND j.attempts = 0 AND j.next_attempt_at > ? AND s.live_partner IS NULL""",
        (now_iso(),),
    ).fetchall()
    return {row["box_id"] for row in rows}


def find_partner(db: sqlite3.Connection, session: sqlite3.Row) -> sqlite3.Row | None:
    """The longest-waiting visitor at another box whose job is still waiting for a partner."""
    if not enabled() or not consented(db, session["id"]):
        return None
    return db.execute(
        """SELECT s.*, j.id AS job_id FROM generation_jobs j JOIN sessions s ON s.id = j.session_id
           JOIN consent_records c ON c.session_id = s.id AND c.purpose = 'live' AND c.withdrawn_at IS NULL
           WHERE j.status = 'queued' AND j.live_wait = 1 AND j.attempts = 0 AND j.next_attempt_at > ?
             AND s.status = 'queued' AND s.box_id != ? AND s.live_partner IS NULL AND s.match_portrait_id IS NULL
           ORDER BY j.created_at LIMIT 1""",
        (now_iso(), session["box_id"]),
    ).fetchone()


def pair(db: sqlite3.Connection, leader: sqlite3.Row, follower: sqlite3.Row) -> str | None:
    """Pair two visitors. Returns the leader's job id, or None if the leader was taken meanwhile.

    The caller holds the write lock and has checked that the follower is uploaded and unpaired.
    """
    claimed = db.execute(
        """UPDATE sessions SET match_portrait_id=?, live_partner=?, live_role=?
           WHERE id=? AND status='queued' AND live_partner IS NULL AND match_portrait_id IS NULL""",
        (follower["portrait_id"], follower["id"], LEAD, leader["id"]),
    ).rowcount
    if claimed != 1:
        return None
    moved = db.execute(
        "UPDATE sessions SET status='queued', match_portrait_id=?, live_partner=?, live_role=?, error=NULL WHERE id=? AND status IN ('uploaded', 'failed')",
        (leader["portrait_id"], leader["id"], FOLLOW, follower["id"]),
    ).rowcount
    if moved != 1:
        raise RuntimeError("follower changed while the write lock was held")
    db.execute("UPDATE generation_jobs SET next_attempt_at=?, live_wait=0 WHERE id=?", (now_iso(), leader["job_id"]))
    for session in (leader, follower):
        audit.record(db, "live_matched", session_id=session["id"], device_id=session["box_id"])
        presence_events.emit(db, "match.live_created", session["box_id"], session_id=session["id"])
    return leader["job_id"]


def follower_of(db: sqlite3.Connection, session_id: str) -> sqlite3.Row | None:
    return db.execute("SELECT * FROM sessions WHERE live_partner=? AND live_role=?", (session_id, FOLLOW)).fetchone()


def leader_of(db: sqlite3.Connection, session: sqlite3.Row) -> str | None:
    return session["live_partner"] if session["live_role"] == FOLLOW else None


def unpair(db: sqlite3.Connection, session_id: str) -> str | None:
    """Break a pair and clear both counterparts. Returns the partner's id, if there was one."""
    row = db.execute("SELECT live_partner FROM sessions WHERE id=?", (session_id,)).fetchone()
    partner = row["live_partner"] if row else None
    if partner is None:
        return None
    for sid in (session_id, partner):
        db.execute("UPDATE sessions SET live_partner=NULL, live_role=NULL, match_portrait_id=NULL WHERE id=? AND live_partner IS NOT NULL", (sid,))
    audit.record(db, "live_unpaired", session_id=session_id)
    return partner


def complete_follower(db: sqlite3.Connection, leader_id: str, output_path: str) -> None:
    """The follower receives the identical strip: the same file, kept and deleted with the leader's."""
    follower = follower_of(db, leader_id)
    if follower is None:
        return
    if db.execute(
        "UPDATE sessions SET status='complete', output_path=?, finished_at=? WHERE id=? AND status='queued'",
        (output_path, now_iso(), follower["id"]),
    ).rowcount:
        presence_events.emit(db, "generation.completed", follower["box_id"], session_id=follower["id"])


def fail_follower(db: sqlite3.Connection, leader_id: str, message: str) -> None:
    follower = follower_of(db, leader_id)
    if follower is None:
        return
    db.execute("UPDATE sessions SET status='failed', error=? WHERE id=? AND status='queued'", (message, follower["id"]))
    presence_events.emit(db, "generation.failed", follower["box_id"], session_id=follower["id"])
    unpair(db, leader_id)


def match_kind(session: sqlite3.Row) -> str | None:
    if session["live_partner"]:
        return "live"
    match = session["match_portrait_id"]
    if match is None:
        return None
    return "seed" if match.startswith("seed:") else "pool"
