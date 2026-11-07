from __future__ import annotations
import asyncio
import hashlib
import logging
import os
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from PIL import Image
from . import audit, throttle
from .auth import authenticate
from .config import get_cleanup_interval, get_provider, load_env_file
from .core import cleanup_expired, db_connect, now_iso, save_portrait, strip_images_active, validate_image, render_mock_strip, storage_root, write_private_image
from .pool import record_use, select_match
from .sessions import AWAITING_CONSENT, AWAITING_PORTRAIT, COMPLETE, FAILED, PROCESSING, UPLOADED, recover_interrupted, transition
from .withdrawal import new_code

PUBLIC_PATHS = frozenset({"/api/v1/health"})
CONSENT_POLICY_VERSION = "dev-attestation-1"
SESSION_PORTRAIT_HOURS = 1
IDEMPOTENCY_KEY_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}")
# Room for the multipart boundary and headers around the image itself.
MULTIPART_OVERHEAD_BYTES = 64 * 1024
logger = logging.getLogger(__name__)


def upload_limit() -> int:
    return int(os.getenv("PHOTOBOX_MAX_UPLOAD_BYTES", "10000000"))


def run_cleanup() -> None:
    with db_connect() as db:
        cleanup_expired(db)


async def cleanup_loop(interval: int) -> None:
    while True:
        try:
            await asyncio.to_thread(run_cleanup)
        except Exception as exc:
            # Keep retrying: stopping would silently leave expired images on disk.
            logger.error("automatic cleanup failed: %s", type(exc).__name__)
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    load_env_file()
    get_provider()
    interval = get_cleanup_interval()
    with db_connect() as db:
        interrupted = recover_interrupted(db)
    if interrupted:
        logger.warning("sessions interrupted by the last shutdown: %d", interrupted)
    cleanup_task = asyncio.create_task(cleanup_loop(interval))
    yield
    cleanup_task.cancel()
    with suppress(asyncio.CancelledError):
        await cleanup_task


app = FastAPI(title="Connected Photobox API", version="0.5.0", description="DEVELOPMENT ONLY: device tokens, no TLS. Never expose publicly.", lifespan=lifespan)


