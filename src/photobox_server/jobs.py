"""Durable generation jobs: a queue in SQLite and one worker thread.

One job runs at a time, because a model uses most of the machine's memory. Frames are kept while a
job is active, so a retry or restart continues from the frames it has. Failure never falls back
to the mock: a job that cannot be generated fails visibly.
"""
from __future__ import annotations
import json
import logging
import random
import shutil
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from PIL import Image
from . import audit, live
from .presence import events as presence_events
from .config import get_frame_count, get_provider, int_setting
from .core import SEED_PREFIX, db_connect, now_iso, storage_root, strip_images_active, write_private_image
from .pool import record_use, select_match
from .library import active_prompt_version, prompt_content
from .prompts import prompts_for
from .providers import FrameMetrics, FrameRequest, GenerationCancelled, GenerationError, ImageGenerationProvider, create_provider
from .strip import dated_footer, frame_problem, render_strip
from .sessions import COMPLETE, FAILED, PROCESSING, QUEUED, UPLOADED, transition

QUEUED_JOB, RUNNING_JOB, COMPLETE_JOB, FAILED_JOB, CANCELLED_JOB = "queued", "running", "complete", "failed", "cancelled"
ACTIVE_JOB_STATES = (QUEUED_JOB, RUNNING_JOB)
logger = logging.getLogger(__name__)
_work_available = threading.Event()


def max_attempts() -> int:
    return int_setting("PHOTOBOX_GENERATION_MAX_ATTEMPTS", 3, 1, 10)


def frame_timeout() -> int:
    return int_setting("PHOTOBOX_GENERATION_FRAME_TIMEOUT_SECONDS", 600, 1, 86400)


def retry_delay() -> int:
    return int_setting("PHOTOBOX_GENERATION_RETRY_DELAY_SECONDS", 30, 0, 86400)


def job_folder(job_id: str) -> Path:
    return storage_root() / "jobs" / job_id


def discard_frames(job_id: str) -> None:
    folder = job_folder(job_id)
    if folder.parent == storage_root() / "jobs":
        shutil.rmtree(folder, ignore_errors=True)


def wake() -> None:
    _work_available.set()


def enqueue(db: sqlite3.Connection, session_id: str, source_state: str, wait_seconds: int = 0) -> str | None:
    """Queue a session once. Returns None if another request already moved it.

    With `wait_seconds`, the job waits that long for a live partner before it may start (ADR-024).
    """
    if not transition(db, session_id, source_state, QUEUED):
        return None
    existing = db.execute("SELECT id FROM generation_jobs WHERE session_id=?", (session_id,)).fetchone()
    job_id = existing["id"] if existing else str(uuid.uuid4())
    now = now_iso()
    start = (datetime.now(timezone.utc) + timedelta(seconds=wait_seconds)).isoformat() if wait_seconds else now
    if existing:
        db.execute(
            "UPDATE generation_jobs SET provider=?,prompt_version=?,status=?,run_id=NULL,attempts=0,frames_done=0,next_attempt_at=?,error=NULL,finished_at=NULL,live_wait=?,created_at=? WHERE id=?",
            (get_provider(), active_prompt_version(db), QUEUED_JOB, start, int(bool(wait_seconds)), now, job_id),
        )
    else:
        db.execute(
            "INSERT INTO generation_jobs(id,session_id,provider,prompt_version,status,seed,frames_total,next_attempt_at,created_at,live_wait) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (job_id, session_id, get_provider(), active_prompt_version(db), QUEUED_JOB, random.randrange(2**31), get_frame_count(), start, now, int(bool(wait_seconds))),
        )
    db.execute("UPDATE sessions SET error=NULL WHERE id=?", (session_id,))
    return job_id


def job_for_session(db: sqlite3.Connection, session_id: str) -> sqlite3.Row | None:
    return db.execute("SELECT * FROM generation_jobs WHERE session_id=?", (session_id,)).fetchone()


def cancel_for_session(db: sqlite3.Connection, session_id: str, reason: str) -> bool:
    """Mark a session's active job cancelled. The worker notices and stops the running process."""
    job = job_for_session(db, session_id)
    if job is None or job["status"] not in ACTIVE_JOB_STATES:
        return False
    db.execute("UPDATE generation_jobs SET status=?,run_id=NULL,finished_at=? WHERE id=?", (CANCELLED_JOB, now_iso(), job["id"]))
    audit.record(db, "generation_cancelled", session_id=session_id, detail=reason)
    discard_frames(job["id"])
    return True


