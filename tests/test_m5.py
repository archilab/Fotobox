"""M5: strip geometry, frame checks, output profiles, no corrupt results. Synthetic images only."""
import io
import threading
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from conftest import process
from photobox_server import jobs
from photobox_server.app import app
from photobox_server.auth import create_device
from photobox_server.config import ConfigError
from photobox_server.core import db_connect, storage_root
from photobox_server.pool import import_seed
from photobox_server.providers import FrameMetrics, GenerationCancelled
from photobox_server.providers.mock import MockProvider
from photobox_server.strip import apply_profile, frame_problem, render_strip


def scene(size=(768, 1024)):
    return Image.linear_gradient("L").resize(size).convert("RGB")


def jpeg(color="#878787"):
    out = io.BytesIO()
    Image.new("RGB", (120, 160), color).save(out, format="JPEG")
    return out.getvalue()


def ready(client):
    with db_connect() as db:
        import_seed(db, jpeg("#222222"), "SYNTHETIC")
    sid = client.post("/api/v1/sessions").json()["id"]
    client.post(f"/api/v1/sessions/{sid}/consent", json={"processing": True, "documented_externally": True})
    assert client.post(f"/api/v1/sessions/{sid}/portrait", files={"image": ("x.jpg", jpeg(), "image/jpeg")}).status_code == 200
    return sid


def state(client, sid):
    return client.get(f"/api/v1/sessions/{sid}").json()


def strips():
    return list((storage_root() / "generated").iterdir())


class Scripted:
    """Produces each listed kind of frame in turn, then good ones."""

    name, strip_footer = "scripted", "SCRIPTED"

    def __init__(self, *kinds):
        self.kinds = list(kinds)
        self.seeds = []

    def generate_frame(self, request, should_cancel):
        self.seeds.append(request.seed)
        kind = self.kinds.pop(0) if self.kinds else "good"
        if kind == "good":
            scene().save(request.output, "PNG")
        elif kind == "blank":
            Image.new("RGB", (768, 1024), "black").save(request.output, "PNG")
        elif kind == "small":
            scene((100, 100)).save(request.output, "PNG")
        elif kind == "corrupt":
            request.output.write_bytes(b"not an image at all")
        return FrameMetrics(seconds=1.0)


# Geometry

@pytest.mark.parametrize("count", [3, 4])
@pytest.mark.parametrize("width", [384, 560, 1200])
def test_expected_geometry(monkeypatch, count, width):
    monkeypatch.setenv("PHOTOBOX_STRIP_WIDTH", str(width))
    result = render_strip([scene()] * count, "FOOTER")
    margin = gap = round(width * 0.03)
    frame_height = round((width - 2 * margin) * 1024 / 768)
    assert result.width == width
    assert result.height == frame_height * count + gap * (count - 1) + 2 * margin + max(42, round(width * 0.075))
    assert result.mode == "RGB" and result.height > result.width


def test_frames_of_another_shape_are_cropped_not_stretched():
    wide = Image.new("RGB", (2000, 1000), "white")
    for x in range(900, 1100):
        for y in range(0, 1000, 50):
            wide.putpixel((x, y), (0, 0, 0))
    wide.paste((255, 0, 0), (0, 0, 100, 1000))
    result = render_strip([scene(), wide, wide], "FOOTER")
    # The red edge lies outside the crop to the first frame's shape.
    assert not any(result.getpixel((x, 700 + 200)) == (255, 0, 0) for x in range(0, result.width, 10))


def test_footer_states_the_origin():
    plain = render_strip([scene()] * 3, "")
    labelled = render_strip([scene()] * 3, "CONNECTED PHOTOBOX - AI GENERATED")
    assert plain.tobytes() != labelled.tobytes()


# Frame checks

@pytest.mark.parametrize("make,expected", [
    (lambda p: scene().save(p, "PNG"), None),
    (lambda p: Image.new("RGB", (768, 1024), "black").save(p, "PNG"), "blank"),
    (lambda p: Image.new("RGB", (768, 1024), "white").save(p, "PNG"), "blank"),
    (lambda p: scene((255, 1024)).save(p, "PNG"), "too small"),
    (lambda p: p.write_bytes(b"not an image"), "unreadable"),
    (lambda p: p.write_bytes(scene_bytes()[:200]), "unreadable"),
])
def test_frame_problem(tmp_path, make, expected):
    path = tmp_path / "frame.png"
    make(path)
    assert frame_problem(path) == expected


def scene_bytes():
    out = io.BytesIO()
    scene().save(out, "PNG")
    return out.getvalue()


def test_missing_frame_is_reported(tmp_path):
    assert frame_problem(tmp_path / "missing.png") == "unreadable"


@pytest.mark.parametrize("kind", ["blank", "small", "corrupt"])
def test_bad_frame_is_regenerated_with_a_new_seed(client, kind):
    sid = ready(client)
    provider = Scripted(kind)
    process(client, sid, provider)
    assert state(client, sid)["status"] == "complete"
    assert state(client, sid)["job"]["attempts"] == 2
    assert provider.seeds[0] != provider.seeds[1]
    assert len(strips()) == 1


def test_frames_that_keep_failing_end_the_job_without_a_strip(client, monkeypatch):
    monkeypatch.setenv("PHOTOBOX_GENERATION_MAX_ATTEMPTS", "2")
    sid = ready(client)
    process(client, sid, Scripted("blank", "blank"))
    status = state(client, sid)
    assert status["status"] == "failed" and status["error"] == "frame 1 failed its check: blank"
    assert strips() == [] and client.get(f"/api/v1/sessions/{sid}/result").status_code == 404


