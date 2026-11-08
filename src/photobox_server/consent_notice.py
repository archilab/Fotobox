"""The consent notices shown to participants on the box's screen (ADR-020, ADR-022, ADR-024).

There are two kinds. The processing notice comes first and is required. The pool notice is a
separate, optional second question: may the photo be combined with other visitors' photos until
a stated time. Without a pool notice the box does not ask, and no photo enters the pool.

The operator writes it in the dashboard, or in `consent-notice.txt` before the first dashboard edit.
Its version is a hash of its text, so a recorded consent always names exactly the words that were
shown, and the dashboard keeps every version. A notice with unfilled placeholders is refused, so a
draft can never reach a participant.
"""
from __future__ import annotations
import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

PLACEHOLDER = "TODO-OPERATOR"
MAX_CHARACTERS = 4000
UNTIL, HOURS = "{until}", "{hours}"
logger = logging.getLogger(__name__)


class NoticeError(Exception):
    """No usable notice. The message is safe to show to an operator."""


def notice_path() -> Path:
    return Path(os.getenv("PHOTOBOX_CONSENT_NOTICE_FILE", "consent-notice.txt"))


def notice_version(text: str) -> str:
    return "notice-" + hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:12]


def check_notice(text: str, kind: str = "processing") -> None:
    name = "the pool notice" if kind == "pool" else "the consent notice"
    text = text.strip()
    if not text:
        raise NoticeError(f"{name} is empty")
    if PLACEHOLDER in text:
        raise NoticeError(f"{name} still contains {PLACEHOLDER} placeholders")
    if len(text) > MAX_CHARACTERS:
        raise NoticeError(f"{name} is longer than {MAX_CHARACTERS} characters")
    if kind == "pool" and UNTIL not in text:
        raise NoticeError(f"the pool notice must contain {UNTIL}, where the box shows when the photo leaves the pool")


def file_notice() -> str:
    try:
        return notice_path().read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise NoticeError("no consent notice is configured: write one in the dashboard, or copy consent-notice.example.txt to consent-notice.txt and complete it") from exc


def load_notice() -> tuple[str, str]:
    """Return (version, text) of the active notice: the dashboard's, or else the file's. Raises NoticeError."""
    from .core import db_connect
    from .library import active_notice
    with db_connect() as db:
        stored = active_notice(db)
    if stored is not None:
        return stored
    text = file_notice()
    check_notice(text)
    return notice_version(text), text


# The pool notice (ADR-024)

def pool_notice_path() -> Path:
    return Path(os.getenv("PHOTOBOX_POOL_NOTICE_FILE", "pool-notice.txt"))


def pool_version(text: str) -> str:
    return "pool-" + hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:12]


def pool_hours() -> int:
    from .config import int_setting
    return int_setting("PHOTOBOX_POOL_HOURS", 24, 1, 720)


@dataclass(frozen=True)
class PoolNotice:
    version: str  # the text's version plus the hours, so a consent names exactly what was shown
    text: str  # with the end time filled in
    until: datetime  # when the photo leaves the pool; the box sends it back with the consent
    until_text: str


def pool_until(now: datetime, hours: int) -> datetime:
    """The end time to show: rounded down to the minute, so the photo never stays longer than shown."""
    return (now + timedelta(hours=hours)).replace(second=0, microsecond=0)


def format_until(moment: datetime) -> str:
    # The server's local time, as the visitor reads it on the box's clock.
    return moment.astimezone().strftime("%d.%m. %H:%M")


def stored_pool_notice() -> tuple[str, str] | None:
    """The active pool notice as written: the dashboard's, or else the file's. None if there is none."""
    from .core import db_connect
    from .library import active_notice
    with db_connect() as db:
        stored = active_notice(db, "pool")
    if stored is not None:
        return stored
    try:
        text = pool_notice_path().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return pool_version(text), text


def load_pool_notice(now: datetime | None = None) -> PoolNotice | None:
    """The pool question for a box, or None if pool use is not offered. A broken notice is not offered."""
    stored = stored_pool_notice()
    if stored is None:
        return None
    version, template = stored
    try:
        check_notice(template, "pool")
    except NoticeError as exc:
        logger.warning("pool use is not offered: %s", exc)
        return None
    hours = pool_hours()
    until = pool_until(now or datetime.now(timezone.utc), hours)
    shown = format_until(until)
    text = template.replace(HOURS, str(hours)).replace(UNTIL, shown)
    return PoolNotice(f"{version}-{hours}h", text, until, shown)
