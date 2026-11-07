"""Dashboard password, sessions and request checks (ADR-022).

The dashboard only answers requests from the Mac itself, checks the Host header against DNS rebinding,
needs a password, and protects every form with a per-session token.
"""
from __future__ import annotations
import hashlib
import hmac
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from ..core import now_iso

ITERATIONS = 300_000
MIN_PASSWORD = 10
SESSION_SECONDS = 8 * 3600
FREE_ATTEMPTS = 5
# "testclient" is the address Starlette's test client reports; no real connection can have it.
LOCAL_CLIENTS = frozenset({"127.0.0.1", "::1", "testclient"})
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]"})
COOKIE = "photobox_dashboard"


def set_password(db: sqlite3.Connection, password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"the password needs at least {MIN_PASSWORD} characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    db.execute(
        "INSERT INTO dashboard_password(id,salt,hash,iterations,set_at) VALUES(1,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET salt=excluded.salt, hash=excluded.hash, iterations=excluded.iterations, set_at=excluded.set_at",
        (salt.hex(), digest.hex(), ITERATIONS, now_iso()),
    )


def password_is_set(db: sqlite3.Connection) -> bool:
    return db.execute("SELECT 1 FROM dashboard_password WHERE id=1").fetchone() is not None


def password_matches(db: sqlite3.Connection, password: str) -> bool:
    row = db.execute("SELECT salt, hash, iterations FROM dashboard_password WHERE id=1").fetchone()
    if row is None:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(row["salt"]), row["iterations"])
    return hmac.compare_digest(digest.hex(), row["hash"])


@dataclass
class Session:
    csrf: str
    expires: float


@dataclass
class Sessions:
    """Logins live in memory: restarting the dashboard logs everyone out."""

    clock: object = time.monotonic
    sessions: dict[str, Session] = field(default_factory=dict)
    failures: int = 0
    locked_until: float = 0.0

    def create(self) -> str:
        token = secrets.token_urlsafe(32)
        self.sessions[token] = Session(secrets.token_urlsafe(24), self.clock() + SESSION_SECONDS)
        return token

    def get(self, token: str | None) -> Session | None:
        session = self.sessions.get(token or "")
        if session is None or session.expires < self.clock():
            self.sessions.pop(token or "", None)
            return None
        return session

    def end(self, token: str | None) -> None:
        self.sessions.pop(token or "", None)

    def wait_seconds(self) -> int:
        return max(0, int(self.locked_until - self.clock() + 0.999))

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= FREE_ATTEMPTS:
            self.locked_until = self.clock() + min(900, 30 * 2 ** (self.failures - FREE_ATTEMPTS))

    def record_success(self) -> None:
        self.failures = 0
