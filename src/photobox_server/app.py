from __future__ import annotations
import asyncio
import hashlib
import io
import logging
import os
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image
from pydantic import BaseModel, Field
from . import audit, live, message_windows, messaging, presence, throttle
from .presence import events as presence_events
from .auth import authenticate
from .consent_notice import NoticeError, load_notice, load_pool_notice, pool_hours
from .pairing import complete_pairing
from .config import ConfigError, get_cleanup_interval, get_frame_count, get_provider, load_env_file
from .core import cleanup_expired, db_connect, now_iso, save_portrait, storage_root, strip_images_active, validate_image
from .jobs import Worker, cancel_for_session, release_partner, cleanup_job_folders, enqueue, job_for_session, recover_after_restart, wake
from .pool import select_match
from .providers import create_provider
from .strip import apply_profile, validate_settings
from .sessions import AWAITING_CONSENT, AWAITING_PORTRAIT, COMPLETE, FAILED, PROCESSING, QUEUED, UPLOADED, transition
from .withdrawal import new_code

# Pairing must work before a box has a token; it is limited separately (see pair_device).
PUBLIC_PATHS = frozenset({"/api/v1/health", "/api/v1/pair"})
PAIR_ATTEMPTS_PER_MINUTE = 10
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
        cleanup_job_folders(db)
        presence_events.purge(db)


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
    provider = create_provider(get_provider())
    get_frame_count()
    validate_settings()
    presence.settings()
    live.max_wait_seconds()
    messaging.Limits.from_env()
    interval = get_cleanup_interval()
    with db_connect() as db:
        interrupted = recover_after_restart(db)
    if interrupted:
        logger.warning("jobs interrupted by the last shutdown: %d", interrupted)
    worker = Worker(provider)
    worker.start()
    cleanup_task = asyncio.create_task(cleanup_loop(interval))
    yield
    worker.stop()
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


DEVICE_STATES = ("idle", "consent", "capturing", "generating", "showing", "setup", "not ready")


class StatusReport(BaseModel):
    state: Literal["idle", "consent", "capturing", "generating", "showing", "setup", "not ready"]
    # Only operator-facing text such as "camera failed"; never anything about a participant.
    detail: str | None = Field(default=None, max_length=120)


class MessageRequest(BaseModel):
    message_id: str = Field(min_length=1, max_length=64)
    match_id: str = Field(min_length=1, max_length=128)
    # The limit that applies is in the relay; this only keeps the request small. The text is never logged.
    text: str = Field(max_length=messaging.RAW_TEXT_LIMIT)


class PairRequest(BaseModel):
    code: str = Field(min_length=1, max_length=12)


class ConsentRequest(BaseModel):
    processing: bool
    pool: bool = False
    # Being paired live with a visitor at another box, covered by the notice when live matching is on (ADR-024).
    live: bool = False
    documented_externally: bool = False
    # How consent was given: stated by the operator, or confirmed by the participant on the box's screen.
    method: Literal["operator_attestation", "participant_screen"] = "operator_attestation"
    # For participant_screen: the version of the notice that was shown.
    notice_version: str | None = Field(default=None, max_length=40)
    # For participant_screen with pool: the pool notice's version and the end time it showed (ADR-024).
    pool_notice_version: str | None = Field(default=None, max_length=40)
    pool_until: datetime | None = None
    # A reference to the outside consent document, such as a form number. Never personal data.
    evidence_ref: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")


def idempotency_key(request: Request) -> str | None:
    key = request.headers.get("idempotency-key")
    if key is not None and not IDEMPOTENCY_KEY_PATTERN.fullmatch(key):
        raise HTTPException(400, "Idempotency-Key must be 1-128 letters, digits, '-' or '_'")
    return key


@app.post("/api/v1/devices/status", status_code=204)
def report_status(body: StatusReport, request: Request):
    """A box reports what it is doing, for the dashboard. Boxes send it on every change and every 30 s."""
    with db_connect() as db:
        db.execute(
            "INSERT INTO device_status(device_id,state,detail,reported_at) VALUES(?,?,?,?) "
            "ON CONFLICT(device_id) DO UPDATE SET state=excluded.state, detail=excluded.detail, reported_at=excluded.reported_at",
            (request.state.device_id, body.state, body.detail, now_iso()),
        )
    return Response(status_code=204)


