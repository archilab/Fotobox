"""Per-device bearer tokens. Only a hash is stored; the token is shown once at creation."""
from __future__ import annotations
import hashlib
import re
import secrets
import sqlite3
from .core import now_iso

DEVICE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def hash_token(token: str) -> str:
    # Tokens are 256-bit random values, so a fast hash is sufficient (unlike passwords).
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_device(db: sqlite3.Connection, device_id: str) -> str:
    """Register a device and return its token, which cannot be recovered later."""
    if not DEVICE_ID_PATTERN.fullmatch(device_id):
        raise ValueError("device id must be 1-64 letters, digits, '-' or '_', starting with a letter or digit")
    token = secrets.token_urlsafe(32)
    try:
        db.execute("INSERT INTO devices(id,token_hash,created_at) VALUES(?,?,?)", (device_id, hash_token(token), now_iso()))
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"device {device_id!r} already exists") from exc
    return token


def revoke_device(db: sqlite3.Connection, device_id: str) -> bool:
    """Revoke a device's token; returns False if there is no active device with this id."""
    cursor = db.execute("UPDATE devices SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (now_iso(), device_id))
    return cursor.rowcount == 1


def authenticate(db: sqlite3.Connection, token: str) -> str | None:
    """Return the id of the active device that owns this token, if any."""
    row = db.execute("SELECT id FROM devices WHERE token_hash=? AND revoked_at IS NULL", (hash_token(token),)).fetchone()
    return row["id"] if row else None