def discard_frames_using(db: sqlite3.Connection, image_ids: list[str]) -> None:
    """A withdrawn image must not survive in another session's unfinished frames."""
    for image_id in image_ids:
        rows = db.execute(
            f"""SELECT j.id, s.id AS session_id FROM generation_jobs j JOIN sessions s ON s.id = j.session_id
                WHERE s.match_portrait_id = ? AND j.status IN ({",".join("?" * len(ACTIVE_JOB_STATES))})""",
            (image_id, *ACTIVE_JOB_STATES),
        ).fetchall()
        for row in rows:
            db.execute("UPDATE sessions SET match_portrait_id=NULL WHERE id=?", (row["session_id"],))
            discard_frames(row["id"])


def release_partner(db: sqlite3.Connection, session: sqlite3.Row) -> None:
    """After one side of a live pair cancels or withdraws, the other visitor still gets a strip.

    `session` is the row from before the change. A leaving follower's photo is removed from the
    leader's frames first; the leader then starts again with a pool portrait or seed. A leaving
    leader's follower had no job of its own, so it is queued on its own.
    """
    # A finished pair stays recorded: the shared strip shows both, so it must be deleted with either photo.
    if session["live_partner"] is None or session["status"] not in (QUEUED, PROCESSING):
        return
    if session["live_role"] == live.FOLLOW and session["portrait_id"]:
        discard_frames_using(db, [session["portrait_id"]])
    partner_id = live.unpair(db, session["id"])
    if partner_id is None or session["live_role"] != live.LEAD:
        return
    partner = db.execute("SELECT status FROM sessions WHERE id=?", (partner_id,)).fetchone()
    if partner is not None and partner["status"] == QUEUED and transition(db, partner_id, QUEUED, UPLOADED):
        if enqueue(db, partner_id, UPLOADED):
            wake()


def recover_after_restart(db: sqlite3.Connection) -> int:
    """Requeue jobs interrupted by a shutdown. Attempts are counted, so a job that keeps crashing the server stops."""
    interrupted = db.execute("SELECT * FROM generation_jobs WHERE status=?", (RUNNING_JOB,)).fetchall()
    for job in interrupted:
        if job["attempts"] >= max_attempts():
            _mark_failed(db, job["id"], job["session_id"], "interrupted by restart")
        else:
            db.execute("UPDATE generation_jobs SET status=?,run_id=NULL,next_attempt_at=? WHERE id=?", (QUEUED_JOB, now_iso(), job["id"]))
            transition(db, job["session_id"], PROCESSING, QUEUED)
    # Sessions from before jobs existed have nothing that could continue them.
    db.execute("UPDATE sessions SET status=?,error='interrupted by restart' WHERE status=?", (FAILED, PROCESSING))
    return len(interrupted)


def cleanup_job_folders(db: sqlite3.Connection) -> int:
    """Delete frames of any job that is no longer active."""
    folder = storage_root() / "jobs"
    active = {row[0] for row in db.execute(f"SELECT id FROM generation_jobs WHERE status IN ({','.join('?' * len(ACTIVE_JOB_STATES))})", ACTIVE_JOB_STATES)}
    removed = 0
    for path in folder.iterdir():
        if path.is_dir() and path.name not in active:
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    return removed


def _mark_failed(db: sqlite3.Connection, job_id: str, session_id: str, message: str) -> None:
    db.execute("UPDATE generation_jobs SET status=?,run_id=NULL,error=?,finished_at=? WHERE id=?", (FAILED_JOB, message, now_iso(), job_id))
    if transition(db, session_id, PROCESSING, FAILED):
        db.execute("UPDATE sessions SET error=? WHERE id=?", (message, session_id))
        box = db.execute("SELECT box_id FROM sessions WHERE id=?", (session_id,)).fetchone()
        presence_events.emit(db, "generation.failed", box["box_id"], session_id=session_id)
        live.fail_follower(db, session_id, message)
    audit.record(db, "generation_failed", session_id=session_id, detail=message)
    discard_frames(job_id)


@dataclass(frozen=True)
class Run:
    job_id: str
    run_id: str
    session_id: str
    portrait_a: Path
    portrait_b: Path
    match_id: str
    seed: int
    frames_total: int
    attempts: int
    prompt_version: str


def _image_path(db: sqlite3.Connection, image_id: str) -> Path | None:
    if image_id.startswith(SEED_PREFIX):
        row = db.execute("SELECT path FROM seeds WHERE id=? AND removed_at IS NULL", (image_id[len(SEED_PREFIX):],)).fetchone()
    else:
        row = db.execute("SELECT path FROM portraits WHERE id=?", (image_id,)).fetchone()
    return Path(row["path"]) if row else None


