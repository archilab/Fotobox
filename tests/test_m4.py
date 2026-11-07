"""M4: provider boundary, durable jobs, retries, timeouts, cancellation. No real model runs here."""
import io
import json
import os
import stat
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from conftest import process
from photobox_client import cli
from photobox_server import app as app_module
from photobox_server import faces, jobs
from photobox_server import strip as strip_module
from photobox_server.app import app
from photobox_server.config import ConfigError
from photobox_server.core import cleanup_expired, db_connect, storage_root
from photobox_server.pool import import_seed
from photobox_server.prompts import PROMPT_VERSION, prompts_for
from photobox_server.providers import FrameRequest, GenerationCancelled, GenerationError, create_provider
from photobox_server.providers.mflux_flux2 import MfluxFlux2Provider
from photobox_server.providers.mock import MockProvider
from photobox_server.withdrawal import withdraw_session


def jpeg(color="#878787"):
    out = io.BytesIO()
    Image.new("RGB", (120, 160), color).save(out, format="JPEG")
    return out.getvalue()


def add_seed():
    with db_connect() as db:
        return import_seed(db, jpeg("#444444"), "SYNTHETIC")


def uploaded(client, pool=False):
    sid = client.post("/api/v1/sessions").json()["id"]
    client.post(f"/api/v1/sessions/{sid}/consent", json={"processing": True, "pool": pool, "documented_externally": True})
    assert client.post(f"/api/v1/sessions/{sid}/portrait", files={"image": ("x.jpg", jpeg(), "image/jpeg")}).status_code == 200
    return sid


def ready(client):
    add_seed()
    return uploaded(client)


def state(client, sid):
    return client.get(f"/api/v1/sessions/{sid}").json()


def job_row(sid):
    with db_connect() as db:
        return dict(db.execute("SELECT * FROM generation_jobs WHERE session_id=?", (sid,)).fetchone())


def files(folder):
    return list((storage_root() / folder).iterdir())


class Recording:
    """A fake AI model: different from the mock in every visible way, same interface."""

    name, strip_footer = "fake-model", "FAKE MODEL"

    def __init__(self, fail_times=0, error=GenerationError("model failed"), on_frame=None):
        self.requests = []
        self.fail_times = fail_times
        self.error = error
        self.on_frame = on_frame

    def generate_frame(self, request, should_cancel):
        self.requests.append(request)
        if self.on_frame:
            self.on_frame(request)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise self.error
        if should_cancel():
            raise GenerationCancelled()
        Image.linear_gradient("L").resize((768, 1024)).convert("RGB").save(request.output, "PNG")
        return jobs.FrameMetrics(seconds=1.5, peak_memory_gb=10.3)


# Provider boundary

def test_swapping_the_provider_does_not_change_the_api(client, monkeypatch):
    add_seed()
    answers = []
    for provider in (MockProvider(), Recording()):
        sid = uploaded(client)
        response = process(client, sid, provider)
        body = response.json()
        status = state(client, sid)
        answers.append((response.status_code, set(body), set(status), set(status["job"]), status["status"]))
        assert client.get(f"/api/v1/sessions/{sid}/result").headers["content-type"] == "image/png"
    assert answers[0] == answers[1]


def test_routes_do_not_name_a_model():
    source = Path(app_module.__file__).read_text()
    for word in ("flux", "mflux", "mlx", "comfy", "qwen", "render_strip"):
        assert word not in source.lower()


def test_real_provider_output_has_no_mock_label(client):
    sid = ready(client)
    provider = Recording()
    process(client, sid, provider)
    assert state(client, sid)["status"] == "complete"
    assert len(provider.requests) == 4


@pytest.mark.parametrize("frames", ["3", "4"])
def test_frame_count_comes_from_configuration(client, monkeypatch, frames):
    monkeypatch.setenv("PHOTOBOX_NUM_FRAMES", frames)
    sid = ready(client)
    provider = Recording()
    process(client, sid, provider)
    assert [r.frame_index for r in provider.requests] == list(range(int(frames)))
    seed = job_row(sid)["seed"]
    assert [r.prompt for r in provider.requests] == prompts_for(int(frames), seed)
    assert {r.seed for r in provider.requests} == {seed}
    assert state(client, sid)["job"]["frames_done"] == int(frames)


