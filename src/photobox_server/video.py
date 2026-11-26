"""The hidden video link between two boxes (ADR-045).

A visitor holds the button for 5 s on the start screen and agrees on a short notice; the server then asks
one free box, which shows the request for REQUEST_SECONDS. The link exists only once someone there agrees
too, and lasts at most PHOTOBOX_VIDEO_SECONDS; either side ends it at once. Picture and sound go directly
between the two boxes (step V2) and never through the server, which only keeps who called whom and when,
for a day, and records both agreements in the audit trail. Nothing about a person is stored.

Off unless the operator switches it on (PHOTOBOX_VIDEO_LINK=true), once the notices mention it.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from . import audit
from .config import int_setting
from .presence import messages
from .presence.projection import OFFLINE_SECONDS

REQUESTED, CONNECTED, ENDED = "requested", "connected", "ended"
CALLER, CALLEE = "caller", "callee"
REQUEST_SECONDS = 20
KEEP_HOURS = 24  # who called whom, then deleted
END_REASONS = ("caller", "callee", "time", "unanswered", "cancelled")
# The messages that set up picture and sound between the two boxes (WebRTC: offer, answer, network
# candidates, step V2). They hold the boxes' network addresses, so they live in memory only, never in the
# database or the log, and are dropped when the link ends. Picture and sound themselves never come here.
SIGNAL_KINDS = ("offer", "answer", "candidate")
MAX_SIGNAL_LENGTH = 20_000
MAX_SIGNALS = 200  # per link: far more than a connection needs
_signals: dict[str, list[tuple[str, str, str]]] = {}  # link id -> [(sender box, kind, data)]
_signals_lock = threading.Lock()


def enabled() -> bool:
    return os.getenv("PHOTOBOX_VIDEO_LINK", "").strip().lower() in {"1", "true", "yes"}


def link_seconds() -> int:
    return int_setting("PHOTOBOX_VIDEO_SECONDS", 120, 30, 300)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _expire(db: sqlite3.Connection, now: datetime) -> None:
    """End links whose time has run out, and forget old ones."""
    stamp = now.isoformat()
    db.execute("UPDATE video_links SET status=?, ended_at=?, end_reason='unanswered' WHERE status=? AND answer_by<=?",
               (ENDED, stamp, REQUESTED, stamp))
    db.execute("UPDATE video_links SET status=?, ended_at=?, end_reason='time' WHERE status=? AND ends_at<=?",
               (ENDED, stamp, CONNECTED, stamp))
    db.execute("DELETE FROM video_links WHERE created_at<?", ((now - timedelta(hours=KEEP_HOURS)).isoformat(),))


def _active(db: sqlite3.Connection, box: str) -> sqlite3.Row | None:
    return db.execute("SELECT * FROM video_links WHERE status IN (?,?) AND (caller=? OR callee=?) ORDER BY created_at DESC LIMIT 1",
                      (REQUESTED, CONNECTED, box, box)).fetchone()


def free_boxes(db: sqlite3.Connection, caller: str, now: datetime | None = None) -> list[str]:
    """Boxes showing their start screen right now, not resting and not in a link, someone nearby first."""
    now = now or _now()
    cutoff = (now - timedelta(seconds=OFFLINE_SECONDS)).isoformat()
    rows = db.execute(
        """SELECT s.device_id FROM device_status s JOIN devices d ON d.id = s.device_id
           WHERE s.device_id != ? AND d.revoked_at IS NULL AND s.reported_at > ? AND s.state = 'idle' AND s.detail IS NULL
             AND NOT EXISTS (SELECT 1 FROM video_links v WHERE v.status IN (?,?) AND (v.caller = s.device_id OR v.callee = s.device_id))
           ORDER BY s.device_id""",
        (caller, cutoff, REQUESTED, CONNECTED),
    ).fetchall()
    boxes = [row["device_id"] for row in rows]
    if len(boxes) > 1:
        from . import presence
        nearby = {box for box, word in presence.activity_by_box(db, now).items() if word == "NEARBY"}
        boxes.sort(key=lambda box: box not in nearby)
    return boxes


def call(db: sqlite3.Connection, caller: str) -> sqlite3.Row | None:
    """The caller has agreed: ask the first free box. None if no box is free or the caller is already in a link."""
    db.execute("BEGIN IMMEDIATE")  # one box is never asked twice at once
    now = _now()
    _expire(db, now)
    if _active(db, caller) is not None:
        return None
    boxes = free_boxes(db, caller, now)
    if not boxes:
        return None
    link_id = secrets.token_hex(8)
    db.execute("INSERT INTO video_links(id, caller, callee, status, created_at, answer_by) VALUES(?,?,?,?,?,?)",
               (link_id, caller, boxes[0], REQUESTED, now.isoformat(), (now + timedelta(seconds=REQUEST_SECONDS)).isoformat()))
    audit.record(db, "video_agreed", device_id=caller, detail=f"{link_id} caller")
    return db.execute("SELECT * FROM video_links WHERE id=?", (link_id,)).fetchone()


def current(db: sqlite3.Connection, box: str) -> sqlite3.Row | None:
    _expire(db, _now())
    return _active(db, box)


def latest(db: sqlite3.Connection, link_id: str, box: str) -> sqlite3.Row | None:
    """A link this box belongs to, also once it has ended, so both sides learn how it ended."""
    _expire(db, _now())
    return db.execute("SELECT * FROM video_links WHERE id=? AND (caller=? OR callee=?)", (link_id, box, box)).fetchone()


def accept(db: sqlite3.Connection, link_id: str, box: str) -> sqlite3.Row | None:
    """Someone at the asked box agrees. None if the request is not for this box or has ended."""
    db.execute("BEGIN IMMEDIATE")
    now = _now()
    _expire(db, now)
    row = db.execute("SELECT * FROM video_links WHERE id=? AND callee=? AND status=?", (link_id, box, REQUESTED)).fetchone()
    if row is None:
        return None
    db.execute("UPDATE video_links SET status=?, connected_at=?, ends_at=? WHERE id=?",
               (CONNECTED, now.isoformat(), (now + timedelta(seconds=link_seconds())).isoformat(), link_id))
    audit.record(db, "video_agreed", device_id=box, detail=f"{link_id} callee")
    return db.execute("SELECT * FROM video_links WHERE id=?", (link_id,)).fetchone()


def end(db: sqlite3.Connection, link_id: str, box: str) -> sqlite3.Row | None:
    """Either side ends the link, or the caller withdraws the request. None if it is not this box's."""
    db.execute("BEGIN IMMEDIATE")
    now = _now()
    _expire(db, now)
    row = db.execute("SELECT * FROM video_links WHERE id=? AND (caller=? OR callee=?)", (link_id, box, box)).fetchone()
    if row is None:
        return None
    if row["status"] != ENDED:
        reason = "cancelled" if row["status"] == REQUESTED else (CALLER if box == row["caller"] else CALLEE)
        db.execute("UPDATE video_links SET status=?, ended_at=?, end_reason=? WHERE id=?", (ENDED, now.isoformat(), reason, link_id))
    forget_signals(link_id)
    return db.execute("SELECT * FROM video_links WHERE id=?", (link_id,)).fetchone()