def _claim() -> Run | None:
    with db_connect() as db:
        job = db.execute(
            "SELECT * FROM generation_jobs WHERE status=? AND next_attempt_at<=? ORDER BY created_at LIMIT 1",
            (QUEUED_JOB, now_iso()),
        ).fetchone()
        if job is None or not transition(db, job["session_id"], QUEUED, PROCESSING):
            return None
        session = db.execute("SELECT * FROM sessions WHERE id=?", (job["session_id"],)).fetchone()
        own = _image_path(db, session["portrait_id"]) if session["portrait_id"] else None
        if own is None:
            _mark_failed(db, job["id"], session["id"], "portrait has expired")
            return None
        # Keep the counterpart while it may still be used, because existing frames show it.
        match_id = session["match_portrait_id"]
        if match_id is None or not strip_images_active(db, session):
            discard_frames(job["id"])
            if session["live_partner"]:
                # The live partner's photo is gone; they are handled by their withdrawal or expiry.
                live.unpair(db, session["id"])
            matched = select_match(db, session["id"])
            if matched is None:
                _mark_failed(db, job["id"], session["id"], "no eligible match")
                return None
            match_id = matched[0]
            db.execute("UPDATE sessions SET match_portrait_id=? WHERE id=?", (match_id, session["id"]))
            db.execute("UPDATE generation_jobs SET frames_done=0 WHERE id=?", (job["id"],))
            if not match_id.startswith(SEED_PREFIX):
                presence_events.emit(db, "match.pool_created", session["box_id"], session_id=session["id"])
        match_path = _image_path(db, match_id)
        run_id = str(uuid.uuid4())
        attempts = job["attempts"] + 1
        db.execute(
            "UPDATE generation_jobs SET status=?,run_id=?,attempts=?,started_at=? WHERE id=?",
            (RUNNING_JOB, run_id, attempts, now_iso(), job["id"]),
        )
        presence_events.emit(db, "generation.started", session["box_id"], session_id=session["id"])
        follower = live.follower_of(db, session["id"])
        if follower is not None:
            presence_events.emit(db, "generation.started", follower["box_id"], session_id=follower["id"])
        return Run(job["id"], run_id, session["id"], own, match_path, match_id, job["seed"], job["frames_total"], attempts, job["prompt_version"])


def _still_current(run: Run) -> bool:
    with db_connect() as db:
        row = db.execute("SELECT run_id FROM generation_jobs WHERE id=?", (run.job_id,)).fetchone()
    return row is not None and row["run_id"] == run.run_id


def _handle_failure(run: Run, message: str) -> None:
    with db_connect() as db:
        row = db.execute("SELECT run_id FROM generation_jobs WHERE id=?", (run.job_id,)).fetchone()
        if row is None or row["run_id"] != run.run_id:
            return
        if run.attempts < max_attempts():
            retry_at = (datetime.now(timezone.utc) + timedelta(seconds=retry_delay())).isoformat()
            db.execute("UPDATE generation_jobs SET status=?,run_id=NULL,error=?,next_attempt_at=? WHERE id=?", (QUEUED_JOB, message, retry_at, run.job_id))
            transition(db, run.session_id, PROCESSING, QUEUED)
        else:
            _mark_failed(db, run.job_id, run.session_id, message)


def _finish(run: Run, provider: ImageGenerationProvider, frames: list[Path], metrics: list[FrameMetrics]) -> None:
    images = []
    for path in frames:
        with Image.open(path) as image:
            images.append(image.convert("RGB"))
    dest = storage_root() / "generated" / f"{run.session_id}.png"
    kept = False
    try:
        write_private_image(render_strip(images, dated_footer(provider.strip_footer)), dest, "PNG")
        # Read it back, so a damaged file can never become the session's result.
        with Image.open(dest) as written:
            written.verify()
        with db_connect() as db:
            job = db.execute("SELECT run_id FROM generation_jobs WHERE id=?", (run.job_id,)).fetchone()
            if job is None or job["run_id"] != run.run_id:
                return
            session = db.execute("SELECT * FROM sessions WHERE id=?", (run.session_id,)).fetchone()
            # An image can expire or be withdrawn while frames are made, and then the strip may not exist.
            # Withdrawal clears the session's match, which alone would pass the check below.
            if session["match_portrait_id"] != run.match_id or not strip_images_active(db, session):
                raise GenerationError("an image is no longer available")
            if not transition(db, run.session_id, PROCESSING, COMPLETE):
                return
            record_use(db, run.match_id, run.session_id)
            db.execute("UPDATE sessions SET output_path=?,finished_at=? WHERE id=?", (str(dest), now_iso(), run.session_id))
            summary = {"frames": [{"seconds": round(m.seconds, 2), "peak_memory_gb": m.peak_memory_gb} for m in metrics]}
            db.execute(
                "UPDATE generation_jobs SET status=?,run_id=NULL,finished_at=?,metrics=? WHERE id=?",
                (COMPLETE_JOB, now_iso(), json.dumps(summary), run.job_id),
            )
            audit.record(db, "generation_completed", session_id=run.session_id, detail=provider.name)
            presence_events.emit(db, "generation.completed", session["box_id"], session_id=run.session_id)
            live.complete_follower(db, run.session_id, str(dest))
            kept = True
    finally:
        if not kept:
            dest.unlink(missing_ok=True)
    discard_frames(run.job_id)