def test_a_resumed_job_keeps_its_backdrop(client):
    sid = ready(client)
    client.post(f"/api/v1/sessions/{sid}/process")
    stop = threading.Event()

    def stop_after_one(request):
        if request.frame_index == 1:
            stop.set()

    first = Recording(on_frame=stop_after_one)
    jobs.run_one(first, stop)
    with db_connect() as db:
        jobs.recover_after_restart(db)
    second = Recording()
    jobs.run_pending(second)
    expected = prompts_for(4, job_row(sid)["seed"])
    assert [r.prompt for r in first.requests + second.requests][-3:] == expected[1:]


def test_invalid_frame_count_stops_startup(anonymous_client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PHOTOBOX_NUM_FRAMES", "7")
    with pytest.raises(ConfigError):
        with TestClient(app):
            pass


def test_own_portrait_is_image_one_and_the_counterpart_image_two(client):
    sid = ready(client)
    provider = Recording()
    process(client, sid, provider)
    assert provider.requests[0].portrait_a.parent.name == "portraits"
    assert provider.requests[0].portrait_b.parent.name == "seed_portraits"


def test_mock_is_deterministic_and_visibly_labelled(tmp_path):
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    a.write_bytes(jpeg("#111111"))
    b.write_bytes(jpeg("#eeeeee"))
    outputs = []
    for name in ("one.png", "two.png"):
        output = tmp_path / name
        MockProvider().generate_frame(FrameRequest(a, b, "p", 0, 4, 1, output, 10), lambda: False)
        outputs.append(output.read_bytes())
    assert outputs[0] == outputs[1]
    assert MockProvider.strip_footer.endswith("MOCK")


def test_failure_never_falls_back_to_the_mock(client, monkeypatch):
    monkeypatch.setenv("PHOTOBOX_GENERATION_MAX_ATTEMPTS", "1")
    sid = ready(client)
    process(client, sid, Recording(fail_times=99))
    assert state(client, sid)["status"] == "failed"
    assert files("generated") == [] and client.get(f"/api/v1/sessions/{sid}/result").status_code == 404


# Queue

def test_process_only_queues(client):
    sid = ready(client)
    response = client.post(f"/api/v1/sessions/{sid}/process")
    assert response.status_code == 202 and response.json()["status"] == "queued"
    assert state(client, sid)["status"] == "queued" and files("generated") == []


def test_jobs_run_oldest_first_and_one_at_a_time(client):
    add_seed()
    first, second = uploaded(client), uploaded(client)
    client.post(f"/api/v1/sessions/{first}/process")
    time.sleep(0.01)
    client.post(f"/api/v1/sessions/{second}/process")
    provider = Recording()
    assert jobs.run_one(provider) is True
    assert state(client, first)["status"] == "complete" and state(client, second)["status"] == "queued"
    assert jobs.run_one(provider) is True and jobs.run_one(provider) is False


def test_progress_is_reported_during_generation(client):
    sid = ready(client)
    seen = []
    provider = Recording(on_frame=lambda request: seen.append((request.frame_index, state(client, sid)["job"]["frames_done"], state(client, sid)["status"])))
    process(client, sid, provider)
    assert seen == [(0, 0, "processing"), (1, 1, "processing"), (2, 2, "processing"), (3, 3, "processing")]


def test_metrics_and_versions_are_recorded(client):
    sid = ready(client)
    process(client, sid, Recording())
    job = job_row(sid)
    assert job["status"] == "complete" and job["prompt_version"] == PROMPT_VERSION and job["provider"] == "mock"
    frames = json.loads(job["metrics"])["frames"]
    assert len(frames) == 4 and frames[0] == {"seconds": 1.5, "peak_memory_gb": 10.3}
    assert files("jobs") == []


def test_frames_are_private(client):
    sid = ready(client)
    modes = []

    class CheckFolder(Recording):
        def generate_frame(self, request, should_cancel):
            result = super().generate_frame(request, should_cancel)
            modes.append(stat.S_IMODE(os.stat(request.output.parent).st_mode))
            return result

    process(client, sid, CheckFolder())
    assert set(modes) == {0o700}
    assert stat.S_IMODE(os.stat(storage_root() / "jobs").st_mode) == 0o700


# Retries

def test_failures_retry_then_succeed_and_keep_finished_frames(client):
    sid = ready(client)
    provider = Recording(fail_times=1)
    process(client, sid, provider)
    assert state(client, sid)["status"] == "complete" and job_row(sid)["attempts"] == 2


def test_retries_are_bounded(client, monkeypatch):
    monkeypatch.setenv("PHOTOBOX_GENERATION_MAX_ATTEMPTS", "3")
    sid = ready(client)
    provider = Recording(fail_times=99)
    process(client, sid, provider)
    assert len(provider.requests) == 3
    status = state(client, sid)
    assert status["status"] == "failed" and status["error"] == "model failed" and status["job"]["attempts"] == 3
    assert files("jobs") == []


def test_retry_waits_for_its_delay(client, monkeypatch):
    monkeypatch.setenv("PHOTOBOX_GENERATION_RETRY_DELAY_SECONDS", "3600")
    sid = ready(client)
    process(client, sid, Recording(fail_times=1))
    assert state(client, sid)["status"] == "queued" and jobs.run_pending(Recording()) == 0


def test_unexpected_error_is_handled_without_details(client, monkeypatch, caplog):
    monkeypatch.setenv("PHOTOBOX_GENERATION_MAX_ATTEMPTS", "1")
    sid = ready(client)
    process(client, sid, Recording(fail_times=1, error=RuntimeError("/secret/portrait/path.jpg")))
    status = state(client, sid)
    assert status["status"] == "failed" and status["error"] == "generation failed"
    assert "/secret/" not in caplog.text and "RuntimeError" in caplog.text


# Restart

def test_job_survives_restart_and_resumes_from_its_frames(client):
    sid = ready(client)
    client.post(f"/api/v1/sessions/{sid}/process")
    stop = threading.Event()

    def stop_after_two(request):
        if request.frame_index == 2:
            stop.set()

    first = Recording(on_frame=stop_after_two)
    jobs.run_one(first, stop)
    assert state(client, sid)["status"] == "processing" and job_row(sid)["frames_done"] == 2
    with db_connect() as db:
        assert jobs.recover_after_restart(db) == 1
    assert state(client, sid)["status"] == "queued"
    second = Recording()
    jobs.run_pending(second)
    assert [r.frame_index for r in second.requests] == [2, 3]
    assert state(client, sid)["status"] == "complete"


def test_job_that_keeps_getting_interrupted_stops(client, monkeypatch):
    monkeypatch.setenv("PHOTOBOX_GENERATION_MAX_ATTEMPTS", "2")
    sid = ready(client)
    client.post(f"/api/v1/sessions/{sid}/process")
    stop = threading.Event()
    stop.set()
    for _ in range(2):
        jobs.run_one(Recording(), stop)
        with db_connect() as db:
            jobs.recover_after_restart(db)
    status = state(client, sid)
    assert status["status"] == "failed" and status["error"] == "interrupted by restart" and files("jobs") == []


def test_real_worker_thread_runs_jobs_and_stops_cleanly(client, tmp_path, monkeypatch):
    add_seed()
    monkeypatch.chdir(tmp_path)
    with TestClient(app, headers={"Authorization": client.headers["Authorization"]}) as running:
        sid = uploaded(running)
        running.post(f"/api/v1/sessions/{sid}/process")
        for _ in range(200):
            if state(running, sid)["status"] == "complete":
                break
            time.sleep(0.02)
        assert state(running, sid)["status"] == "complete"
        assert running.get(f"/api/v1/sessions/{sid}/result").status_code == 200
    assert not any(t.name == "photobox-generation" and t.is_alive() for t in threading.enumerate())


# Cancellation

@pytest.mark.parametrize("wait_for_start", [False, True])
def test_cancel_stops_a_job_and_keeps_the_portrait(client, wait_for_start):
    sid = ready(client)
    client.post(f"/api/v1/sessions/{sid}/process")
    if wait_for_start:
        def cancel(request):
            if request.frame_index == 1:
                assert client.post(f"/api/v1/sessions/{sid}/cancel").json() == {"status": "uploaded", "cancelled": True}
        jobs.run_one(Recording(on_frame=cancel))
    else:
        assert client.post(f"/api/v1/sessions/{sid}/cancel").json()["cancelled"] is True
        assert jobs.run_one(Recording()) is False
    assert state(client, sid)["status"] == "uploaded" and job_row(sid)["status"] == "cancelled"
    assert files("generated") == [] and files("jobs") == [] and len(files("portraits")) == 1
    assert process(client, sid, Recording()).status_code == 202
    assert state(client, sid)["status"] == "complete"


def test_cancel_is_safe_to_repeat_and_refused_when_finished(client):
    sid = ready(client)
    assert client.post(f"/api/v1/sessions/{sid}/cancel").json() == {"status": "uploaded", "cancelled": False}
    process(client, sid, Recording())
    assert client.post(f"/api/v1/sessions/{sid}/cancel").status_code == 409


def test_cancel_needs_the_owning_device(client):
    sid = ready(client)
    from photobox_server.auth import create_device
    with db_connect() as db:
        token = create_device(db, "pi-02")
    assert client.post(f"/api/v1/sessions/{sid}/cancel", headers={"Authorization": f"Bearer {token}"}).status_code == 404


# Images changing while a job waits or runs

def test_withdrawing_a_queued_session_removes_its_job(client):
    sid = ready(client)
    client.post(f"/api/v1/sessions/{sid}/process")
    with db_connect() as db:
        withdraw_session(db, sid)
    assert job_row(sid)["status"] == "cancelled" and jobs.run_one(Recording()) is False


def test_counterpart_withdrawn_during_generation_gives_no_strip_and_a_new_match(client):
    donor = uploaded(client, pool=True)
    add_seed()
    sid = uploaded(client)
    client.post(f"/api/v1/sessions/{sid}/process")
    seen = []

    def withdraw_donor(request):
        seen.append(request.portrait_b.parent.name)
        if request.frame_index == 1 and len(seen) == 2:
            with db_connect() as db:
                withdraw_session(db, donor)

    provider = Recording(on_frame=withdraw_donor)
    jobs.run_pending(provider)
    assert seen[0] == "pool_portraits" and seen[-1] == "seed_portraits"
    assert state(client, sid)["status"] == "complete"
    with db_connect() as db:
        match = db.execute("SELECT match_portrait_id FROM sessions WHERE id=?", (sid,)).fetchone()[0]
    assert match.startswith("seed:") and len([r for r in provider.requests if r.portrait_b.parent.name == "seed_portraits"]) == 4


def test_strip_is_refused_if_the_match_changed_after_the_last_frame(client):
    donor = uploaded(client, pool=True)
    add_seed()
    sid = uploaded(client)
    client.post(f"/api/v1/sessions/{sid}/process")

    def withdraw_at_end(request):
        # After the last frame is requested, and leaving the folder in place as a late write would.
        if request.frame_index == 3 and request.portrait_b.parent.name == "pool_portraits":
            with db_connect() as db:
                db.execute("UPDATE sessions SET match_portrait_id=NULL WHERE id=?", (sid,))

    provider = Recording(on_frame=withdraw_at_end)
    assert jobs.run_one(provider) is True
    assert files("generated") == [] and state(client, sid)["status"] == "queued"
    assert donor


def test_own_portrait_expired_while_queued_fails_clearly(client):
    sid = ready(client)
    client.post(f"/api/v1/sessions/{sid}/process")
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with db_connect() as db:
        db.execute("UPDATE portraits SET expires_at=?", (past,))
        cleanup_expired(db)
    jobs.run_pending(Recording())
    status = state(client, sid)
    assert status["status"] == "failed" and status["error"] == "portrait has expired"


def test_cleanup_removes_frames_of_inactive_jobs_only(client):
    sid = ready(client)
    client.post(f"/api/v1/sessions/{sid}/process")
    active = jobs.job_folder(job_row(sid)["id"])
    active.mkdir()
    stray = storage_root() / "jobs" / "finished-long-ago"
    stray.mkdir()
    with db_connect() as db:
        assert jobs.cleanup_job_folders(db) == 1
    assert active.exists() and not stray.exists()


def test_generation_is_audited(client):
    sid = ready(client)
    process(client, sid, Recording())
    with db_connect() as db:
        rows = db.execute("SELECT event,detail FROM audit_events WHERE session_id=? AND event='generation_completed'", (sid,)).fetchall()
    assert [tuple(r) for r in rows] == [("generation_completed", "fake-model")]


# The mflux adapter, against a stand-in program

FAKE_TOOL = """#!{python}
import json, os, sys, time
args = sys.argv[1:]
output = args[args.index("--output") + 1]
mode = os.environ.get("FAKE_MODE", "ok")
with open(os.environ["FAKE_RECORD"], "w") as record:
    json.dump({{"args": args, "offline": os.environ.get("HF_HUB_OFFLINE"), "pid": os.getpid()}}, record)
print("Peak MLX memory: 10.30 GB")
if mode == "sleep":
    time.sleep(60)
if mode == "fail":
    sys.exit(3)
if mode == "garbage":
    open(output, "wb").write(b"not an image")
    sys.exit(0)
if mode == "none":
    sys.exit(0)
from PIL import Image
Image.linear_gradient("L").resize((768, 1024)).save(output, "PNG")
"""


@pytest.fixture
def fake_tool(tmp_path, monkeypatch):
    tool = tmp_path / "fake-mflux"
    tool.write_text(FAKE_TOOL.format(python=sys.executable))
    tool.chmod(0o700)
    monkeypatch.setenv("PHOTOBOX_MFLUX_COMMAND", str(tool))
    # These tests are about the adapter; test_faces.py covers the detector itself.
    monkeypatch.setattr(faces, "ensure_available", lambda: None)
    monkeypatch.setattr(strip_module, "count_faces", lambda _path: 2)
    record = tmp_path / "record.json"
    monkeypatch.setenv("FAKE_RECORD", str(record))
    return record


def frame_request(tmp_path, timeout=30):
    a, b = tmp_path / "a b.jpg", tmp_path / "c.jpg"
    a.write_bytes(jpeg())
    b.write_bytes(jpeg())
    return FrameRequest(a, b, "two people; rm -rf $(whoami)", 1, 4, 77, tmp_path / "frame.png", timeout)


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_until_gone(pid):
    for _ in range(100):
        if not alive(pid):
            return True
        time.sleep(0.05)
    return False


def test_adapter_passes_arguments_without_a_shell_and_stays_offline(tmp_path, fake_tool):
    request = frame_request(tmp_path)
    metrics = MfluxFlux2Provider().generate_frame(request, lambda: False)
    record = json.loads(fake_tool.read_text())
    args = record["args"]
    assert record["offline"] == "1"
    assert args[args.index("--prompt") + 1] == request.prompt
    assert args[args.index("--image-paths") + 1:args.index("--image-paths") + 3] == [str(request.portrait_a), str(request.portrait_b)]
    assert args[args.index("--seed") + 1] == "77" and "--no-exif" in args and "flux2-klein-4b" in args
    assert metrics.peak_memory_gb == 10.3 and metrics.seconds > 0
    with Image.open(request.output) as image:
        assert image.size == (768, 1024)
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("frame")) == ["frame.png"]


