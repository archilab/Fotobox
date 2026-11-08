"""Turn recorded events and status reports into a per-box view (ADR-023). Pure: no database, no clock.

Rules, each a test in tests/test_presence.py:
- online means the box reported within OFFLINE_SECONDS, and never that a person is there;
- an offline box shows no activity at all;
- a session is live from session.started until it ends, the box reports an idle state after the
  session's last event, the box goes offline, LEASE_SECONDS pass without a sign of it, or
  MAX_SESSION_SECONDS pass in total; a status report renews it only while the box reports a
  session state, and only for a session that already started;
- an ended session can never become live again, whatever order events arrive in;
- a recent photo lasts `recent_seconds` after portrait.captured.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from .events import PresenceEvent

OFFLINE_SECONDS = 90  # the same as the dashboard (ADR-022)
LEASE_SECONDS = 300
MAX_SESSION_SECONDS = 1800  # the kiosk stops waiting for a strip after 30 minutes
PERSON_MAX_SECONDS = 1800  # a detection that never ends is more likely a poster than a visitor (ADR-029)
PERSON_EVENTS = frozenset({"person.arrived", "person.left"})
SESSION_STATES = frozenset({"consent", "capturing", "generating", "showing"})
IDLE_STATES = frozenset({"idle", "setup", "not ready"})
SESSION_EVENTS = frozenset({"session.started", "portrait.captured", "generation.started", "generation.completed",
                            "generation.failed", "match.live_created", "match.pool_created"})
DeviceState = Literal["online", "offline"]
ActivityState = Literal["session_active", "person_nearby", "recent_photo", "none"]


@dataclass(frozen=True)
class Box:
    box_id: str
    label: str | None
    status: str | None = None  # the latest reported state
    reported_at: datetime | None = None
    simulated: bool = False


@dataclass(frozen=True)
class Settings:
    recent_seconds: int = 180
    detail: Literal["coarse", "detailed"] = "coarse"


@dataclass(frozen=True)
class BoxView:
    box_id: str
    label: str | None
    device_state: DeviceState
    activity_state: ActivityState
    activity_age_seconds: int | None
    # A visitor there is waiting for a live partner. Set by presence.snapshot from the job queue (ADR-024).
    match_available: bool = False
    simulated: bool = False


@dataclass
class _Session:
    started: datetime
    last_event: datetime
    ended: bool = False


@dataclass
class _Track:
    sessions: dict[str, _Session] = field(default_factory=dict)
    ended: set[str] = field(default_factory=set)
    last_photo: datetime | None = None
    simulated: bool = False
    person_at: datetime | None = None
    person_here: bool = False


def _seconds(later: datetime, earlier: datetime) -> float:
    return (later - earlier).total_seconds()


def _track(events: list[PresenceEvent]) -> dict[str, _Track]:
    tracks: dict[str, _Track] = {}
    for event in events:
        track = tracks.setdefault(event.box_id, _Track())
        track.simulated = track.simulated or event.simulated
        if event.type == "portrait.captured" and (track.last_photo is None or event.occurred_at > track.last_photo):
            track.last_photo = event.occurred_at
        if event.type in PERSON_EVENTS:
            if track.person_at is None or event.occurred_at >= track.person_at:
                track.person_at, track.person_here = event.occurred_at, event.type == "person.arrived"
            continue
        sid = event.session_id
        if sid is None:
            continue
        if event.type == "session.ended":
            track.ended.add(sid)
            continue
        if event.type not in SESSION_EVENTS:
            continue
        session = track.sessions.get(sid)
        if event.type == "session.started":
            if session is None:
                track.sessions[sid] = _Session(event.occurred_at, event.occurred_at)
            else:
                session.started = min(session.started, event.occurred_at)
                session.last_event = max(session.last_event, event.occurred_at)
        elif session is not None:
            # Activity without a recorded start never makes a session live on its own.
            session.last_event = max(session.last_event, event.occurred_at)
    return tracks


def _live(session: _Session, box: Box, online: bool, now: datetime) -> bool:
    if not online or _seconds(now, session.started) > MAX_SESSION_SECONDS:
        return False
    renewed = session.last_event
    if box.reported_at is not None and box.reported_at >= session.started:
        if box.status in IDLE_STATES and box.reported_at > session.last_event:
            return False  # the box is back at its start screen: the visitor has gone
        if box.status in SESSION_STATES:
            renewed = max(renewed, box.reported_at)
    return _seconds(now, renewed) <= LEASE_SECONDS


def project(boxes: list[Box], events: list[PresenceEvent], now: datetime, settings: Settings = Settings()) -> dict[str, BoxView]:
    tracks = _track(events)
    views = {}
    for box in boxes:
        track = tracks.get(box.box_id, _Track())
        online = box.reported_at is not None and 0 <= _seconds(now, box.reported_at) <= OFFLINE_SECONDS
        activity: ActivityState = "none"
        age = None
        if online:
            if any(_live(s, box, online, now) for sid, s in track.sessions.items() if sid not in track.ended):
                activity = "session_active"
            elif track.person_here and track.person_at is not None and _seconds(now, track.person_at) <= PERSON_MAX_SECONDS:
                activity = "person_nearby"  # seen by a camera: never counts as a visitor who has agreed
            elif track.last_photo is not None and 0 <= _seconds(now, track.last_photo) <= settings.recent_seconds:
                activity = "recent_photo"
                if settings.detail == "detailed":
                    age = int(_seconds(now, track.last_photo))
        views[box.box_id] = BoxView(box.box_id, box.label, "online" if online else "offline", activity, age,
                                    simulated=box.simulated or track.simulated)
    return views


PRIORITY = {"session_active": 0, "person_nearby": 1, "recent_photo": 2}


def order(views: list[BoxView]) -> list[BoxView]:
    """Live first, then someone nearby, then recent photos, then online, then offline; by name within each."""
    def rank(view: BoxView) -> tuple:
        if view.device_state == "offline":
            level = 4
        else:
            level = PRIORITY.get(view.activity_state, 3)
        return (level, (view.label or "~").casefold(), view.box_id)
    return sorted(views, key=rank)