@app.post("/api/v1/pair")
def pair_device(body: PairRequest, request: Request):
    """Exchange a pairing code shown by `admin device pair` for this box's token."""
    client = request.client.host if request.client else "unknown"
    if throttle.retry_after("pair:" + client, limit=PAIR_ATTEMPTS_PER_MINUTE) is not None:
        raise HTTPException(429, "too many pairing attempts")
    with db_connect() as db:
        paired = complete_pairing(db, body.code.strip())
    if paired is None:
        raise HTTPException(403, "invalid or expired pairing code")
    device_id, token = paired
    return {"device_id": device_id, "token": token}


@app.get("/api/v1/consent-notice")
def consent_notice():
    """The text a box shows before taking a photo, and its version to send back with the consent.

    `pool` is the separate second question, or null when pool use is not offered (ADR-024).
    """
    try:
        version, text = load_notice()
    except NoticeError as exc:
        raise HTTPException(503, str(exc)) from exc
    pool = load_pool_notice()
    # live_matching: the notice above covers being paired live with a visitor at another box (ADR-024).
    return {"version": version, "text": text, "live_matching": live.enabled(), "pool": None if pool is None else {
        "version": pool.version, "text": pool.text, "until": pool.until.isoformat(), "until_text": pool.until_text}}


@app.get("/api/v1/presence/snapshot")
def presence_snapshot(request: Request):
    """What is happening at the other boxes, and the one message this box should show (ADR-023)."""
    with db_connect() as db:
        return presence.snapshot(db, request.state.device_id)


def require_messaging() -> None:
    if not message_windows.enabled():
        raise HTTPException(404, "messaging is not switched on")


@app.get("/api/v1/messaging/events")
def messaging_events(request: Request):
    """Events waiting for this box, once each: windows opening and closing, and text from its live partner."""
    require_messaging()
    with db_connect() as db:
        message_windows.sync(db, request.state.device_id)
    return {"events": message_windows.relay().drain(request.state.device_id)}


@app.post("/api/v1/sessions/{session_id}/messages")
def send_message(session_id: str, body: MessageRequest, request: Request):
    """One text message to this session's live partner. The server decides who receives it (ADR-027)."""
    require_messaging()
    with db_connect() as db:
        session = get_session(db, session_id, request.state.device_id)
        return message_windows.send(db, session, body.match_id, body.message_id, body.text)


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


def screen_pool_consent(body: ConsentRequest) -> tuple[str, str]:
    """Check a pool consent given on the box's screen. Returns its policy version and end time.

    The visitor answered a separate question that named an end time; the photo may stay in the
    pool until that time and no longer (ADR-024).
    """
    pool = load_pool_notice()
    if pool is None:
        raise HTTPException(403, "pool use is not offered on this server")
    if body.pool_notice_version != pool.version:
        raise HTTPException(409, "the pool notice has changed; show the current one")
    if body.pool_until is None or body.pool_until.tzinfo is None:
        raise HTTPException(422, "pool_until with a time zone is required")
    now = datetime.now(timezone.utc)
    if not now < body.pool_until <= now + timedelta(hours=pool_hours(), minutes=1):
        raise HTTPException(422, "pool_until is outside the allowed time")
    return pool.version, body.pool_until.astimezone(timezone.utc).isoformat()