@pytest.mark.parametrize("mode,message", [("fail", "code 3"), ("garbage", "no valid image"), ("none", "no valid image")])
def test_adapter_reports_failures_without_leaving_files(tmp_path, fake_tool, monkeypatch, mode, message):
    monkeypatch.setenv("FAKE_MODE", mode)
    with pytest.raises(GenerationError, match=message):
        MfluxFlux2Provider().generate_frame(frame_request(tmp_path), lambda: False)
    assert not [p for p in tmp_path.iterdir() if p.name.startswith("frame")]


def test_adapter_timeout_stops_the_process(tmp_path, fake_tool, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "sleep")
    with pytest.raises(GenerationError, match="timed out"):
        MfluxFlux2Provider().generate_frame(frame_request(tmp_path, timeout=1), lambda: False)
    assert wait_until_gone(json.loads(fake_tool.read_text())["pid"])


def test_adapter_cancel_stops_the_process(tmp_path, fake_tool, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "sleep")
    started = time.monotonic()
    with pytest.raises(GenerationCancelled):
        MfluxFlux2Provider().generate_frame(frame_request(tmp_path), lambda: time.monotonic() - started > 1)
    assert wait_until_gone(json.loads(fake_tool.read_text())["pid"])
    assert not [p for p in tmp_path.iterdir() if p.name.startswith("frame")]


