"""Presence simulator (milestone S3): 2-5 virtual boxes for the dashboard preview.

Developer mode only. Virtual boxes live in their own table with ids like `sim:1`, which no real
device can have, and every event they cause is marked simulated. A box's own snapshot leaves both
out (presence.snapshot without include_simulated), so simulated activity can never reach a real
screen. The simulator never touches sessions, portraits, consent or jobs.

Actions, each mirroring what a real box causes:
    connect     the box reports in (online, idle)
    disconnect  the box stops reporting (offline at once)
    start       a visitor gives consent (session.started; the box reports capturing)
    capture     the photo is uploaded and generation starts (portrait.captured, generation.started)
    finish      the strip is done and the box is back at its start screen (generation.completed, idle)
    cancel      the session is cancelled or withdrawn (session.ended, idle)
    expire      five minutes pass without any sign of the session: its events are moved back in time
"""
from __future__ import annotations
import sqlite3
from datetime import datetime, timedelta, timezone
from ..config import developer_mode
from . import events
from .projection import LEASE_SECONDS, Box

MAX_BOXES = 5
ACTIONS = ("connect", "disconnect", "start", "capture", "finish", "cancel", "expire")
PREFIX = "sim:"


class SimulatorError(ValueError):
    """The action was refused; the message says why."""


def enabled() -> bool:
    return developer_mode()


def _require() -> None:
    if not enabled():
        raise SimulatorError("the simulator needs PHOTOBOX_DEVELOPER_MODE=true")


def add_box(db: sqlite3.Connection, label: str) -> str:
    from . import validate_label
    _require()
    label = validate_label(label)
    rows = db.execute("SELECT box_id FROM simulated_boxes").fetchall()
    if len(rows) >= MAX_BOXES:
        raise SimulatorError(f"at most {MAX_BOXES} simulated boxes")
    taken = {row["box_id"] for row in rows}
    box_id = next(f"{PREFIX}{n}" for n in range(1, MAX_BOXES + 2) if f"{PREFIX}{n}" not in taken)
    db.execute("INSERT INTO simulated_boxes(box_id,label,connected,state,created_at) VALUES(?,?,1,'idle',?)",
               (box_id, label, datetime.now(timezone.utc).isoformat()))
    return box_id


def reset(db: sqlite3.Connection) -> None:
    """Remove every simulated box and simulated event. Allowed even outside developer mode, to clean up."""
    db.execute("DELETE FROM simulated_boxes")
    db.execute("DELETE FROM presence_events WHERE simulated = 1")


def act(db: sqlite3.Connection, box_id: str, action: str, now: datetime | None = None) -> None:
    _require()
    if action not in ACTIONS:
        raise SimulatorError(f"unknown action {action!r}")
    box = db.execute("SELECT * FROM simulated_boxes WHERE box_id = ?", (box_id,)).fetchone()
    if box is None:
        raise SimulatorError("unknown simulated box")
    now = now or datetime.now(timezone.utc)
    session = box["session_id"]

    def emit(kind: str) -> None:
        events.emit(db, kind, box_id, session_id=session, occurred_at=now, simulated=True)

    def update(**fields) -> None:
        assignments = ", ".join(f"{name} = ?" for name in fields)
        db.execute(f"UPDATE simulated_boxes SET {assignments} WHERE box_id = ?", (*fields.values(), box_id))

    if action == "connect":
        update(connected=1, state="idle", session_id=None)
        return
    if action == "disconnect":
        update(connected=0)
        return
    if not box["connected"]:
        raise SimulatorError("connect the box first")
    if action == "start":
        if session is not None:
            raise SimulatorError("a session is already running; finish, cancel or expire it first")
        session = f"{box_id}-session-{box['sessions'] + 1}"
        # Conditional, like a real session's state change: of two clicks at once only one starts a session.
        claimed = db.execute(
            "UPDATE simulated_boxes SET state='capturing', session_id=?, sessions=sessions+1 WHERE box_id=? AND session_id IS NULL",
            (session, box_id),
        ).rowcount
        if claimed != 1:
            raise SimulatorError("a session is already running; finish, cancel or expire it first")
        emit("session.started")
        return
    if session is None:
        raise SimulatorError("start a session first")
    if action == "capture":
        emit("portrait.captured")
        emit("generation.started")
        update(state="generating")
    elif action == "finish":
        emit("generation.completed")
        update(state="idle", session_id=None)
    elif action == "cancel":
        emit("session.ended")
        update(state="idle", session_id=None)
    elif action == "expire":
        # The box keeps reporting, but no longer a session state, so nothing renews the lease.
        back = timedelta(seconds=LEASE_SECONDS + 1)
        for row in db.execute("SELECT event_id, occurred_at FROM presence_events WHERE box_id = ? AND simulated = 1", (box_id,)).fetchall():
            moved = datetime.fromisoformat(row["occurred_at"]) - back
            db.execute("UPDATE presence_events SET occurred_at = ? WHERE event_id = ?", (moved.isoformat(), row["event_id"]))
        update(state=None, session_id=None)


def simulated_boxes(db: sqlite3.Connection, now: datetime) -> list[Box]:
    """Virtual boxes as the projection sees them. A connected one has just reported its state."""
    return [Box(row["box_id"], row["label"], row["state"], now if row["connected"] else None, simulated=True)
            for row in db.execute("SELECT * FROM simulated_boxes ORDER BY box_id")]


def listing(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return db.execute("SELECT * FROM simulated_boxes ORDER BY box_id").fetchall()
