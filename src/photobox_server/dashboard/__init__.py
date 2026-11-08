"""Operator dashboard, reachable only from the Mac itself (ADR-022).

    python -m uvicorn photobox_server.dashboard:app --host 127.0.0.1 --port 8001

Shows the boxes, the queue and the stored strips, and edits the consent notice, the stories and prompts,
seed images and box pairing. It shares the database with the photo booth server but never runs jobs.
"""
from __future__ import annotations
import io
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image
from .. import audit
from ..config import ConfigError, get_provider, load_env_file
from ..consent_notice import NoticeError, file_notice, load_notice
from ..core import db_connect, now_iso, storage_root, strip_images_active
from ..jobs import ACTIVE_JOB_STATES, cancel_for_session
from ..library import (ContentError, activate_prompt_version, active_notice, active_prompt_version, notice_versions,
                       prompt_content, prompt_versions, save_notice, save_prompt_content)
from ..pairing import VALID_MINUTES, start_pairing
from ..pool import import_seed, remove_seed
from ..prompts import content_version, default_content, prompts_for
from ..sessions import PROCESSING, QUEUED, UPLOADED, transition
from ..strip import PROFILES, apply_profile
from ..withdrawal import withdraw_session
from .security import COOKIE, LOCAL_CLIENTS, LOCAL_HOSTS, SESSION_SECONDS, Sessions, password_is_set, password_matches

ONLINE_SECONDS = 90
TEMPLATES = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=select_autoescape(["html"]))
app = FastAPI(title="Photo booth dashboard", docs_url=None, redoc_url=None, openapi_url=None)
app.state.sessions = Sessions()
load_env_file()

PUBLIC = frozenset({"/login"})
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; form-action 'self'; frame-ancestors 'none'",
}


@app.middleware("http")
async def guard(request: Request, call_next):
    client = request.client.host if request.client else ""
    host_header = request.headers.get("host", "")
    host = host_header.split("]")[0] + "]" if host_header.startswith("[") else host_header.split(":")[0]
    # Only the Mac itself, under its own name: refuses the LAN even if bound wrongly, and DNS rebinding.
    if client not in LOCAL_CLIENTS or host not in LOCAL_HOSTS:
        return Response("The dashboard is only available on this Mac.", status_code=403)
    session = app.state.sessions.get(request.cookies.get(COOKIE))
    request.state.session = session
    if session is None and request.url.path not in PUBLIC:
        return RedirectResponse("/login", status_code=303)
    if request.method == "POST" and request.url.path not in PUBLIC:
        form = await request.form()
        request.state.form = form
        if form.get("csrf") != session.csrf:
            return Response("This form has expired. Go back and reload the page.", status_code=403)
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


def page(request: Request, template: str, status_code: int = 200, **context) -> HTMLResponse:
    session = getattr(request.state, "session", None)
    html = TEMPLATES.get_template(template).render(request=request, csrf=session.csrf if session else "", path=request.url.path, **context)
    return HTMLResponse(html, status_code=status_code)


def back(location: str, message: str | None = None) -> RedirectResponse:
    from urllib.parse import quote
    return RedirectResponse(location + (f"?message={quote(message)}" if message else ""), status_code=303)


def form(request: Request):
    return request.state.form


# Login

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    with db_connect() as db:
        ready = password_is_set(db)
    return page(request, "login.html", ready=ready, wait=app.state.sessions.wait_seconds())


@app.post("/login")
async def login(request: Request):
    sessions: Sessions = app.state.sessions
    data = await request.form()
    with db_connect() as db:
        ready = password_is_set(db)
        if sessions.wait_seconds() or not ready or not password_matches(db, str(data.get("password", ""))):
            if ready and not sessions.wait_seconds():
                sessions.record_failure()
            return page(request, "login.html", 401, ready=ready, wait=sessions.wait_seconds(), failed=True)
        audit.record(db, "dashboard_login")
    sessions.record_success()
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(COOKIE, sessions.create(), max_age=SESSION_SECONDS, httponly=True, samesite="strict", path="/")
    return response


@app.post("/logout")
def logout(request: Request):
    app.state.sessions.end(request.cookies.get(COOKIE))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE, path="/")
    return response


# Overview and queue

def device_rows(db) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=ONLINE_SECONDS)).isoformat()
    rows = db.execute(
        """SELECT d.id, d.created_at, d.revoked_at, d.public_label, s.state, s.detail, s.reported_at FROM devices d
           LEFT JOIN device_status s ON s.device_id = d.id ORDER BY d.id"""
    ).fetchall()
    return [dict(row) | {"online": bool(row["reported_at"] and row["reported_at"] > cutoff and not row["revoked_at"])} for row in rows]