def send_signal(db: sqlite3.Connection, link_id: str, box: str, kind: str, data: str) -> bool:
    """Pass a setup message to the other box of a connected link. False if the link is not this box's or not live."""
    row = latest(db, link_id, box)
    if row is None or row["status"] != CONNECTED or kind not in SIGNAL_KINDS or len(data) > MAX_SIGNAL_LENGTH:
        return False
    with _signals_lock:
        queue = _signals.setdefault(link_id, [])
        if len(queue) >= MAX_SIGNALS:
            return False
        queue.append((box, kind, data))
    return True


def signals(db: sqlite3.Connection, link_id: str, box: str, after: int) -> list[dict] | None:
    """The other box's setup messages after position `after`, each with its position. None if not this box's link."""
    row = latest(db, link_id, box)
    if row is None:
        return None
    with _signals_lock:
        if row["status"] != CONNECTED:
            _signals.pop(link_id, None)
            return []
        queue = list(_signals.get(link_id, []))
    return [{"n": n, "kind": kind, "data": data} for n, (sender, kind, data) in enumerate(queue, 1) if n > after and sender != box]


def forget_signals(link_id: str) -> None:
    with _signals_lock:
        _signals.pop(link_id, None)


def peek(db: sqlite3.Connection, box: str, now: datetime | None = None) -> sqlite3.Row | None:
    """This box's open link without writing anything, for the presence stream that asks twice a second."""
    stamp = (now or _now()).isoformat()
    row = _active(db, box)
    if row is None:
        return None
    if (row["status"] == REQUESTED and row["answer_by"] <= stamp) or (row["status"] == CONNECTED and row["ends_at"] <= stamp):
        return None
    return row


def view(db: sqlite3.Connection, row: sqlite3.Row | None, box: str) -> dict | None:
    """What a box needs to know about its link: never anything about a person.

    `until` is when the request or the link ends, on the server's clock; the box counts down to it from the
    time the server gives with it, so its own clock does not matter.
    """
    if row is None:
        return None
    role = CALLER if row["caller"] == box else CALLEE
    other = row["callee"] if role == CALLER else row["caller"]
    label = db.execute("SELECT public_label FROM devices WHERE id=?", (other,)).fetchone()
    until = row["answer_by"] if row["status"] == REQUESTED else row["ends_at"]
    return {"id": row["id"], "role": role, "status": row["status"], "until": until if row["status"] != ENDED else None,
            "place": messages.clean(label["public_label"]) if label and label["public_label"] else None,
            "end_reason": row["end_reason"], "link_seconds": link_seconds()}
