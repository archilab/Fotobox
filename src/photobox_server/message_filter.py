"""What visitors may not send each other (spec section 15.6, ADR-027).

A proportionate first check, not moderation: a short word list the operator edits in the dashboard,
plus contact details, because two strangers should not swap phone numbers through a photo booth.
A blocked message gets a neutral refusal and is not relayed. Nothing about it is stored.
"""
from __future__ import annotations
import re
import sqlite3
import unicodedata
from .core import now_iso

MAX_WORDS = 500
MAX_WORD_LENGTH = 60
DEFAULT_WORDS = (
    "arschloch", "wichser", "hurensohn", "fotze", "schlampe", "nutte", "missgeburt", "spast", "nazi", "heil hitler",
    "asshole", "bastard", "bitch", "cunt", "motherfucker", "fuck", "fucking", "whore", "slut", "retard",
)
CONTACT = re.compile(
    r"(?:\d[\s./-]*){6,}"            # a phone number, however it was spoken
    r"|\S+@\S+|\S+\s+(?:at|ät)\s+\S+\s+(?:punkt|dot)\s+\w+"
    r"|https?://|www\.|\b[\w-]+\.(?:com|de|net|org|at|ch)\b"
    r"|\b(?:whatsapp|instagram|insta|snapchat|telegram|signal|tiktok)\b",
    re.IGNORECASE,
)
TRANSLITERATE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).casefold().translate(TRANSLITERATE).split())


def load_words(db: sqlite3.Connection) -> list[str]:
    row = db.execute("SELECT words FROM message_filter WHERE id=1").fetchone()
    return list(DEFAULT_WORDS) if row is None else [word for word in row["words"].splitlines() if word]


def save_words(db: sqlite3.Connection, text: str) -> list[str]:
    """Store one word or phrase per line. Raises ValueError if the list is too long."""
    words = sorted({normalize(line) for line in text.splitlines() if line.strip()})
    if len(words) > MAX_WORDS or any(len(word) > MAX_WORD_LENGTH for word in words):
        raise ValueError(f"at most {MAX_WORDS} entries of {MAX_WORD_LENGTH} characters each")
    db.execute(
        "INSERT INTO message_filter(id,words,updated_at) VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET words=excluded.words, updated_at=excluded.updated_at",
        ("\n".join(words), now_iso()),
    )
    return words


def is_blocked(text: str, words: list[str]) -> bool:
    if CONTACT.search(text):
        return True
    normalized = normalize(text)
    return any(re.search(rf"(?<!\w){re.escape(normalize(word))}(?!\w)", normalized) for word in words)
