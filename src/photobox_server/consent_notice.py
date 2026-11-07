"""The consent notice shown to participants on the box's screen (ADR-020, ADR-022).

The operator writes it in the dashboard, or in `consent-notice.txt` before the first dashboard edit.
Its version is a hash of its text, so a recorded consent always names exactly the words that were
shown, and the dashboard keeps every version. A notice with unfilled placeholders is refused, so a
draft can never reach a participant.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path

PLACEHOLDER = "TODO-OPERATOR"
MAX_CHARACTERS = 4000


class NoticeError(Exception):
    """No usable notice. The message is safe to show to an operator."""


def notice_path() -> Path:
    return Path(os.getenv("PHOTOBOX_CONSENT_NOTICE_FILE", "consent-notice.txt"))


def notice_version(text: str) -> str:
    return "notice-" + hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:12]


def check_notice(text: str) -> None:
    text = text.strip()
    if not text:
        raise NoticeError("the consent notice is empty")
    if PLACEHOLDER in text:
        raise NoticeError(f"the consent notice still contains {PLACEHOLDER} placeholders")
    if len(text) > MAX_CHARACTERS:
        raise NoticeError(f"the consent notice is longer than {MAX_CHARACTERS} characters")


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
