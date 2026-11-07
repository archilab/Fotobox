"""The face-count check. Only numbers come out of it; synthetic images only."""
import io
import pytest
from PIL import Image
from conftest import process
from photobox_server import faces, jobs
from photobox_server import strip as strip_module
from photobox_server.config import ConfigError
from photobox_server.core import db_connect, storage_root
from photobox_server.pool import import_seed
from photobox_server.providers import FrameMetrics
from photobox_server.providers.mflux_flux2 import MfluxFlux2Provider
from photobox_server.providers.mock import MockProvider
from photobox_server.strip import frame_problem


def scene():
    return Image.linear_gradient("L").resize((1024, 768)).convert("RGB")


def jpeg(color="#878787"):
    out = io.BytesIO()
    Image.new("RGB", (120, 160), color).save(out, format="JPEG")
    return out.getvalue()


def ready(client):
    with db_connect() as db:
        import_seed(db, jpeg("#222222"), "SYNTHETIC")
    sid = client.post("/api/v1/sessions").json()["id"]
    client.post(f"/api/v1/sessions/{sid}/consent", json={"processing": True, "documented_externally": True})
    client.post(f"/api/v1/sessions/{sid}/portrait", files={"image": ("x.jpg", jpeg(), "image/jpeg")})
    return sid


class Counting:
    name, strip_footer, max_faces = "counting", "FOOTER", 2

    def __init__(self):
        self.seeds = []

    def generate_frame(self, request, should_cancel):
        self.seeds.append(request.seed)
        scene().save(request.output, "PNG")
        return FrameMetrics(seconds=1.0)


def test_too_many_faces_is_a_problem(tmp_path, monkeypatch):
    path = tmp_path / "frame.png"
    scene().save(path)
    for found, expected in ((0, None), (1, None), (2, None), (3, "too many faces"), (5, "too many faces")):
        monkeypatch.setattr(strip_module, "count_faces", lambda _p, n=found: n)
        assert frame_problem(path, 2) == expected


def test_faces_are_not_counted_unless_asked(tmp_path, monkeypatch):
    path = tmp_path / "frame.png"
    scene().save(path)
    monkeypatch.setattr(strip_module, "count_faces", lambda _p: pytest.fail("counted faces"))
    assert frame_problem(path) is None


def test_the_count_is_skipped_for_an_already_bad_frame(tmp_path, monkeypatch):
    path = tmp_path / "frame.png"
    Image.new("RGB", (1024, 768), "black").save(path)
    monkeypatch.setattr(strip_module, "count_faces", lambda _p: pytest.fail("counted faces"))
    assert frame_problem(path, 2) == "blank"


def test_frame_with_an_extra_person_is_regenerated_with_a_new_seed(client, monkeypatch):
    sid = ready(client)
    answers = iter([3])
    monkeypatch.setattr(strip_module, "count_faces", lambda _p: next(answers, 2))
    provider = Counting()
    process(client, sid, provider)
    state = client.get(f"/api/v1/sessions/{sid}").json()
    assert state["status"] == "complete" and state["job"]["attempts"] == 2
    assert provider.seeds[0] != provider.seeds[1]


def test_frames_that_keep_showing_extra_people_end_the_job(client, monkeypatch):
    sid = ready(client)
    monkeypatch.setenv("PHOTOBOX_GENERATION_MAX_ATTEMPTS", "2")
    monkeypatch.setattr(strip_module, "count_faces", lambda _p: 3)
    process(client, sid, Counting())
    state = client.get(f"/api/v1/sessions/{sid}").json()
    assert state["status"] == "failed" and state["error"] == "frame 1 failed its check: too many faces"
    assert list((storage_root() / "generated").iterdir()) == []


def test_mock_frames_are_not_counted(client, monkeypatch):
    sid = ready(client)
    monkeypatch.setattr(strip_module, "count_faces", lambda _p: pytest.fail("counted faces"))
    process(client, sid, MockProvider())
    assert client.get(f"/api/v1/sessions/{sid}").json()["status"] == "complete"


def test_no_face_limit_by_default(tmp_path, monkeypatch):
    tool = tmp_path / "tool"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o700)
    monkeypatch.setenv("PHOTOBOX_MFLUX_COMMAND", str(tool))
    monkeypatch.delenv("PHOTOBOX_MAX_FACES", raising=False)
    monkeypatch.setattr(faces, "ensure_available", lambda: pytest.fail("loaded the detector"))
    assert MfluxFlux2Provider().max_faces is None
    assert MockProvider.max_faces is None


def test_face_limit_can_be_switched_on(tmp_path, monkeypatch):
    tool = tmp_path / "tool"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o700)
    monkeypatch.setenv("PHOTOBOX_MFLUX_COMMAND", str(tool))
    monkeypatch.setenv("PHOTOBOX_MAX_FACES", "3")
    monkeypatch.setattr(faces, "ensure_available", lambda: None)
    assert MfluxFlux2Provider().max_faces == 3
    monkeypatch.setenv("PHOTOBOX_MAX_FACES", "1")
    with pytest.raises(ConfigError):
        MfluxFlux2Provider()


def test_a_face_limit_needs_the_detector(monkeypatch):
    def missing():
        raise ConfigError("needs OpenCV 4")

    monkeypatch.setattr(faces, "ensure_available", missing)
    monkeypatch.setenv("PHOTOBOX_MAX_FACES", "2")
    with pytest.raises(ConfigError, match="OpenCV 4"):
        MfluxFlux2Provider()


@pytest.mark.parametrize("boxes,expected", [
    ([], 0),
    ([(0, 0, 100, 100)], 1),
    ([(0, 0, 100, 100), (300, 0, 100, 100)], 2),
    ([(0, 0, 100, 100), (10, 10, 100, 100)], 1),
    ([(0, 0, 100, 100), (40, 40, 40, 40)], 1),
    ([(0, 0, 100, 100), (95, 95, 100, 100), (400, 0, 80, 80)], 3),
])
def test_overlapping_boxes_count_as_one_face(boxes, expected):
    assert faces._distinct(boxes) == expected


def test_real_detector_finds_nothing_in_a_faceless_image(tmp_path):
    cv2 = pytest.importorskip("cv2")
    if not hasattr(cv2, "CascadeClassifier"):
        pytest.skip("needs OpenCV 4")
    path = tmp_path / "frame.png"
    scene().save(path)
    faces.ensure_available()
    assert faces.count_faces(path) == 0