@app.post("/api/v1/sessions/{session_id}/consent")
def record_consent(session_id: str, body: ConsentRequest, request: Request):
    if body.live and not live.enabled():
        raise HTTPException(403, "live matching is not switched on")
    with db_connect() as db:
        s = get_session(db, session_id, request.state.device_id)
        if body.method == "participant_screen":
            try:
                current_version, _ = load_notice()
            except NoticeError as exc:
                raise HTTPException(503, str(exc)) from exc
            policy_version = current_version
            pool_policy, pool_expiry = screen_pool_consent(body) if body.pool else (None, None)
        else:
            policy_version = CONSENT_POLICY_VERSION
            pool_policy, pool_expiry = CONSENT_POLICY_VERSION, None
        if s["status"] == AWAITING_CONSENT:
            if body.method == "participant_screen":
                if not body.processing:
                    raise HTTPException(403, "consent to processing is required")
                if body.notice_version != current_version:
                    raise HTTPException(409, "the consent notice has changed; show the current one")
            elif not body.processing or not body.documented_externally:
                raise HTTPException(403, "documented external test subject authorization required")
            if transition(db, session_id, AWAITING_CONSENT, AWAITING_PORTRAIT):
                granted = now_iso()
                code, code_hash = new_code()
                db.execute("UPDATE sessions SET withdrawal_hash=? WHERE id=?", (code_hash, session_id))
                purposes = [("processing", policy_version, None)] + ([("pool", pool_policy, pool_expiry)] if body.pool else [])
                purposes += [("live", policy_version, None)] if body.live else []
                for purpose, version, expiry in purposes:
                    db.execute(
                        "INSERT INTO consent_records(id,session_id,purpose,policy_version,granted_at,provenance,evidence_ref,expires_at) VALUES(?,?,?,?,?,?,?,?)",
                        (str(uuid.uuid4()), session_id, purpose, version, granted, body.method, body.evidence_ref, expiry),
                    )
                    audit.record(db, "consent_granted", session_id=session_id, device_id=request.state.device_id, detail=purpose)
                # Only now, with consent recorded, may other boxes learn that someone is here (ADR-023).
                presence_events.emit(db, "session.started", request.state.device_id, session_id=session_id)
                if body.live:
                    live.extend_waiting(db, request.state.device_id)
                return {"status": AWAITING_PORTRAIT, "pool_consent": body.pool, "withdrawal_code": code}
            s = get_session(db, session_id, request.state.device_id)
        # A retry of the same request succeeds; anything that would change consent does not.
        processing = active_consent(db, session_id, "processing")
        same = (
            processing is not None and body.processing
            and processing["provenance"] == body.method and processing["policy_version"] == policy_version
            and (body.documented_externally or body.method == "participant_screen")
            and processing["evidence_ref"] == body.evidence_ref
            and (active_consent(db, session_id, "pool") is not None) == body.pool
            and (active_consent(db, session_id, "live") is not None) == body.live
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
    try:
        hours = pool_hours()
    except ConfigError as exc:
        raise HTTPException(500, "invalid pool retention configuration") from exc
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
            pool_consent = active_consent(db, session_id, "pool")
            pool = pool_consent is not None
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
                    (pool_id, session_id, str(saved[1]), now_iso(), pool_expiry(now, hours, pool_consent["expires_at"])),
                )
            db.execute("UPDATE sessions SET portrait_id=? WHERE id=?", (photo_id, session_id))
            presence_events.emit(db, "portrait.captured", device_id, session_id=session_id)
    except Exception:
        # A portrait with no record could never be found again for deletion.
        for path in saved:
            path.unlink(missing_ok=True)
        raise
    return {"portrait_id": photo_id, "status": UPLOADED}


def pool_expiry(now: datetime, hours: int, shown: str | None) -> str:
    """The configured time, but never later than the end time the visitor was shown."""
    expiry = now + timedelta(hours=hours)
    if shown is not None:
        expiry = min(expiry, datetime.fromisoformat(shown))
    return expiry.isoformat()


def accepted(session_id: str, status: str, job_id: str) -> dict:
    return {"status": status, "job_id": job_id, "mode": get_provider(), "status_url": f"/api/v1/sessions/{session_id}", "result_url": f"/api/v1/sessions/{session_id}/result"}


@app.post("/api/v1/sessions/{session_id}/process", status_code=202)
def process_session(session_id: str, request: Request, response: Response):
    """Queue generation, whichever provider is configured. Poll the session for progress.

    With live matching on, a visitor waiting at another box is paired with this one first (ADR-024).
    """
    with db_connect() as db:
        # The write lock from the start, so two boxes arriving together cannot both miss each other.
        db.execute("BEGIN IMMEDIATE")
        s = get_session(db, session_id, request.state.device_id)
        status = s["status"]
        if status in (UPLOADED, FAILED):
            own = db.execute("SELECT 1 FROM portraits WHERE id=?", (s["portrait_id"],)).fetchone()
            if own is None:
                raise HTTPException(409, "portrait has expired")
            partner = live.find_partner(db, s)
            if partner is not None:
                job_id = live.pair(db, partner, s)
                if job_id is not None:
                    wake()
                    return accepted(session_id, QUEUED, job_id)
            if select_match(db, session_id) is None:
                raise HTTPException(409, "no eligible match: add a rights-cleared seed portrait")
            # Only one request can win this, so a session is never queued twice.
            job_id = enqueue(db, session_id, status, live.wait_for(db, s))
            if job_id is not None:
                wake()
                return accepted(session_id, QUEUED, job_id)
            status = get_session(db, session_id, request.state.device_id)["status"]
        job = job_for_leader(db, s)
        if status in (QUEUED, PROCESSING) and job is not None:
            return accepted(session_id, status, job["id"])
        if status == COMPLETE and s["output_path"] and job is not None:
            response.status_code = 200
            return accepted(session_id, COMPLETE, job["id"])
        raise HTTPException(409, "session must be uploaded")


