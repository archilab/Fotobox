from __future__ import annotations
import io
import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

MAX_IMAGE_PIXELS = 20_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def storage_root() -> Path:
    p = Path(os.getenv("PHOTOBOX_STORAGE_DIR", "./storage")).resolve()
    p.mkdir(parents=True, exist_ok=True)
    for folder in ("portraits", "generated", "seed_portraits"):
        (p / folder).mkdir(exist_ok=True)
    return p

@contextmanager
def db_connect() -> Iterator[sqlite3.Connection]:
    """Open a connection; commit on success, roll back on error, and always close it."""
    root = storage_root()
    p = Path(os.getenv("PHOTOBOX_DB_PATH", str(root / "photobox.sqlite3"))).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(p, timeout=15)
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY, box_id TEXT NOT NULL, status TEXT NOT NULL,
            consent_processing INTEGER NOT NULL DEFAULT 0,
            consent_pool INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, finished_at TEXT, error TEXT,
            portrait_id TEXT, match_portrait_id TEXT, output_path TEXT
        );
        CREATE TABLE IF NOT EXISTS portraits (
            id TEXT PRIMARY KEY, session_id TEXT, path TEXT NOT NULL,
            pool_eligible INTEGER NOT NULL, created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL, use_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS devices (
            id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL, revoked_at TEXT
        );
        """)
        db.commit()
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
            opened.verify()
        with Image.open(io.BytesIO(payload)) as opened:
            return ImageOps.exif_transpose(opened).convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("invalid image") from exc

def save_portrait(img: Image.Image, portrait_id: str) -> Path:
    dest = storage_root() / "portraits" / f"{portrait_id}.jpg"
    img.thumbnail((1600, 1600))
    img.save(dest, "JPEG", quality=88)
    return dest

def select_match(db: sqlite3.Connection, own_id: str) -> tuple[str, Path] | None:
    candidates = db.execute("SELECT id,path FROM portraits WHERE pool_eligible=1 AND expires_at>? AND id!=? ORDER BY use_count ASC, RANDOM() LIMIT 1", (now_iso(), own_id)).fetchone()
    if candidates:
        return candidates["id"], Path(candidates["path"])
    seeds = [p for p in (storage_root() / "seed_portraits").iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]
    if seeds:
        return "seed:" + seeds[0].name, seeds[0]
    return None

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

def cleanup_expired(db: sqlite3.Connection) -> int:
    # Do not remove records for active generations; production cleanup must handle queued jobs explicitly.
    expired = db.execute("SELECT id,path FROM portraits WHERE expires_at<=?", (now_iso(),)).fetchall()
    for record in expired:
        p = Path(record["path"])
        if p.parent == (storage_root() / "portraits"):
            p.unlink(missing_ok=True)
        db.execute("DELETE FROM portraits WHERE id=?", (record["id"],))
    db.commit()
    return len(expired)