@app.get("/", response_class=HTMLResponse)
def overview(request: Request):
    with db_connect() as db:
        jobs = dict(db.execute("SELECT status, COUNT(*) FROM generation_jobs GROUP BY status").fetchall())
        strips = db.execute("SELECT COUNT(*) FROM sessions WHERE output_path IS NOT NULL").fetchone()[0]
        seeds = db.execute("SELECT COUNT(*) FROM seeds WHERE removed_at IS NULL").fetchone()[0]
        devices = device_rows(db)
        prompts = active_prompt_version(db)
    try:
        provider = get_provider()
    except ConfigError as exc:
        provider = f"invalid: {exc}"
    try:
        notice = load_notice()[0]
    except NoticeError as exc:
        notice = f"not ready: {exc}"
    disk = shutil.disk_usage(storage_root())
    return page(request, "overview.html", devices=devices, jobs=jobs, strips=strips, seeds=seeds, provider=provider,
                notice=notice, prompts=prompts, free_gb=disk.free / 1e9)


@app.get("/queue", response_class=HTMLResponse)
def queue(request: Request):
    with db_connect() as db:
        jobs = db.execute(
            """SELECT j.*, s.status AS session_status, s.box_id FROM generation_jobs j JOIN sessions s ON s.id = j.session_id
               ORDER BY j.created_at DESC LIMIT 50"""
        ).fetchall()
    return page(request, "queue.html", jobs=jobs, active=ACTIVE_JOB_STATES, message=request.query_params.get("message"))


