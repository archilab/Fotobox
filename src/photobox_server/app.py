from __future__ import annotations
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from PIL import Image
from .auth import authenticate
from .config import get_provider, load_env_file
from .core import db_connect, now_iso, save_portrait, validate_image, select_match, render_mock_strip, storage_root

PUBLIC_PATHS = frozenset({"/api/v1/health"})

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    load_env_file()
    get_provider()
    with db_connect():
        pass
    yield

app = FastAPI(title="Connected Photobox API", version="0.5.0", description="DEVELOPMENT ONLY: device tokens, no TLS. Never expose publicly.", lifespan=lifespan)

@app.middleware("http")
async def require_device_token(request: Request, call_next):
    # Deny by default. This runs in middleware because FastAPI parses multipart bodies
    # before route dependencies, which would let unauthenticated clients spool uploads.
    if request.url.path not in PUBLIC_PATHS:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        device_id = None
        if scheme.lower() == "bearer" and token:
            with db_connect() as db:
                device_id = authenticate(db, token.strip())
        if device_id is None:
            return JSONResponse({"detail": "valid device token required"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
        request.state.device_id = device_id
    return await call_next(request)

class ConsentRequest(BaseModel):
    processing: bool
    pool: bool = False
    documented_externally: bool = False

@app.get("/api/v1/health")
def health():
    return {"status": "ok", "provider": get_provider(), "production_ready": False}

@app.post("/api/v1/sessions", status_code=201)
def create_session(request: Request):
    session_id = str(uuid.uuid4())
    with db_connect() as db:
        db.execute("INSERT INTO sessions(id,box_id,status,created_at) VALUES(?,?,?,?)", (session_id, request.state.device_id, "awaiting_consent", now_iso()))
    return {"id": session_id, "status": "awaiting_consent"}

def get_session(db, session_id, device_id):
    # Another device's session looks the same as an unknown one, so ids cannot be probed.
    record = db.execute("SELECT * FROM sessions WHERE id=? AND box_id=?", (session_id, device_id)).fetchone()
    if record is None:
        raise HTTPException(404, "unknown session")
    return record

@app.post("/api/v1/sessions/{session_id}/consent")
def record_consent(session_id: str, body: ConsentRequest, request: Request):
    with db_connect() as db:
        s = get_session(db, session_id, request.state.device_id)
        if s["status"] != "awaiting_consent":
            raise HTTPException(409, "consent cannot be changed in current state")
        if not body.processing or not body.documented_externally:
            raise HTTPException(403, "documented external test subject authorization required")
        db.execute("UPDATE sessions SET consent_processing=1, consent_pool=?,status='awaiting_portrait' WHERE id=?", (int(body.pool),session_id))
    return {"status": "awaiting_portrait", "pool_consent": body.pool}

@app.post("/api/v1/sessions/{session_id}/portrait")
async def upload_portrait(session_id: str, request: Request, image: UploadFile = File(...)):
    limit = int(os.getenv("PHOTOBOX_MAX_UPLOAD_BYTES", "10000000"))
    raw = await image.read(limit+1)
    try:
        img = validate_image(raw)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    with db_connect() as db:
        s = get_session(db, session_id, request.state.device_id)
        if s["status"] != "awaiting_portrait":
            raise HTTPException(409, "portrait not accepted in current state")
        photo_id = str(uuid.uuid4())
        path = save_portrait(img, photo_id)
        hours = int(os.getenv("PHOTOBOX_POOL_HOURS", "24"))
        if not 1 <= hours <= 720:
            raise HTTPException(500, "invalid pool retention configuration")
        expires = (datetime.now(timezone.utc)+timedelta(hours=hours if s["consent_pool"] else 1)).isoformat()
        db.execute("INSERT INTO portraits(id,session_id,path,pool_eligible,created_at,expires_at) VALUES(?,?,?,?,?,?)", (photo_id, session_id,str(path),s["consent_pool"],now_iso(),expires))
        db.execute("UPDATE sessions SET portrait_id=?,status='uploaded' WHERE id=?", (photo_id,session_id))
    return {"portrait_id": photo_id, "status": "uploaded"}

@app.post("/api/v1/sessions/{session_id}/process")
def process_session(session_id: str, request: Request):
    # Synchronous development mock ONLY; AI milestone replaces this with a durable queued worker.
    with db_connect() as db:
        s = get_session(db,session_id,request.state.device_id)
        if s["status"] != "uploaded":
            raise HTTPException(409, "session must be uploaded")
        own = db.execute("SELECT * FROM portraits WHERE id=?", (s["portrait_id"],)).fetchone()
        matched = select_match(db,s["portrait_id"])
        if matched is None:
            raise HTTPException(409, "no eligible match: add a rights-cleared seed portrait")
        match_id, match_path = matched
        db.execute("UPDATE sessions SET status='processing',match_portrait_id=? WHERE id=?",(match_id,session_id))
    try:
        with Image.open(own["path"]) as a, Image.open(match_path) as b:
            frame_count = int(os.getenv("PHOTOBOX_NUM_FRAMES","4"))
            result = render_mock_strip(a,b,frame_count)
            dest = storage_root()/"generated"/f"{session_id}.png"
            result.save(dest,"PNG")
        with db_connect() as db:
            if not match_id.startswith("seed:"):
                db.execute("UPDATE portraits SET use_count=use_count+1 WHERE id=?",(match_id,))
            db.execute("UPDATE sessions SET status='complete',output_path=?,finished_at=? WHERE id=?",(str(dest),now_iso(),session_id))
    except Exception:
        with db_connect() as db:
            db.execute("UPDATE sessions SET status='failed',error='render failed' WHERE id=?",(session_id,))
        raise HTTPException(500, "render failed")
    return {"status":"complete","mode":"mock","result_url":f"/api/v1/sessions/{session_id}/result"}

@app.get("/api/v1/sessions/{session_id}")
def session_status(session_id: str, request: Request):
    with db_connect() as db:
        s = get_session(db,session_id,request.state.device_id)
        return {"id":s["id"],"box_id":s["box_id"],"status":s["status"],"error":s["error"],"has_result":bool(s["output_path"])}

@app.get("/api/v1/sessions/{session_id}/result")
def get_result(session_id: str, request: Request):
    with db_connect() as db:
        s = get_session(db, session_id, request.state.device_id)
        if s["status"] != "complete" or not s["output_path"]:
            raise HTTPException(404,"no completed result")
        path = Path(s["output_path"])
        if not path.is_file() or path.parent != (storage_root()/"generated"):
            raise HTTPException(404,"result not found")
        return FileResponse(path,media_type="image/png",filename="photostrip.png")