def run_one(provider: ImageGenerationProvider, stop: threading.Event | None = None) -> bool:
    """Run the oldest waiting job. Returns False if there was nothing to do."""
    run = _claim()
    if run is None:
        return False

    def should_cancel() -> bool:
        return (stop is not None and stop.is_set()) or not _still_current(run)

    folder = job_folder(run.job_id)
    frames: list[Path] = []
    metrics: list[FrameMetrics] = []
    # The content the job was queued with, even if it has been edited since.
    with db_connect() as db:
        content = prompt_content(db, run.prompt_version)
    prompts = prompts_for(run.frames_total, run.seed, content)
    try:
        for index in range(run.frames_total):
            output = folder / f"frame-{index + 1}.png"
            frames.append(output)
            # A frame from an earlier attempt is only reused if it is still sound.
            if output.exists():
                if frame_problem(output, getattr(provider, "max_faces", None)) is None:
                    continue
                output.unlink()
            folder.mkdir(parents=True, exist_ok=True)
            folder.chmod(0o700)
            # A new attempt gets a new seed, so it does not simply repeat a frame that failed.
            # All frames share a seed, which helps them look like one session.
            seed = run.seed + 1000 * (run.attempts - 1)
            request = FrameRequest(run.portrait_a, run.portrait_b, prompts[index], index, run.frames_total, seed, output, frame_timeout())
            metrics.append(provider.generate_frame(request, should_cancel))
            problem = frame_problem(output, getattr(provider, "max_faces", None))
            if problem is not None:
                output.unlink(missing_ok=True)
                raise GenerationError(f"frame {index + 1} failed its check: {problem}")
            output.chmod(0o600)
            with db_connect() as db:
                db.execute("UPDATE generation_jobs SET frames_done=? WHERE id=? AND run_id=?", (index + 1, run.job_id, run.run_id))
        if should_cancel():
            raise GenerationCancelled()
        _finish(run, provider, frames, metrics)
    except GenerationCancelled:
        # On shutdown the job stays as it is, and the next start requeues it.
        if stop is None or not stop.is_set():
            with db_connect() as db:
                job = db.execute("SELECT status FROM generation_jobs WHERE id=?", (run.job_id,)).fetchone()
                if job is not None and job["status"] == CANCELLED_JOB:
                    discard_frames(run.job_id)
    except GenerationError as exc:
        _handle_failure(run, str(exc))
    except Exception as exc:
        # Only the type: a message from a library could contain paths or other details.
        logger.error("generation failed: %s", type(exc).__name__)
        _handle_failure(run, "generation failed")
    return True


def run_pending(provider: ImageGenerationProvider | None = None) -> int:
    """Run every job that is ready now."""
    provider = provider or create_provider(get_provider())
    count = 0
    while run_one(provider):
        count += 1
    return count


class Worker:
    def __init__(self, provider: ImageGenerationProvider) -> None:
        self.provider = provider
        self.stop_requested = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="photobox-generation", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_requested.set()
        wake()
        self.thread.join(timeout=15)

    def _loop(self) -> None:
        while not self.stop_requested.is_set():
            worked = False
            try:
                worked = run_one(self.provider, self.stop_requested)
            except Exception as exc:
                logger.error("generation worker error: %s", type(exc).__name__)
            if not worked:
                _work_available.wait(1)
                _work_available.clear()


__all__ = ["Worker", "enqueue", "run_one", "run_pending", "job_for_session", "cancel_for_session", "recover_after_restart", "wake", "UPLOADED"]
