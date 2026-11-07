from __future__ import annotations
import io
import os
import sqlite3
import stat
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple
from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError
from . import audit
from .schema import migrate

MAX_IMAGE_PIXELS = 20_000_000
MIN_IMAGE_SIDE = 64
MAX_IMAGE_SIDE = 8000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
# Untracked files younger than this may belong to a request still in progress.
ORPHAN_GRACE_SECONDS = 3600
SEED_PREFIX = "seed:"
# Short-lived session images and reusable pool images are kept apart.
PORTRAIT_FOLDERS = ("portraits", "pool_portraits")

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _restrict(path: Path, mode: int) -> None:
    if stat.S_IMODE(path.stat().st_mode) != mode:
        path.chmod(mode)

def storage_root() -> Path:
    p = Path(os.getenv("PHOTOBOX_STORAGE_DIR", "./storage")).resolve()
    p.mkdir(parents=True, exist_ok=True)
    _restrict(p, 0o700)
    for folder in (*PORTRAIT_FOLDERS, "generated", "seed_portraits"):
        (p / folder).mkdir(exist_ok=True)
        _restrict(p / folder, 0o700)
    return p

def write_private_image(img: Image.Image, dest: Path, image_format: str, **options) -> None:
    """Save an image only the server's account can read, appearing at `dest` only when complete.

    A crash leaves at most a temporary file, which cleanup removes as untracked.
    """
    temporary = dest.with_name(f"{dest.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            img.save(handle, image_format, **options)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, dest)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

@contextmanager
def db_connect() -> Iterator[sqlite3.Connection]:
    """Open a connection; commit on success, roll back on error, and always close it."""
    root = storage_root()
    p = Path(os.getenv("PHOTOBOX_DB_PATH", str(root / "photobox.sqlite3"))).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    # Create the file first so it is never readable by others. SQLite gives its -wal and -shm
    # files the database's permissions, but files from an older version need correcting.
    os.close(os.open(p, os.O_WRONLY | os.O_CREAT, 0o600))
    _restrict(p, 0o600)
    for suffix in ("-wal", "-shm"):
        # Another connection closing can remove these at any moment.
        with suppress(FileNotFoundError):
            _restrict(p.with_name(p.name + suffix), 0o600)
    db = sqlite3.connect(p, timeout=15)
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        migrate(db)
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()

def validate_image(payload: bytes) -> Image.Image:
    if len(payload) > int(os.getenv("PHOTOBOX_MAX_UPLOAD_BYTES", "10000000")):
        raise ValueError("image exceeds upload limit")
    try:
        with Image.open(io.BytesIO(payload)) as opened:
            if opened.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError("unsupported image type")
            # Pillow only warns between the limit and twice the limit, so check it here.
            if opened.width * opened.height > MAX_IMAGE_PIXELS:
                raise ValueError("image exceeds pixel limit")
            if max(opened.size) > MAX_IMAGE_SIDE or min(opened.size) < MIN_IMAGE_SIDE:
                raise ValueError(f"image sides must be {MIN_IMAGE_SIDE} to {MAX_IMAGE_SIDE} pixels")
            if getattr(opened, "n_frames", 1) > 1:
                raise ValueError("animated images are not accepted")
            opened.verify()
        with Image.open(io.BytesIO(payload)) as opened:
            return ImageOps.exif_transpose(opened).convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("invalid image") from exc

def save_portrait(img: Image.Image, portrait_id: str, folder: str = "portraits") -> Path:
    dest = storage_root() / folder / f"{portrait_id}.jpg"
    img.thumbnail((1600, 1600))
    write_private_image(img, dest, "JPEG", quality=88)
    return dest

def render_mock_strip(image_a: Image.Image, image_b: Image.Image, frames: int = 4) -> Image.Image:
    if frames not in (3, 4):
        raise ValueError("frames must be 3 or 4")
    width, frame_height, margin, gap = 560, 400, 18, 12
    strip = Image.new("RGB", (width, frame_height * frames + gap * (frames - 1) + 2 * margin + 42), "white")
    draw = ImageDraw.Draw(strip)
    a = ImageOps.fit(image_a.convert("RGB"), ((width - 2 * margin) // 2, frame_height))
    b = ImageOps.fit(image_b.convert("RGB"), ((width - 2 * margin) // 2, frame_height))
    for frame in range(frames):
        y = margin + frame * (frame_height + gap)
        if frame % 2 == 0:
            strip.paste(a, (margin, y)); strip.paste(b, (margin + a.width, y))
        else:
            strip.paste(b, (margin, y)); strip.paste(a, (margin + b.width, y))
        draw.text((margin + 8, y + 8), f"MOCK / NOT AI - FRAME {frame + 1}", fill="red", stroke_width=1, stroke_fill="white")
    draw.text((margin, strip.height - 32), "CONNECTED PHOTOBOX - DEVELOPMENT MOCK", fill="black")
    return strip

def strip_images_active(db: sqlite3.Connection, session: sqlite3.Row) -> bool:
    """A strip contains its images, so it is only available while all of them are still valid."""
    for image_id in (session["portrait_id"], session["match_portrait_id"]):
        if image_id is None:
            continue
        if image_id.startswith(SEED_PREFIX):
            found = db.execute("SELECT 1 FROM seeds WHERE id=? AND removed_at IS NULL", (image_id[len(SEED_PREFIX):],)).fetchone()
        else:
            found = db.execute("SELECT 1 FROM portraits WHERE id=? AND expires_at>?", (image_id, now_iso())).fetchone()
        if found is None:
            return False
    return True

def delete_portrait(db: sqlite3.Connection, portrait: sqlite3.Row, reason: str, session_id: str | None = None) -> None:
    root = storage_root()
    path = Path(portrait["path"])
    if path.parent in {root / folder for folder in PORTRAIT_FOLDERS}:
        path.unlink(missing_ok=True)
    db.execute("DELETE FROM portraits WHERE id=?", (portrait["id"],))
    audit.record(db, "portrait_deleted", portrait_id=portrait["id"], session_id=session_id, detail=reason)

def purge_stale_strips(db: sqlite3.Connection) -> int:
    """Delete every strip containing an image that no longer exists or may no longer be used."""
    root = storage_root()
    stale = db.execute(
        """SELECT id,output_path FROM sessions WHERE output_path IS NOT NULL AND (
               portrait_id IS NULL OR portrait_id NOT IN (SELECT id FROM portraits)
               OR (match_portrait_id IS NOT NULL AND (
                   (match_portrait_id LIKE 'seed:%' AND substr(match_portrait_id, 6) NOT IN (SELECT id FROM seeds WHERE removed_at IS NULL))
                   OR (match_portrait_id NOT LIKE 'seed:%' AND match_portrait_id NOT IN (SELECT id FROM portraits)))))"""
    ).fetchall()
    for record in stale:
        path = Path(record["output_path"])
        if path.parent == root / "generated":
            path.unlink(missing_ok=True)
        db.execute("UPDATE sessions SET output_path=NULL WHERE id=?", (record["id"],))
        audit.record(db, "strip_deleted", session_id=record["id"])
    return len(stale)

class CleanupResult(NamedTuple):
    portraits: int
    strips: int
    untracked_files: int

def cleanup_expired(db: sqlite3.Connection) -> CleanupResult:
    """Delete expired portraits, every strip containing one, and files no record refers to."""
    # Do not remove records for active generations; production cleanup must handle queued jobs explicitly.
    root = storage_root()
    expired = db.execute("SELECT id,path,session_id FROM portraits WHERE expires_at<=?", (now_iso(),)).fetchall()
    for record in expired:
        delete_portrait(db, record, "expired", record["session_id"])
    strips = purge_stale_strips(db)
    tracked = {Path(row[0]).name for row in db.execute("SELECT path FROM portraits")}
    tracked |= {Path(row[0]).name for row in db.execute("SELECT output_path FROM sessions WHERE output_path IS NOT NULL")}
    cutoff = datetime.now(timezone.utc).timestamp() - ORPHAN_GRACE_SECONDS
    untracked = 0
    for folder in (*PORTRAIT_FOLDERS, "generated"):
        for p in (root / folder).iterdir():
            if p.is_file() and p.name not in tracked and p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
                untracked += 1
    db.commit()
    return CleanupResult(len(expired), strips, untracked)
