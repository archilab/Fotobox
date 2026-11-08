"""Operator dashboard, reachable only from the Mac itself (ADR-022).

    python -m uvicorn photobox_server.dashboard:app --host 127.0.0.1 --port 8001

Shows the boxes, the queue and the stored strips, and edits the consent notice, the stories and prompts,
seed images and box pairing. It shares the database with the photo booth server but never runs jobs.
"""
from __future__ import annotations
import asyncio
import io
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response, StreamingResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image
from .. import audit
from ..config import ConfigError, get_provider, load_env_file
from ..consent_notice import NoticeError, check_notice, file_notice, load_notice, pool_hours, stored_pool_notice
from ..core import db_connect, now_iso, storage_root, strip_images_active
from ..jobs import ACTIVE_JOB_STATES, cancel_for_session
from ..library import (ContentError, activate_prompt_version, active_notice, deactivate_pool_notice, active_prompt_version, notice_versions,
                       prompt_content, prompt_versions, save_notice, save_prompt_content)
from ..pairing import VALID_MINUTES, start_pairing
from ..pool import import_seed, remove_seed
from ..prompts import content_version, default_content, prompts_for
from ..sessions import PROCESSING, QUEUED, UPLOADED, transition
from ..strip import PROFILES, apply_profile
from ..withdrawal import remove_from_pool, withdraw_session
from .encounter import Encounter
from .security import COOKIE, LOCAL_CLIENTS, LOCAL_HOSTS, SESSION_SECONDS, Sessions, password_is_set, password_matches

ONLINE_SECONDS = 90
TEMPLATES = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=select_autoescape(["html"]))
app = FastAPI(title="Photo booth dashboard", docs_url=None, redoc_url=None, openapi_url=None)
app.state.sessions = Sessions()
app.state.encounter = Encounter()
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
    from ..presence import activity_by_box
    activity = activity_by_box(db)
    return [dict(row) | {"online": bool(row["reported_at"] and row["reported_at"] > cutoff and not row["revoked_at"]), "activity": activity.get(row["id"], "")} for row in rows]


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
        row = db.execute("SELECT text, kind FROM consent_notices WHERE version=?", (version,)).fetchone()
        if row is None:
            return back("/consent", "Unknown version.")
        save_notice(db, row["text"], row["kind"])
    location = "/pool" if row["kind"] == "pool" else "/consent"
    return back(location, f"Active again: {version}.")


# The pool (ADR-024)

def pool_rows(db) -> list[dict]:
    now = datetime.now(timezone.utc)
    rows = db.execute(
        """SELECT p.session_id, p.created_at, p.expires_at, p.use_count, s.box_id, c.policy_version FROM portraits p
           JOIN sessions s ON s.id = p.session_id
           LEFT JOIN consent_records c ON c.session_id = p.session_id AND c.purpose = 'pool' AND c.withdrawn_at IS NULL
           WHERE p.pool_eligible = 1 ORDER BY p.expires_at"""
    ).fetchall()
    result = []
    for row in rows:
        left = max(0, int((datetime.fromisoformat(row["expires_at"]) - now).total_seconds()))
        result.append(dict(row) | {"left": f"{left // 3600} h {left % 3600 // 60:02d} min", "usable": row["policy_version"] is not None and left > 0})
    return result


def pool_page(request: Request, status_code: int = 200, *, text: str | None = None, error: str | None = None) -> HTMLResponse:
    with db_connect() as db:
        stored = active_notice(db, "pool")
        versions = notice_versions(db, "pool")
        rows = pool_rows(db)
    source, version = ("dashboard", stored[0]) if stored else ("file", None)
    if text is None:
        current = stored_pool_notice()
        text = current[1] if current else ""
        if current is None:
            source = "none"
    problem = None
    if text and error is None:
        try:
            check_notice(text, "pool")
        except NoticeError as exc:
            problem = str(exc)
    return page(request, "pool.html", status_code, text=text, version=version, source=source, versions=versions, rows=rows,
                hours=pool_hours(), problem=problem, error=error, message=request.query_params.get("message"))


@app.get("/pool", response_class=HTMLResponse)
def pool(request: Request):
    return pool_page(request)