def test_adapter_error_does_not_contain_the_prompt_or_paths(tmp_path, fake_tool, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "fail")
    request = frame_request(tmp_path)
    with pytest.raises(GenerationError) as caught:
        MfluxFlux2Provider().generate_frame(request, lambda: False)
    assert request.prompt not in str(caught.value) and str(tmp_path) not in str(caught.value)


def test_missing_tool_stops_the_server_with_instructions(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOBOX_PROVIDER", "mflux-flux2")
    monkeypatch.setenv("PHOTOBOX_MFLUX_COMMAND", str(tmp_path / "missing"))
    monkeypatch.setattr(faces, "ensure_available", lambda: None)
    with pytest.raises(ConfigError, match="uv tool install mflux"):
        create_provider("mflux-flux2")


def test_real_provider_runs_through_a_whole_job(client, fake_tool, monkeypatch):
    monkeypatch.setenv("PHOTOBOX_PROVIDER", "mflux-flux2")
    sid = ready(client)
    assert process(client, sid).status_code == 202
    status = state(client, sid)
    assert status["status"] == "complete" and status["job"]["provider"] == "mflux-flux2"
    assert json.loads(job_row(sid)["metrics"])["frames"][0]["peak_memory_gb"] == 10.3


# Client

def make_client_with(monkeypatch, states):
    remaining = list(states)

    def handler(request):
        if request.url.path.endswith("/sessions"):
            return httpx.Response(201, json={"id": "sid", "status": "awaiting_consent"})
        if request.method == "GET":
            return httpx.Response(200, json=remaining.pop(0) if len(remaining) > 1 else remaining[0])
        return httpx.Response(202, json={"status": "queued"})

    real = httpx.Client
    monkeypatch.setattr(cli.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    monkeypatch.setenv(cli.TOKEN_ENV, "t")


def session_state(status, done=0, error=None):
    return {"id": "sid", "status": status, "error": error, "has_result": status == "complete", "job": {"frames_done": done, "frames_total": 4, "attempts": 1}}


@pytest.fixture
def image_file(tmp_path):
    path = tmp_path / "x.jpg"
    path.write_bytes(jpeg())
    return path


def test_client_shows_progress_until_complete(monkeypatch, image_file, capsys):
    make_client_with(monkeypatch, [session_state("queued"), session_state("processing", 2), session_state("complete", 4)])
    cli.main(["capture", "--file", str(image_file), "--attest-documented-consent"])
    output = capsys.readouterr().out
    assert "queued: frame 0/4" in output and "processing: frame 2/4" in output and "result sid" in output


def test_client_reports_failed_generation(monkeypatch, image_file, capsys):
    make_client_with(monkeypatch, [session_state("failed", error="model failed")])
    with pytest.raises(SystemExit):
        cli.main(["capture", "--file", str(image_file), "--attest-documented-consent"])
    assert "model failed" in capsys.readouterr().err


def test_client_stops_waiting_but_says_the_job_continues(monkeypatch, image_file, capsys):
    make_client_with(monkeypatch, [session_state("processing", 1)])
    with pytest.raises(SystemExit):
        cli.main(["capture", "--file", str(image_file), "--attest-documented-consent", "--wait-seconds", "0"])
    assert "continues on the server" in capsys.readouterr().err