@app.post("/api/v1/sessions/{session_id}/cancel")
def cancel_session(session_id: str, request: Request):
    """Stop waiting or running generation. The portrait stays, so the session can be processed again.

    Cancelling either side of a live pair breaks the pair: the other visitor's strip is then made
    with a pool portrait or seed instead, or, if this was the leader, the follower is queued on its own.
    """
    with db_connect() as db:
        s = get_session(db, session_id, request.state.device_id)
        if s["status"] in (QUEUED, PROCESSING) and transition(db, session_id, s["status"], UPLOADED):
            cancel_for_session(db, session_id, "requested by device")
            release_partner(db, s)
            return {"status": UPLOADED, "cancelled": True}
        current = get_session(db, session_id, request.state.device_id)["status"]
        if current == UPLOADED:
            return {"status": UPLOADED, "cancelled": False}
        raise HTTPException(409, "nothing to cancel in current state")


def job_for_leader(db, s):
    """A follower's progress is its leader's job: they share one strip."""
    return job_for_session(db, live.leader_of(db, s) or s["id"])


def match_info(db, s) -> dict:
    kind = live.match_kind(s)
    partner_label = None
    if kind == "live":
        row = db.execute(
            "SELECT d.public_label FROM sessions p JOIN devices d ON d.id = p.box_id WHERE p.id=?", (s["live_partner"],)
        ).fetchone()
        partner_label = presence.messages.clean(row["public_label"]) if row and row["public_label"] else None
    return {"kind": kind, "partner_label": partner_label}


@app.get("/api/v1/sessions/{session_id}")
def session_status(session_id: str, request: Request):
    with db_connect() as db:
        s = get_session(db, session_id, request.state.device_id)
        job = job_for_leader(db, s)
        status = s["status"]
        if s["live_role"] == live.FOLLOW and status == QUEUED and job is not None and job["status"] == "running":
            status = PROCESSING
        # Still waiting for a visitor at another box (ADR-024). The time left is the server's real deadline.
        waiting = job is not None and bool(job["live_wait"]) and job["status"] == "queued" and job["next_attempt_at"] > now_iso() and s["live_partner"] is None
        visitor = live.coming(db, s["box_id"]) if waiting else None
        progress = None if job is None else {
            "status": job["status"], "frames_done": job["frames_done"], "frames_total": job["frames_total"],
            "attempts": job["attempts"], "provider": job["provider"], "waiting_for_partner": waiting,
            "wait_seconds_left": max(0, int((datetime.fromisoformat(job["next_attempt_at"]) - datetime.now(timezone.utc)).total_seconds()) + 1) if waiting else None,
            "partner_coming": visitor is not None,
            "partner_coming_label": presence.messages.clean(visitor["public_label"]) if visitor is not None and visitor["public_label"] else None,
        }
        return {"id": s["id"], "box_id": s["box_id"], "status": status, "error": s["error"], "has_result": bool(s["output_path"]),
                "job": progress, "match": match_info(db, s)}


@app.get("/api/v1/sessions/{session_id}/result")
def get_result(session_id: str, request: Request, profile: Literal["color", "grayscale", "thermal"] = "color"):
    with db_connect() as db:
        s = get_session(db, session_id, request.state.device_id)
        if s["status"] != COMPLETE or not s["output_path"]:
            raise HTTPException(404, "no completed result")
        if not strip_images_active(db, s):
            raise HTTPException(404, "result expired")
        path = Path(s["output_path"])
        if not path.is_file() or path.parent != (storage_root()/"generated"):
            raise HTTPException(404, "result not found")
    if profile == "color":
        return FileResponse(path, media_type="image/png", filename="photostrip.png")
    # Made on request rather than stored, so there are no further copies to expire or withdraw.
    with Image.open(path) as strip:
        output = io.BytesIO()
        apply_profile(strip, profile).save(output, "PNG")
    return Response(output.getvalue(), media_type="image/png", headers={"Content-Disposition": f'attachment; filename="photostrip-{profile}.png"'})