def test_damaged_frame_from_an_earlier_run_is_replaced(client):
    sid = ready(client)
    client.post(f"/api/v1/sessions/{sid}/process")
    stop = threading.Event()

    class StopAfterTwo(Scripted):
        def generate_frame(self, request, should_cancel):
            if request.frame_index == 2:
                stop.set()
                raise GenerationCancelled()
            return super().generate_frame(request, should_cancel)

    jobs.run_one(StopAfterTwo(), stop)
    with db_connect() as db:
        job_id = jobs.job_for_session(db, sid)["id"]
        assert jobs.recover_after_restart(db) == 1
    (jobs.job_folder(job_id) / "frame-2.png").write_bytes(b"cut off by a crash")
    provider = Scripted()
    jobs.run_pending(provider)
    assert len(provider.seeds) == 3
    assert state(client, sid)["status"] == "complete"
    with Image.open(strips()[0]) as result:
        result.verify()


# No corrupt results

def test_failed_strip_write_leaves_no_result(client, monkeypatch):
    sid = ready(client)
    monkeypatch.setenv("PHOTOBOX_GENERATION_MAX_ATTEMPTS", "1")
    with monkeypatch.context() as patch:
        patch.setattr(jobs, "write_private_image", lambda *_a, **_k: (_ for _ in ()).throw(OSError("disk full")))
        process(client, sid, Scripted())
    assert strips() == [] and state(client, sid)["status"] == "failed"
    assert state(client, sid)["has_result"] is False


def test_damaged_strip_is_never_kept(client, monkeypatch):
    sid = ready(client)
    monkeypatch.setenv("PHOTOBOX_GENERATION_MAX_ATTEMPTS", "1")

    def damage(_image, dest, *_a, **_k):
        dest.write_bytes(b"damaged")

    with monkeypatch.context() as patch:
        patch.setattr(jobs, "write_private_image", damage)
        process(client, sid, Scripted())
    assert strips() == [] and state(client, sid)["status"] == "failed"


# Profiles

def test_profiles_have_the_expected_form():
    strip = render_strip([scene()] * 4, "FOOTER")
    assert apply_profile(strip, "color").mode == "RGB"
    assert apply_profile(strip, "grayscale").mode == "L"
    thermal = apply_profile(strip, "thermal")
    assert thermal.mode == "1" and set(thermal.convert("L").getdata()) == {0, 255}
    assert {apply_profile(strip, p).size for p in ("color", "grayscale", "thermal")} == {strip.size}


def test_thermal_is_deterministic_and_dithers_midtones():
    strip = render_strip([Image.new("RGB", (768, 1024), "#808080")] * 3, "")
    once, again = apply_profile(strip, "thermal"), apply_profile(strip, "thermal")
    assert once.tobytes() == again.tobytes()
    box = once.convert("L").crop((100, 100, 200, 200))
    assert set(box.getdata()) == {0, 255}


def test_thermal_settings_change_the_output(monkeypatch):
    strip = render_strip([scene()] * 3, "")
    normal = apply_profile(strip, "thermal").tobytes()
    monkeypatch.setenv("PHOTOBOX_THERMAL_BRIGHTNESS", "2.0")
    assert apply_profile(strip, "thermal").tobytes() != normal


def test_unknown_profile_is_refused():
    with pytest.raises(ValueError):
        apply_profile(render_strip([scene()] * 3, ""), "sepia")


@pytest.mark.parametrize("setting,value", [("PHOTOBOX_STRIP_WIDTH", "10"), ("PHOTOBOX_THERMAL_CONTRAST", "9"), ("PHOTOBOX_THERMAL_BRIGHTNESS", "bright")])
def test_invalid_strip_setting_stops_startup(anonymous_client, tmp_path, monkeypatch, setting, value):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(setting, value)
    with pytest.raises(ConfigError):
        with TestClient(app):
            pass


@pytest.mark.parametrize("profile,mode", [("color", "RGB"), ("grayscale", "L"), ("thermal", "1")])
def test_result_can_be_fetched_per_profile_without_new_files(client, profile, mode):
    sid = ready(client)
    process(client, sid, MockProvider())
    before = strips()
    response = client.get(f"/api/v1/sessions/{sid}/result", params={"profile": profile})
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"
    with Image.open(io.BytesIO(response.content)) as image:
        assert image.mode == mode
    assert strips() == before


def test_default_result_is_color(client):
    sid = ready(client)
    process(client, sid, MockProvider())
    with Image.open(io.BytesIO(client.get(f"/api/v1/sessions/{sid}/result").content)) as image:
        assert image.mode == "RGB"


def test_invalid_profile_is_refused_over_http(client):
    sid = ready(client)
    process(client, sid, MockProvider())
    assert client.get(f"/api/v1/sessions/{sid}/result", params={"profile": "sepia"}).status_code == 422


def test_profiles_follow_the_same_access_rules(client):
    sid = ready(client)
    process(client, sid, MockProvider())
    with db_connect() as db:
        token = create_device(db, "pi-02")
    url = f"/api/v1/sessions/{sid}/result?profile=thermal"
    assert client.get(url, headers={"Authorization": f"Bearer {token}"}).status_code == 404
    assert TestClient(app).get(url).status_code == 401
    with db_connect() as db:
        db.execute("UPDATE portraits SET expires_at='2000-01-01T00:00:00+00:00'")
    assert client.get(url).status_code == 404


def test_mock_label_stays_visible_in_every_profile(client):
    sid = ready(client)
    process(client, sid, MockProvider())
    with Image.open(strips()[0]) as strip:
        strip.load()
        without_label = strip.copy()
    without_label.paste((135, 135, 135), (18, 18, 280, 40))
    for profile in ("grayscale", "thermal"):
        assert apply_profile(strip, profile).crop((18, 18, 280, 40)).tobytes() != apply_profile(without_label, profile).crop((18, 18, 280, 40)).tobytes()