@app.post("/pool", response_class=HTMLResponse)
def save_pool_notice(request: Request):
    text = str(form(request).get("text", ""))
    try:
        with db_connect() as db:
            version = save_notice(db, text, "pool")
    except ContentError as exc:
        return pool_page(request, 422, text=text, error=str(exc))
    return back("/pool", f"Saved and active: {version}. Boxes ask the pool question from their next visitor on.")


@app.post("/pool/off")
def pool_off(request: Request):
    from ..consent_notice import pool_notice_path
    with db_connect() as db:
        deactivate_pool_notice(db)
    if pool_notice_path().is_file():
        return back("/pool", f"The dashboard version is off, but {pool_notice_path()} is still asked. Delete that file to stop.")
    return back("/pool", "The booths no longer ask the pool question. Photos already in the pool stay until their time.")


@app.post("/pool/{session_id}/remove")
def pool_remove(session_id: str, request: Request):
    with db_connect() as db:
        strips = remove_from_pool(db, session_id)
    if strips is None:
        return back("/pool", "That photo is not in the pool.")
    return back("/pool", f"Removed from the pool. Strips with it deleted: {strips}.")


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


# Presence preview (ADR-023, milestone S2)

PRESENCE_LANGUAGES = ("de", "en")
STREAM_INTERVAL_SECONDS = 2.0
STREAM_KEEPALIVE_SECONDS = 15.0
ASPECTS = {"16:9": "16 / 9", "9:16": "9 / 16", "4:3": "4 / 3"}


def preview_snapshot(box_id: str) -> dict:
    """What `box_id` would be told, including simulated boxes, which the preview marks as such."""
    from .. import presence
    with db_connect() as db:
        return presence.snapshot(db, box_id, include_simulated=True)


def active_boxes() -> list[str]:
    with db_connect() as db:
        return [row["id"] for row in db.execute("SELECT id FROM devices WHERE revoked_at IS NULL ORDER BY id")]


def simulator_rows() -> list | None:
    """The simulated boxes, or None when the simulator is off (outside developer mode)."""
    from ..presence import simulator
    if not simulator.enabled():
        return None
    with db_connect() as db:
        return simulator.listing(db)


@app.get("/presence", response_class=HTMLResponse)
def presence_preview(request: Request, box: str = "", lang: str = "de", aspect: str = "16:9"):
    from ..presence.simulator import ACTIONS, MAX_BOXES
    boxes = active_boxes()
    lang = lang if lang in PRESENCE_LANGUAGES else "de"
    aspect = aspect if aspect in ASPECTS else "16:9"
    common = dict(lang=lang, aspect=aspect, ratio=ASPECTS[aspect], aspects=ASPECTS, simulator=simulator_rows(),
                  actions=ACTIONS, max_simulated=MAX_BOXES, message=request.query_params.get("message"))
    if not boxes:
        return page(request, "presence.html", boxes=[], snapshot=None, box="", **common)
    box = box if box in boxes else boxes[0]
    return page(request, "presence.html", boxes=boxes, snapshot=preview_snapshot(box), box=box, **common)


def back_to_preview(request: Request, message: str | None = None) -> RedirectResponse:
    from urllib.parse import urlencode
    data = form(request)
    query = {key: str(data.get(key, "")) for key in ("box", "lang", "aspect") if data.get(key)}
    if message:
        query["message"] = message
    return RedirectResponse("/presence" + ("?" + urlencode(query) if query else ""), status_code=303)


def simulate(request: Request, change) -> Response:
    from ..presence import simulator
    if not simulator.enabled():
        return Response("The simulator needs PHOTOBOX_DEVELOPER_MODE=true.", status_code=404)
    try:
        with db_connect() as db:
            change(db, simulator)
    except ValueError as exc:
        return back_to_preview(request, str(exc))
    return back_to_preview(request)


@app.post("/presence/simulator/add")
def simulator_add(request: Request):
    label = str(form(request).get("label", ""))
    return simulate(request, lambda db, sim: sim.add_box(db, label))


@app.post("/presence/simulator/reset")
def simulator_reset(request: Request):
    return simulate(request, lambda db, sim: sim.reset(db))


# Messages between live pairs (ADR-027)

