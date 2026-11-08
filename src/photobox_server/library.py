"""Versioned content edited in the dashboard: prompt sets and consent notices (ADR-022).

Every saved version is kept, so a job or a consent can always be traced to the exact text it used.
Exactly one version of each is active.
"""
from __future__ import annotations
import json
import sqlite3
from . import audit
from .core import now_iso
from .prompts import content_version, default_content, validate_content


class ContentError(ValueError):
    """The content was refused; the message lists why."""


def active_prompt_version(db: sqlite3.Connection) -> str:
    row = db.execute("SELECT version FROM prompt_sets WHERE active=1").fetchone()
    return row["version"] if row else content_version(default_content())


def prompt_content(db: sqlite3.Connection, version: str | None = None) -> dict:
    """The content of a version, or of the active one. Unknown versions fall back to the defaults."""
    row = db.execute("SELECT content FROM prompt_sets WHERE " + ("version=?" if version else "active=1"), (version,) if version else ()).fetchone()
    return json.loads(row["content"]) if row else default_content()


def save_prompt_content(db: sqlite3.Connection, content: dict) -> str:
    """Validate, store and activate content. Returns its version."""
    errors = validate_content(content)
    if errors:
        raise ContentError("; ".join(errors))
    version = content_version(content)
    if db.execute("SELECT 1 FROM prompt_sets WHERE version=?", (version,)).fetchone() is None:
        db.execute("INSERT INTO prompt_sets(version,content,created_at,active) VALUES(?,?,?,0)", (version, json.dumps(content), now_iso()))
    activate_prompt_version(db, version)
    return version


def activate_prompt_version(db: sqlite3.Connection, version: str) -> None:
    if db.execute("SELECT 1 FROM prompt_sets WHERE version=?", (version,)).fetchone() is None:
        if version != content_version(default_content()):
            raise ContentError(f"unknown version {version}")
        db.execute("INSERT INTO prompt_sets(version,content,created_at,active) VALUES(?,?,?,0)", (version, json.dumps(default_content()), now_iso()))
    db.execute("UPDATE prompt_sets SET active = (version = ?)", (version,))
    audit.record(db, "prompts_activated", detail=version)


def prompt_versions(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return db.execute("SELECT version, created_at, active FROM prompt_sets ORDER BY created_at DESC").fetchall()


def active_notice(db: sqlite3.Connection, kind: str = "processing") -> tuple[str, str] | None:
    row = db.execute("SELECT version, text FROM consent_notices WHERE active=1 AND kind=?", (kind,)).fetchone()
    return (row["version"], row["text"]) if row else None


def save_notice(db: sqlite3.Connection, text: str, kind: str = "processing") -> str:
    """Validate, store and activate a consent notice of one kind. Returns its version."""
    from .consent_notice import NoticeError, check_notice, notice_version, pool_version
    try:
        check_notice(text, kind)
    except NoticeError as exc:
        raise ContentError(str(exc)) from exc
    text = text.strip()
    version = pool_version(text) if kind == "pool" else notice_version(text)
    if db.execute("SELECT 1 FROM consent_notices WHERE version=?", (version,)).fetchone() is None:
        db.execute("INSERT INTO consent_notices(version,text,created_at,active,kind) VALUES(?,?,?,0,?)", (version, text, now_iso(), kind))
    db.execute("UPDATE consent_notices SET active = (version = ?) WHERE kind = ?", (version, kind))
    audit.record(db, "pool_notice_activated" if kind == "pool" else "notice_activated", detail=version)
    return version


def notice_versions(db: sqlite3.Connection, kind: str = "processing") -> list[sqlite3.Row]:
    # A pool consent names the text's version plus the hours, such as pool-abc-24h.
    return db.execute(
        """SELECT n.version, n.created_at, n.active,
                  (SELECT COUNT(*) FROM consent_records c WHERE c.policy_version = n.version
                       OR c.policy_version LIKE n.version || '-%h') AS consents
           FROM consent_notices n WHERE n.kind = ? ORDER BY n.created_at DESC""",
        (kind,),
    ).fetchall()


def deactivate_pool_notice(db: sqlite3.Connection) -> None:
    """Stop asking the pool question from the dashboard. A `pool-notice.txt` would still be served."""
    db.execute("UPDATE consent_notices SET active = 0 WHERE kind = 'pool'")
    audit.record(db, "pool_notice_deactivated")
