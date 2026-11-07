"""Choosing a counterpart from the portrait pool, then from seed images imported by an admin."""
from __future__ import annotations
import random
import re
import sqlite3
import uuid
from pathlib import Path
from . import audit
from .core import SEED_PREFIX, now_iso, purge_stale_strips, storage_root, validate_image, write_private_image

RIGHTS_REFERENCE_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,64}")


def _weighted_choice(rows: list[sqlite3.Row]) -> sqlite3.Row:
    # Less-used images are likelier, so a new image is not drowned out but none is ever excluded.
    return random.choices(rows, weights=[1 / (1 + row["use_count"]) for row in rows], k=1)[0]


def select_match(db: sqlite3.Connection, session_id: str) -> tuple[str, Path] | None:
    """Pick a counterpart, re-checking permission at the moment of use.

    A pool image needs an unexpired entry and a pool consent that has not been withdrawn, so a
    withdrawal or expiry applies immediately, even before its file is gone. Images from the same
    session are never used.
    """
    pool = db.execute(
        """SELECT p.id, p.path, p.use_count FROM portraits p
           JOIN consent_records c ON c.session_id = p.session_id AND c.purpose = 'pool' AND c.withdrawn_at IS NULL
           WHERE p.pool_eligible = 1 AND p.expires_at > ? AND p.session_id != ?""",
        (now_iso(), session_id),
    ).fetchall()
    if pool:
        chosen = _weighted_choice(pool)
        return chosen["id"], Path(chosen["path"])
    seeds = db.execute("SELECT id, path, use_count FROM seeds WHERE removed_at IS NULL").fetchall()
    if seeds:
        chosen = _weighted_choice(seeds)
        return SEED_PREFIX + chosen["id"], Path(chosen["path"])
    return None


def record_use(db: sqlite3.Connection, match_id: str, session_id: str) -> None:
    if match_id.startswith(SEED_PREFIX):
        db.execute("UPDATE seeds SET use_count = use_count + 1 WHERE id = ?", (match_id[len(SEED_PREFIX):],))
    else:
        db.execute("UPDATE portraits SET use_count = use_count + 1 WHERE id = ?", (match_id,))
    audit.record(db, "match_used", session_id=session_id, portrait_id=match_id)


def import_seed(db: sqlite3.Connection, payload: bytes, rights_reference: str) -> str:
    """Add a seed image. Re-encoding drops its metadata. Raises ValueError if it is unsuitable."""
    if not RIGHTS_REFERENCE_PATTERN.fullmatch(rights_reference):
        raise ValueError("rights reference must be 1-64 letters, digits or ._:-")
    image = validate_image(payload)
    image.thumbnail((1600, 1600))
    seed_id = str(uuid.uuid4())
    dest = storage_root() / "seed_portraits" / f"{seed_id}.jpg"
    write_private_image(image, dest, "JPEG", quality=88)
    try:
        db.execute(
            "INSERT INTO seeds(id,path,rights_ref,imported_at) VALUES(?,?,?,?)",
            (seed_id, str(dest), rights_reference, now_iso()),
        )
        audit.record(db, "seed_imported", portrait_id=SEED_PREFIX + seed_id, detail=rights_reference)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    return seed_id


def remove_seed(db: sqlite3.Connection, seed_id: str) -> bool:
    """Stop using a seed and delete it, along with every strip containing it."""
    row = db.execute("SELECT path FROM seeds WHERE id = ? AND removed_at IS NULL", (seed_id,)).fetchone()
    if row is None:
        return False
    db.execute("UPDATE seeds SET removed_at = ? WHERE id = ?", (now_iso(), seed_id))
    path = Path(row["path"])
    if path.parent == storage_root() / "seed_portraits":
        path.unlink(missing_ok=True)
    audit.record(db, "seed_removed", portrait_id=SEED_PREFIX + seed_id)
    purge_stale_strips(db)
    return True
