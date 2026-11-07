"""Pairing a photo booth with a short code instead of copying its token by hand (ADR-021).

The operator runs `admin device pair NAME` on the server, which shows a 6-digit code valid for five
minutes. The box sends the code to the public pairing endpoint and receives its token. A code works
once, only the newest pairing is active, and wrong guesses end it, so the small code space is safe.
"""
from __future__ import annotations
import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from . import audit
from .auth import DEVICE_ID_PATTERN, hash_token
from .core import now_iso

CODE_DIGITS = 6
VALID_MINUTES = 5
MAX_WRONG_GUESSES = 5


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def start_pairing(db: sqlite3.Connection, device_id: str) -> str:
    """Create a pairing code for a new or existing device. Any earlier unused code stops working."""
    if not DEVICE_ID_PATTERN.fullmatch(device_id):
        raise ValueError("device id must be 1-64 letters, digits, '-' or '_', starting with a letter or digit")
    code = f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"
    db.execute("DELETE FROM device_pairings WHERE used_at IS NULL")
    expires = (datetime.now(timezone.utc) + timedelta(minutes=VALID_MINUTES)).isoformat()
    db.execute(
        "INSERT INTO device_pairings(device_id,code_hash,created_at,expires_at) VALUES(?,?,?,?)",
        (device_id, _hash(code), now_iso(), expires),
    )
    audit.record(db, "pairing_started", device_id=device_id)
    return code


def complete_pairing(db: sqlite3.Connection, code: str) -> tuple[str, str] | None:
    """Exchange a code for (device id, token), or return None. Every wrong guess counts against the pairing."""
    row = db.execute(
        "SELECT * FROM device_pairings WHERE used_at IS NULL AND expires_at > ? AND wrong_guesses < ?",
        (now_iso(), MAX_WRONG_GUESSES),
    ).fetchone()
    if row is None:
        return None
    if not (len(code) == CODE_DIGITS and code.isdigit() and hmac.compare_digest(_hash(code), row["code_hash"])):
        db.execute("UPDATE device_pairings SET wrong_guesses = wrong_guesses + 1 WHERE id=?", (row["id"],))
        return None
    token = secrets.token_urlsafe(32)
    device_id = row["device_id"]
    # A box that pairs again gets a new token, and its old one stops working.
    if db.execute("SELECT 1 FROM devices WHERE id=?", (device_id,)).fetchone():
        db.execute("UPDATE devices SET token_hash=?, revoked_at=NULL WHERE id=?", (hash_token(token), device_id))
    else:
        db.execute("INSERT INTO devices(id,token_hash,created_at) VALUES(?,?,?)", (device_id, hash_token(token), now_iso()))
    db.execute("UPDATE device_pairings SET used_at=? WHERE id=?", (now_iso(), row["id"]))
    audit.record(db, "device_paired", device_id=device_id)
    return device_id, token