@app.get("/messages", response_class=HTMLResponse)
def messages_page(request: Request):
    from ..message_filter import load_words
    from ..message_windows import enabled
    with db_connect() as db:
        words = load_words(db)
        stats = db.execute("SELECT * FROM message_stats ORDER BY day DESC LIMIT 14").fetchall()
    return page(request, "messages.html", words="\n".join(words), enabled=enabled(), stats=stats, message=request.query_params.get("message"))


@app.post("/messages")
def messages_save(request: Request):
    from ..message_filter import save_words
    try:
        with db_connect() as db:
            words = save_words(db, str(form(request).get("words", "")))
    except ValueError as exc:
        return back("/messages", str(exc))
    return back("/messages", f"Saved: {len(words)} entries.")


# A simulated encounter with text messages (ADR-025)

def simulated_box(db, box_id: str):
    row = db.execute("SELECT * FROM simulated_boxes WHERE box_id=?", (box_id,)).fetchone()
    if row is None:
        raise ValueError("choose a simulated box")
    return row


@app.get("/presence/simulator/encounter")
def encounter_view():
    from ..presence import simulator
    if not simulator.enabled():
        return Response("The simulator needs PHOTOBOX_DEVELOPER_MODE=true.", status_code=404)
    with db_connect() as db:
        boxes = [dict(row) for row in simulator.listing(db)]
    return app.state.encounter.view(boxes)


@app.get("/static/encounter.js")
def encounter_script():
    return FileResponse(Path(__file__).parent / "static" / "encounter.js", media_type="text/javascript")


@app.post("/presence/simulator/encounter/live")
def encounter_live(request: Request):
    data = form(request)
    return simulate(request, lambda db, _sim: app.state.encounter.start_live(
        simulated_box(db, str(data.get("first", ""))), simulated_box(db, str(data.get("second", "")))))


@app.post("/presence/simulator/encounter/pool")
def encounter_pool(request: Request):
    data = form(request)
    return simulate(request, lambda db, _sim: app.state.encounter.start_pool(simulated_box(db, str(data.get("first", "")))))


@app.post("/presence/simulator/encounter/send")
def encounter_send(request: Request):
    data = form(request)
    return simulate(request, lambda db, _sim: app.state.encounter.send(simulated_box(db, str(data.get("sender", ""))), str(data.get("text", ""))))


@app.post("/presence/simulator/encounter/ready")
def encounter_ready(request: Request):
    return simulate(request, lambda _db, _sim: app.state.encounter.close("generation_completed"))


@app.post("/presence/simulator/{box_id}/{action}")
def simulator_action(box_id: str, action: str, request: Request):
    return simulate(request, lambda db, sim: sim.act(db, box_id, action))


async def presence_stream(request: Request, box: str, once: bool = False):
    """Server-sent events: the snapshot whenever it changes, and a keep-alive comment in between."""
    sent, quiet = None, 0.0
    while True:
        if await request.is_disconnected():
            return
        snapshot = await asyncio.to_thread(preview_snapshot, box)
        current = json.dumps({key: value for key, value in snapshot.items() if key != "generated_at"}, sort_keys=True)
        if current != sent:
            sent, quiet = current, 0.0
            yield f"event: snapshot\ndata: {json.dumps(snapshot)}\n\n"
            if once:
                return
        elif quiet >= STREAM_KEEPALIVE_SECONDS:
            quiet = 0.0
            yield ": keep-alive\n\n"
        await asyncio.sleep(STREAM_INTERVAL_SECONDS)
        quiet += STREAM_INTERVAL_SECONDS


@app.get("/presence/stream")
def presence_stream_route(request: Request, box: str, once: bool = False):
    if box not in active_boxes():
        return Response("Unknown box", status_code=404)
    return StreamingResponse(presence_stream(request, box, once), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.get("/static/presence.js")
def presence_script():
    return FileResponse(Path(__file__).parent / "static" / "presence.js", media_type="text/javascript")


# Audit

@app.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request, event: str = ""):
    with db_connect() as db:
        kinds = [row[0] for row in db.execute("SELECT DISTINCT event FROM audit_events ORDER BY event")]
        query = "SELECT * FROM audit_events " + ("WHERE event=? " if event else "") + "ORDER BY id DESC LIMIT 200"
        rows = db.execute(query, (event,) if event else ()).fetchall()
    return page(request, "audit.html", events=rows, kinds=kinds, selected=event)