@app.post("/queue/{session_id}/cancel")
def cancel(session_id: str, request: Request):
    with db_connect() as db:
        row = db.execute("SELECT status FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row and row["status"] in (QUEUED, PROCESSING) and transition(db, session_id, row["status"], UPLOADED):
            cancel_for_session(db, session_id, "cancelled in the dashboard")
            return back("/queue", "Cancelled. The portrait stays, so the box can request it again.")
    return back("/queue", "Nothing to cancel.")


# Strips

@app.get("/strips", response_class=HTMLResponse)
def strips(request: Request):
    with db_connect() as db:
        rows = db.execute(
            """SELECT s.id, s.box_id, s.finished_at, s.match_portrait_id,
                      (SELECT MIN(expires_at) FROM portraits p WHERE p.id IN (s.portrait_id, s.match_portrait_id)) AS expires_at
               FROM sessions s WHERE s.output_path IS NOT NULL AND s.status = 'complete' ORDER BY s.finished_at DESC"""
        ).fetchall()
    now = datetime.now(timezone.utc)
    items = []
    for row in rows:
        minutes = None
        if row["expires_at"]:
            minutes = max(0, int((datetime.fromisoformat(row["expires_at"]) - now).total_seconds() // 60))
        items.append(dict(row) | {"minutes_left": minutes})
    return page(request, "strips.html", strips=items, profiles=PROFILES, message=request.query_params.get("message"))


@app.get("/strips/{session_id}.png")
def strip_image(session_id: str, profile: str = "color"):
    if profile not in PROFILES:
        return Response(status_code=404)
    with db_connect() as db:
        row = db.execute("SELECT * FROM sessions WHERE id=? AND output_path IS NOT NULL", (session_id,)).fetchone()
        if row is None or not strip_images_active(db, row):
            return Response(status_code=404)
    path = Path(row["output_path"])
    if path.parent != storage_root() / "generated" or not path.is_file():
        return Response(status_code=404)
    with Image.open(path) as strip:
        out = io.BytesIO()
        apply_profile(strip, profile).save(out, "PNG")
    return Response(out.getvalue(), media_type="image/png")


@app.post("/strips/{session_id}/withdraw")
def withdraw(session_id: str, request: Request):
    if form(request).get("confirm") != "yes":
        return back("/strips", "Tick the box to confirm the withdrawal.")
    with db_connect() as db:
        result = withdraw_session(db, session_id)
    if result is None:
        return back("/strips", "Unknown session.")
    return back("/strips", f"Withdrawn: {result.portraits} portraits and {result.strips} strips deleted.")


# Consent notice

@app.get("/consent", response_class=HTMLResponse)
def consent(request: Request):
    with db_connect() as db:
        active = active_notice(db)
        versions = notice_versions(db)
    source, text, version = "dashboard", None, None
    if active:
        version, text = active
    else:
        source = "file"
        try:
            text = file_notice()
        except NoticeError:
            text, source = "", "none"
    return page(request, "consent.html", text=text, version=version, source=source, versions=versions,
                message=request.query_params.get("message"), error=None)


@app.post("/consent", response_class=HTMLResponse)
def save_consent(request: Request):
    text = str(form(request).get("text", ""))
    try:
        with db_connect() as db:
            version = save_notice(db, text)
    except ContentError as exc:
        with db_connect() as db:
            versions = notice_versions(db)
        return page(request, "consent.html", 422, text=text, version=None, source="draft", versions=versions, message=None, error=str(exc))
    return back("/consent", f"Saved and active: {version}. Boxes show it from their next visitor on.")


@app.get("/consent/{version}", response_class=HTMLResponse)
def consent_version(version: str, request: Request):
    with db_connect() as db:
        row = db.execute("SELECT * FROM consent_notices WHERE version=?", (version,)).fetchone()
    if row is None:
        return HTMLResponse("Unknown version", status_code=404)
    return page(request, "consent_version.html", notice=row)


@app.post("/consent/{version}/activate")
def consent_activate(version: str, request: Request):
    with db_connect() as db:
        row = db.execute("SELECT text FROM consent_notices WHERE version=?", (version,)).fetchone()
        if row is None:
            return back("/consent", "Unknown version.")
        save_notice(db, row["text"])
    return back("/consent", f"Active again: {version}.")


# Stories and prompts

def save_content(request: Request, new_content: dict, location: str, template: str, **context):
    try:
        with db_connect() as db:
            version = save_prompt_content(db, new_content)
    except ContentError as exc:
        return page(request, template, 422, error=str(exc), **context)
    return back(location, f"Saved and active: {version}. New strips use it; strips already queued keep their version.")


@app.get("/stories", response_class=HTMLResponse)
def stories(request: Request):
    with db_connect() as db:
        content = prompt_content(db)
        version = active_prompt_version(db)
        versions = prompt_versions(db)
    return page(request, "stories.html", content=content, version=version, versions=versions,
                default_version=content_version(default_content()), message=request.query_params.get("message"))


@app.post("/stories/{index}/toggle")
def story_toggle(index: int, request: Request):
    with db_connect() as db:
        content = prompt_content(db)
    if not 0 <= index < len(content["stories"]):
        return back("/stories", "Unknown story.")
    content["stories"][index]["enabled"] = not content["stories"][index].get("enabled", True)
    result = save_content(request, content, "/stories", "message.html", title="Stories")
    return result


def story_form(request: Request, index: int | None, story: dict, error: str | None = None, status_code: int = 200):
    preview = None
    if index is not None and not error:
        try:
            preview = prompts_for(4, 0, {**default_content(), "stories": [story | {"enabled": True}]})
        except (KeyError, IndexError, ValueError):
            preview = None
    return page(request, "story.html", status_code, index=index, story=story, error=error, preview=preview)


@app.get("/stories/new", response_class=HTMLResponse)
def story_new(request: Request):
    return story_form(request, None, {"title": "", "beats": ["", "", "", ""], "enabled": True})


@app.get("/stories/{index}", response_class=HTMLResponse)
def story_edit(index: int, request: Request):
    with db_connect() as db:
        content = prompt_content(db)
    if not 0 <= index < len(content["stories"]):
        return HTMLResponse("Unknown story", status_code=404)
    return story_form(request, index, content["stories"][index])


def _story_from(data) -> dict:
    return {"title": str(data.get("title", "")).strip(), "beats": [str(data.get(f"beat{n}", "")).strip() for n in range(1, 5)],
            "enabled": data.get("enabled") == "yes"}


@app.post("/stories/new")
def story_create(request: Request):
    story = _story_from(form(request))
    with db_connect() as db:
        content = prompt_content(db)
    content["stories"].append(story)
    return save_content(request, content, "/stories", "story.html", index=None, story=story, preview=None)


@app.post("/stories/{index}")
def story_save(index: int, request: Request):
    data = form(request)
    with db_connect() as db:
        content = prompt_content(db)
    if not 0 <= index < len(content["stories"]):
        return back("/stories", "Unknown story.")
    if data.get("delete") == "yes":
        del content["stories"][index]
        return save_content(request, content, "/stories", "message.html", title="Stories")
    story = _story_from(data)
    content["stories"][index] = story
    return save_content(request, content, f"/stories/{index}", "story.html", index=index, story=story, preview=None)


def _lines(value) -> list[str]:
    return [line.strip() for line in str(value or "").splitlines() if line.strip()]


@app.get("/prompts", response_class=HTMLResponse)
def prompt_settings(request: Request):
    with db_connect() as db:
        content = prompt_content(db)
    return page(request, "prompts.html", content=content, error=None, message=request.query_params.get("message"))


@app.post("/prompts")
def prompt_settings_save(request: Request):
    data = form(request)
    with db_connect() as db:
        content = prompt_content(db)
    content |= {
        "scene": str(data.get("scene", "")).strip(),
        "clear_faces": " " + str(data.get("clear_faces", "")).strip() if str(data.get("clear_faces", "")).strip() else "",
        "backdrops": _lines(data.get("backdrops")),
        "opening_stagings": _lines(data.get("opening_stagings")),
        "stagings": _lines(data.get("stagings")),
    }
    return save_content(request, content, "/prompts", "prompts.html", content=content, message=None)


@app.post("/stories/versions/{version}/activate")
def prompts_activate(version: str, request: Request):
    try:
        with db_connect() as db:
            activate_prompt_version(db, version)
    except ContentError as exc:
        return back("/stories", str(exc))
    return back("/stories", f"Active again: {version}.")


# Seeds

@app.get("/seeds", response_class=HTMLResponse)
def seeds(request: Request):
    with db_connect() as db:
        rows = db.execute("SELECT id, rights_ref, imported_at, use_count FROM seeds WHERE removed_at IS NULL ORDER BY imported_at DESC").fetchall()
    return page(request, "seeds.html", seeds=rows, message=request.query_params.get("message"))


@app.get("/seeds/{seed_id}.jpg")
def seed_image(seed_id: str):
    with db_connect() as db:
        row = db.execute("SELECT path FROM seeds WHERE id=? AND removed_at IS NULL", (seed_id,)).fetchone()
    path = Path(row["path"]) if row else None
    if path is None or path.parent != storage_root() / "seed_portraits" or not path.is_file():
        return Response(status_code=404)
    return Response(path.read_bytes(), media_type="image/jpeg")


@app.post("/seeds")
async def seed_add(request: Request):
    data = form(request)
    if data.get("attest") != "yes":
        return back("/seeds", "Confirm that you hold the rights to use this image.")
    upload = data.get("image")
    if upload is None or not hasattr(upload, "read"):
        return back("/seeds", "Choose an image file.")
    payload = await upload.read()
    try:
        with db_connect() as db:
            seed_id = import_seed(db, payload, str(data.get("rights_reference", "")).strip())
    except ValueError as exc:
        return back("/seeds", f"Not imported: {exc}")
    return back("/seeds", f"Imported: {seed_id}")


@app.post("/seeds/{seed_id}/remove")
def seed_remove(seed_id: str, request: Request):
    with db_connect() as db:
        removed = remove_seed(db, seed_id)
    return back("/seeds", "Removed, along with every strip containing it." if removed else "Unknown seed.")


# Boxes

@app.get("/devices", response_class=HTMLResponse)
def devices(request: Request):
    with db_connect() as db:
        rows = device_rows(db)
    return page(request, "devices.html", devices=rows, code=None, message=request.query_params.get("message"))


@app.post("/devices/pair", response_class=HTMLResponse)
def devices_pair(request: Request):
    name = str(form(request).get("device_id", "")).strip()
    try:
        with db_connect() as db:
            code = start_pairing(db, name)
            rows = device_rows(db)
    except ValueError as exc:
        return back("/devices", str(exc))
    return page(request, "devices.html", devices=rows, code=code, name=name, minutes=VALID_MINUTES, message=None)


@app.post("/devices/{device_id}/revoke")
def devices_revoke(device_id: str, request: Request):
    from ..auth import revoke_device
    with db_connect() as db:
        revoked = revoke_device(db, device_id)
    return back("/devices", f"Revoked {device_id}. Pair it again to use it." if revoked else "No active box with that name.")


@app.post("/devices/{device_id}/label")
def devices_label(device_id: str, request: Request):
    from ..presence import set_label
    label = str(form(request).get("label", ""))
    try:
        with db_connect() as db:
            changed = set_label(db, device_id, label)
    except ValueError as exc:
        return back("/devices", str(exc))
    if not changed:
        return back("/devices", "No active box with that name.")
    return back("/devices", f"Public name of {device_id} saved." if label.strip() else f"Public name of {device_id} removed.")


# Audit

@app.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request, event: str = ""):
    with db_connect() as db:
        kinds = [row[0] for row in db.execute("SELECT DISTINCT event FROM audit_events ORDER BY event")]
        query = "SELECT * FROM audit_events " + ("WHERE event=? " if event else "") + "ORDER BY id DESC LIMIT 200"
        rows = db.execute(query, (event,) if event else ()).fetchall()
    return page(request, "audit.html", events=rows, kinds=kinds, selected=event)
