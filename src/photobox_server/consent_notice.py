"""The consent notice shown to participants on the box's screen (ADR-020).

The operator writes it, the server serves it, and its version is a hash of its text, so a recorded
consent always names exactly the words that were shown. A notice with unfilled placeholders is
refused, so a draft can never reach a participant.
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


def load_notice() -> tuple[str, str]:
    """Return (version, text), or raise NoticeError."""
    path = notice_path()
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise NoticeError("no consent notice is configured: copy consent-notice.example.txt to consent-notice.txt and complete it") from exc
    if not text:
        raise NoticeError("the consent notice is empty")
    if PLACEHOLDER in text:
        raise NoticeError(f"the consent notice still contains {PLACEHOLDER} placeholders")
    if len(text) > MAX_CHARACTERS:
        raise NoticeError(f"the consent notice is longer than {MAX_CHARACTERS} characters")
    return "notice-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12], text
