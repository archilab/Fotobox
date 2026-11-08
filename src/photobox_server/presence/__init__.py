"""Social presence (ADR-023): what is happening at the other boxes, told truthfully.

The service reads recorded events, status reports, box labels and pool availability, and builds
the snapshot a box shows. It never matches, generates or stores anything about a person.
"""
from __future__ import annotations
import os
import re
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from ..config import ConfigError, int_setting
from . import events, messages
from .projection import Box, BoxView, Settings, order, project

MAX_LOCATIONS = 5
LABEL_PATTERN = re.compile(r"^[\w][\w .,'&()/+-]{0,31}$")


def settings() -> Settings:
    detail = os.getenv("PHOTOBOX_PUBLIC_ACTIVITY_DETAIL", "coarse").strip().lower()
    if detail not in ("coarse", "detailed"):
        raise ConfigError(f"PHOTOBOX_PUBLIC_ACTIVITY_DETAIL={detail!r} must be 'coarse' or 'detailed'")
    return Settings(recent_seconds=int_setting("PHOTOBOX_PRESENCE_RECENT_SECONDS", 180, 30, 1800), detail=detail)  # type: ignore[arg-type]


def validate_label(label: str) -> str:
    """A public box name: 1-32 letters, digits, spaces and . , ' & ( ) / + -, starting with a letter or digit."""
    cleaned = messages.clean(label, limit=64)
    if not LABEL_PATTERN.fullmatch(cleaned) or cleaned.startswith("_"):
        raise ValueError("a box name must be 1-32 letters, digits, spaces or . , ' & ( ) / + - and start with a letter or digit")
    return cleaned


def set_label(db: sqlite3.Connection, device_id: str, label: str | None) -> bool:
    """Set or clear (None or empty) the public name of a box. Returns False for an unknown or revoked box."""
    value = validate_label(label) if label and label.strip() else None
    return db.execute("UPDATE devices SET public_label=? WHERE id=? AND revoked_at IS NULL", (value, device_id)).rowcount == 1


def _boxes(db: sqlite3.Connection) -> list[Box]:
    rows = db.execute(
        """SELECT d.id, d.public_label, s.state, s.reported_at FROM devices d
           LEFT JOIN device_status s ON s.device_id = d.id WHERE d.revoked_at IS NULL"""
    ).fetchall()
    return [Box(row["id"], row["public_label"], row["state"],
                datetime.fromisoformat(row["reported_at"]) if row["reported_at"] else None) for row in rows]


def earlier_portrait_available(db: sqlite3.Connection, now: datetime) -> bool:
    """A visitor's pool portrait with valid pool consent exists. Seeds never count (ADR-023)."""
    row = db.execute(
        """SELECT 1 FROM portraits p JOIN sessions s ON s.id = p.session_id
           JOIN consent_records c ON c.session_id = s.id AND c.purpose = 'pool' AND c.withdrawn_at IS NULL
           WHERE p.pool_eligible = 1 AND p.expires_at > ? AND s.status != 'withdrawn'
             LIMIT 1""",
        (now.isoformat(),),
    ).fetchone()
    return row is not None


def live_partner(db: sqlite3.Connection, self_box_id: str, views: dict[str, BoxView]) -> BoxView | None:
    """The box this box's current visitor is paired with live (ADR-024), while that visitor is still here."""
    own = views.get(self_box_id)
    if own is None or own.activity_state != "session_active":
        return None
    row = db.execute(
        """SELECT p.box_id FROM sessions s JOIN sessions p ON p.id = s.live_partner
           WHERE s.box_id = ? AND s.status IN ('queued', 'processing', 'complete') ORDER BY s.created_at DESC LIMIT 1""",
        (self_box_id,),
    ).fetchone()
    return views.get(row["box_id"]) if row else None


def _public(view: BoxView) -> dict:
    return {
        "box_id": view.box_id, "label": view.label, "device_state": view.device_state,
        "activity_state": view.activity_state, "activity_age_seconds": view.activity_age_seconds,
        "match_available": view.match_available, "simulated": view.simulated,
        "status": messages.status_word(view),
    }


def snapshot(db: sqlite3.Connection, self_box_id: str, now: datetime | None = None, *, include_simulated: bool = False) -> dict:
    """The sanitized view for one box: the other boxes, pool availability and the message to show.

    Simulated events are left out unless asked for; only a developer preview may ask (milestone S3).
    """
    now = now or datetime.now(timezone.utc)
    config = settings()
    boxes = _boxes(db)
    if include_simulated:
        from .simulator import simulated_boxes
        boxes += simulated_boxes(db, now)
    views = project(boxes, events.load(db, include_simulated=include_simulated), now, config)
    from .. import live
    waiting = live.waiting_boxes(db)
    views = {box_id: replace(view, match_available=True) if box_id in waiting and view.device_state == "online" else view
             for box_id, view in views.items()}
    me = next((box for box in boxes if box.box_id == self_box_id), None)
    others = order([view for box_id, view in views.items() if box_id != self_box_id])
    # Any box's pool portrait counts: matching may use one taken at this box too.
    earlier = earlier_portrait_available(db, now)
    unavailable = me is not None and me.status == "not ready"
    key, about = messages.choose(self_unavailable=unavailable, locations=others, earlier_portrait=earlier,
                                 live_partner=live_partner(db, self_box_id, views))
    return {
        "type": "presence.snapshot",
        "generated_at": now.isoformat(),
        "detail": config.detail,
        "self": {"box_id": self_box_id, "label": me.label if me else None, "state": "unavailable" if unavailable else "ready"},
        "locations": [_public(view) for view in others[:MAX_LOCATIONS]],
        "more_locations": max(0, len(others) - MAX_LOCATIONS),
        "earlier_portrait_available": earlier,
        "message": messages.render(key, about),
    }