@app.middleware("http")
async def require_device_token(request: Request, call_next):
    # Deny by default. This runs in middleware because FastAPI parses multipart bodies
    # before route dependencies, which would let unauthenticated clients spool uploads.
    # The upload checks are here for the same reason: they must run before the body is read.
    if request.url.path not in PUBLIC_PATHS:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        device_id = None
        if scheme.lower() == "bearer" and token:
            with db_connect() as db:
                device_id = authenticate(db, token.strip())
        if device_id is None:
            return JSONResponse({"detail": "valid device token required"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
        request.state.device_id = device_id
        if request.method == "POST" and request.url.path.endswith("/portrait"):
            length = request.headers.get("content-length", "")
            if length.isdigit() and int(length) > upload_limit() + MULTIPART_OVERHEAD_BYTES:
                return JSONResponse({"detail": "image exceeds upload limit"}, status_code=413)
            wait = throttle.retry_after(device_id)
            if wait is not None:
                return JSONResponse({"detail": "too many uploads from this device"}, status_code=429, headers={"Retry-After": str(wait)})
    return await call_next(request)


class ConsentRequest(BaseModel):
    processing: bool
    pool: bool = False
    documented_externally: bool = False
    # A reference to the outside consent document, such as a form number. Never personal data.
    evidence_ref: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")


def idempotency_key(request: Request) -> str | None:
    key = request.headers.get("idempotency-key")
    if key is not None and not IDEMPOTENCY_KEY_PATTERN.fullmatch(key):
        raise HTTPException(400, "Idempotency-Key must be 1-128 letters, digits, '-' or '_'")
    return key


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "provider": get_provider(), "production_ready": False}


@app.post("/api/v1/sessions", status_code=201)
def create_session(request: Request, response: Response):
    key = idempotency_key(request)
    device_id = request.state.device_id
    session_id = str(uuid.uuid4())
    with db_connect() as db:
        cursor = db.execute(
            "INSERT OR IGNORE INTO sessions(id,box_id,status,created_at,create_key) VALUES(?,?,?,?,?)",
            (session_id, device_id, AWAITING_CONSENT, now_iso(), key),
        )
        if cursor.rowcount == 0:
            existing = db.execute("SELECT id,status FROM sessions WHERE box_id=? AND create_key=?", (device_id, key)).fetchone()
            response.status_code = 200
            return {"id": existing["id"], "status": existing["status"]}
    return {"id": session_id, "status": AWAITING_CONSENT}


def get_session(db, session_id, device_id):
    # Another device's session looks the same as an unknown one, so ids cannot be probed.
    record = db.execute("SELECT * FROM sessions WHERE id=? AND box_id=?", (session_id, device_id)).fetchone()
    if record is None:
        raise HTTPException(404, "unknown session")
    return record


def active_consent(db, session_id: str, purpose: str):
    return db.execute(
        "SELECT * FROM consent_records WHERE session_id=? AND purpose=? AND withdrawn_at IS NULL",
        (session_id, purpose),
    ).fetchone()


@app.post("/api/v1/sessions/{session_id}/consent")
def record_consent(session_id: str, body: ConsentRequest, request: Request):
    with db_connect() as db:
        s = get_session(db, session_id, request.state.device_id)
        if s["status"] == AWAITING_CONSENT:
            if not body.processing or not body.documented_externally:
                raise HTTPException(403, "documented external test subject authorization required")
            if transition(db, session_id, AWAITING_CONSENT, AWAITING_PORTRAIT):
                granted = now_iso()
                code, code_hash = new_code()
                db.execute("UPDATE sessions SET withdrawal_hash=? WHERE id=?", (code_hash, session_id))
                for purpose in ("processing", "pool") if body.pool else ("processing",):
                    db.execute(
                        "INSERT INTO consent_records(id,session_id,purpose,policy_version,granted_at,provenance,evidence_ref) VALUES(?,?,?,?,?,?,?)",
                        (str(uuid.uuid4()), session_id, purpose, CONSENT_POLICY_VERSION, granted, "operator_attestation", body.evidence_ref),
                    )
                    audit.record(db, "consent_granted", session_id=session_id, device_id=request.state.device_id, detail=purpose)
                return {"status": AWAITING_PORTRAIT, "pool_consent": body.pool, "withdrawal_code": code}
            s = get_session(db, session_id, request.state.device_id)
        # A retry of the same request succeeds; anything that would change consent does not.
        processing = active_consent(db, session_id, "processing")
        same = (
            processing is not None and body.processing and body.documented_externally
            and processing["evidence_ref"] == body.evidence_ref
            and (active_consent(db, session_id, "pool") is not None) == body.pool
        )
        if not same:
            raise HTTPException(409, "consent cannot be changed in current state")
        # The code is shown once: only its hash is stored, so a repeat cannot return it.
        return {"status": s["status"], "pool_consent": body.pool, "withdrawal_code": None}


def replay_upload(db, session_id: str, key: str | None, digest: str) -> dict:
    """Answer a repeated upload, or refuse if this is a different one."""
    if key is not None:
        existing = db.execute("SELECT id,upload_sha256 FROM portraits WHERE session_id=? AND upload_key=?", (session_id, key)).fetchone()
        if existing is not None:
            if existing["upload_sha256"] != digest:
                raise HTTPException(409, "Idempotency-Key was already used for a different image")
            return {"portrait_id": existing["id"], "status": UPLOADED}
    raise HTTPException(409, "portrait not accepted in current state")


# A plain def, so FastAPI runs image decoding and SQLite in a worker thread rather than blocking
# every other request. Concurrency between those threads is handled by transition().
@app.post("/api/v1/sessions/{session_id}/portrait")
def upload_portrait(session_id: str, request: Request, image: UploadFile = File(...)):
    hours = int(os.getenv("PHOTOBOX_POOL_HOURS", "24"))
    if not 1 <= hours <= 720:
        raise HTTPException(500, "invalid pool retention configuration")
    key = idempotency_key(request)
    device_id = request.state.device_id
    limit = upload_limit()
    raw = image.file.read(limit + 1)
    if len(raw) > limit:
        raise HTTPException(413, "image exceeds upload limit")
    digest = hashlib.sha256(raw).hexdigest()
    with db_connect() as db:
        if get_session(db, session_id, device_id)["status"] != AWAITING_PORTRAIT:
            return replay_upload(db, session_id, key, digest)
    try:
        img = validate_image(raw)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    saved: list[Path] = []
    try:
        with db_connect() as db:
            # Taking the state first holds the write lock, so the file and its record commit together.
            if not transition(db, session_id, AWAITING_PORTRAIT, UPLOADED):
                return replay_upload(db, session_id, key, digest)
            pool = active_consent(db, session_id, "pool") is not None
            photo_id = str(uuid.uuid4())
            saved.append(save_portrait(img, photo_id))
            now = datetime.now(timezone.utc)
            db.execute(
                "INSERT INTO portraits(id,session_id,path,pool_eligible,created_at,expires_at,upload_key,upload_sha256) VALUES(?,?,?,0,?,?,?,?)",
                (photo_id, session_id, str(saved[0]), now_iso(), (now+timedelta(hours=SESSION_PORTRAIT_HOURS)).isoformat(), key, digest),
            )
            if pool:
                # A separate copy with its own lifetime, so the two uses can expire independently.
                pool_id = str(uuid.uuid4())
                saved.append(save_portrait(img, pool_id, "pool_portraits"))
                db.execute(
                    "INSERT INTO portraits(id,session_id,path,pool_eligible,created_at,expires_at) VALUES(?,?,?,1,?,?)",
                    (pool_id, session_id, str(saved[1]), now_iso(), (now+timedelta(hours=hours)).isoformat()),
                )
            db.execute("UPDATE sessions SET portrait_id=? WHERE id=?", (photo_id, session_id))
    except Exception:
        # A portrait with no record could never be found again for deletion.
        for path in saved:
            path.unlink(missing_ok=True)
        raise
    return {"portrait_id": photo_id, "status": UPLOADED}


@app.post("/api/v1/sessions/{session_id}/process")
def process_session(session_id: str, request: Request):
    # Synchronous development mock ONLY; AI milestone replaces this with a durable queued worker.
    result_url = f"/api/v1/sessions/{session_id}/result"
    with db_connect() as db:
        s = get_session(db, session_id, request.state.device_id)
        if s["status"] == COMPLETE and s["output_path"]:
            return {"status": COMPLETE, "mode": "mock", "result_url": result_url}
        # Only one request can win this transition, so a session is never rendered twice at once.
        if s["status"] not in (UPLOADED, FAILED) or not transition(db, session_id, s["status"], PROCESSING):
            raise HTTPException(409, "session must be uploaded")
        own = db.execute("SELECT * FROM portraits WHERE id=?", (s["portrait_id"],)).fetchone()
        if own is None:
            raise HTTPException(409, "portrait has expired")
        matched = select_match(db, session_id)
        if matched is None:
            raise HTTPException(409, "no eligible match: add a rights-cleared seed portrait")
        match_id, match_path = matched
        db.execute("UPDATE sessions SET match_portrait_id=?, error=NULL WHERE id=?", (match_id, session_id))
    try:
        with Image.open(own["path"]) as a, Image.open(match_path) as b:
            frame_count = int(os.getenv("PHOTOBOX_NUM_FRAMES", "4"))
            result = render_mock_strip(a, b, frame_count)
            dest = storage_root()/"generated"/f"{session_id}.png"
            write_private_image(result, dest, "PNG")
        with db_connect() as db:
            completed = transition(db, session_id, PROCESSING, COMPLETE)
            if completed:
                record_use(db, match_id, session_id)
                db.execute("UPDATE sessions SET output_path=?,finished_at=? WHERE id=?", (str(dest), now_iso(), session_id))
    except Exception:
        with db_connect() as db:
            if transition(db, session_id, PROCESSING, FAILED):
                db.execute("UPDATE sessions SET error='render failed' WHERE id=?", (session_id,))
        raise HTTPException(500, "render failed")
    if not completed:
        # Consent was withdrawn during the render, so this strip must not survive.
        dest.unlink(missing_ok=True)
        raise HTTPException(409, "session was withdrawn")
    return {"status": COMPLETE, "mode": "mock", "result_url": result_url}


@app.get("/api/v1/sessions/{session_id}")
def session_status(session_id: str, request: Request):
    with db_connect() as db:
        s = get_session(db, session_id, request.state.device_id)
        return {"id": s["id"], "box_id": s["box_id"], "status": s["status"], "error": s["error"], "has_result": bool(s["output_path"])}


@app.get("/api/v1/sessions/{session_id}/result")
def get_result(session_id: str, request: Request):
    with db_connect() as db:
        s = get_session(db, session_id, request.state.device_id)
        if s["status"] != COMPLETE or not s["output_path"]:
            raise HTTPException(404, "no completed result")
        if not strip_images_active(db, s):
            raise HTTPException(404, "result expired")
        path = Path(s["output_path"])
        if not path.is_file() or path.parent != (storage_root()/"generated"):
            raise HTTPException(404, "result not found")
        return FileResponse(path, media_type="image/png", filename="photostrip.png")
