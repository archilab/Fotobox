import io
import types
import pytest
from photobox_client import camera as cam
from photobox_client.camera import CameraError, CaptureSettings, capture_jpeg, list_stable_paths, probe_camera, resolve_source


class FakeFrame:
    size = 100

    def __init__(self, rotation=None):
        self.rotation = rotation


class FakeCapture:
    """Stands in for cv2.VideoCapture; failures are scripted per opening of the device."""
    instances: list["FakeCapture"] = []
    open_results: list[bool] = []
    read_results: list[bool] = []
    properties: dict[int, float] = {}

    def __init__(self, source, backend):
        self.source, self.backend, self.released, self.sets = source, backend, False, []
        self.opened = FakeCapture.open_results.pop(0) if FakeCapture.open_results else True
        self.read_ok = FakeCapture.read_results.pop(0) if FakeCapture.read_results else True
        self.reads = 0
        FakeCapture.instances.append(self)

    def isOpened(self):
        return self.opened

    def read(self):
        self.reads += 1
        return (True, FakeFrame()) if self.read_ok else (False, None)

    def set(self, name, value):
        self.sets.append((name, value))
        FakeCapture.properties[name] = value
        return True

    def get(self, name):
        return FakeCapture.properties.get(name, -1)

    def getBackendName(self):
        return "FAKE"

    def release(self):
        self.released = True


@pytest.fixture
def fake_cv2(monkeypatch):
    FakeCapture.instances, FakeCapture.open_results, FakeCapture.read_results = [], [], []
    FakeCapture.properties = {}
    module = types.SimpleNamespace(
        VideoCapture=FakeCapture, CAP_V4L2=200, CAP_ANY=0,
        CAP_PROP_FRAME_WIDTH=3, CAP_PROP_FRAME_HEIGHT=4, CAP_PROP_FPS=5, CAP_PROP_FOURCC=6,
        CAP_PROP_AUTOFOCUS=39, CAP_PROP_FOCUS=28, CAP_PROP_AUTO_EXPOSURE=21, CAP_PROP_EXPOSURE=15,
        ROTATE_90_CLOCKWISE=0, ROTATE_180=1, ROTATE_90_COUNTERCLOCKWISE=2, IMWRITE_JPEG_QUALITY=1,
        VideoWriter_fourcc=lambda *chars: sum(ord(c) << (8 * i) for i, c in enumerate(chars)),
        rotate=lambda frame, code: FakeFrame(rotation=code),
        imencode=lambda ext, frame, params: (True, types.SimpleNamespace(tobytes=lambda: b"jpeg", frame=frame, params=params)),
    )
    monkeypatch.setattr(cam, "load_cv2", lambda: module)
    monkeypatch.setattr(cam.time, "sleep", lambda _: None)
    return module


def test_index_is_used_directly():
    assert resolve_source("2") == 2


def test_unknown_stable_name_lists_what_exists(tmp_path):
    (tmp_path / "usb-Acme_Cam-video-index0").touch()
    with pytest.raises(CameraError, match="usb-Acme_Cam-video-index0"):
        resolve_source("usb-Other", by_id_dir=tmp_path)


def test_stable_name_resolves_to_real_device(tmp_path):
    device = tmp_path / "video3"
    device.touch()
    by_id = tmp_path / "by-id"
    by_id.mkdir()
    (by_id / "usb-Acme_Cam-video-index0").symlink_to(device)
    assert resolve_source("usb-Acme_Cam-video-index0", by_id_dir=by_id) == str(device.resolve())


@pytest.mark.parametrize("value", ["/dev/does-not-exist-photobox", "../video0", "some/path"])
def test_invalid_camera_values_are_rejected(value):
    with pytest.raises(CameraError):
        resolve_source(value)


def test_missing_by_id_folder_gives_empty_list(tmp_path):
    assert list_stable_paths(tmp_path / "missing") == []


def test_capture_returns_jpeg_and_releases_camera(fake_cv2):
    assert capture_jpeg("0") == b"jpeg"
    assert len(FakeCapture.instances) == 1
    assert FakeCapture.instances[0].released
    assert FakeCapture.instances[0].reads == CaptureSettings().warmup_frames


def test_invalid_name_fails_without_opening_a_camera(fake_cv2):
    with pytest.raises(CameraError):
        capture_jpeg("/dev/does-not-exist-photobox")
    assert FakeCapture.instances == []


def test_retry_recovers_from_failed_open_and_read(fake_cv2):
    FakeCapture.open_results = [False, True, True]
    FakeCapture.read_results = [True, False, True]
    assert capture_jpeg("0") == b"jpeg"
    assert len(FakeCapture.instances) == 3
    assert all(c.released for c in FakeCapture.instances)


def test_gives_up_after_configured_attempts_and_releases_every_camera(fake_cv2):
    FakeCapture.read_results = [False, False]
    with pytest.raises(CameraError, match="after 2 attempts"):
        capture_jpeg("0", CaptureSettings(attempts=2))
    assert len(FakeCapture.instances) == 2
    assert all(c.released for c in FakeCapture.instances)


def test_camera_is_released_when_encoding_crashes(fake_cv2):
    def crash(*_):
        raise RuntimeError("encoder crashed")

    fake_cv2.imencode = crash
    with pytest.raises(RuntimeError):
        capture_jpeg("0")
    assert FakeCapture.instances[0].released


def test_size_request_selects_mjpg(fake_cv2):
    capture_jpeg("0", CaptureSettings(width=1920, height=1080))
    assert FakeCapture.instances[0].sets == [(6, fake_cv2.VideoWriter_fourcc(*"MJPG")), (3, 1920), (4, 1080)]


@pytest.mark.parametrize("settings", [CaptureSettings(rotation=45), CaptureSettings(jpeg_quality=0), CaptureSettings(jpeg_quality=101), CaptureSettings(attempts=0), CaptureSettings(warmup_frames=0)])
def test_invalid_settings_are_rejected(fake_cv2, settings):
    with pytest.raises(CameraError):
        capture_jpeg("0", settings)


def test_probe_reports_settings_marks_unsupported_controls_and_releases(fake_cv2):
    FakeCapture.properties = {3: 640, 4: 480, 5: 30.0, 6: fake_cv2.VideoWriter_fourcc(*"YUYV"), 39: 1.0}
    report = probe_camera("0")
    assert report["width"] == 640 and report["height"] == 480 and report["fps"] == 30.0
    assert report["format"] == "YUYV" and report["autofocus"] == 1.0 and report["focus"] is None
    assert report["frame_read"] is True and report["accepted_sizes"] == ["640x480", "1280x720", "1920x1080", "2560x1440", "3840x2160"]
    assert FakeCapture.instances[0].released


def test_probe_of_unavailable_camera_gives_clear_error(fake_cv2):
    FakeCapture.open_results = [False]
    with pytest.raises(CameraError, match="could not be opened"):
        probe_camera("0")
    assert FakeCapture.instances[0].released


class RealFrameCapture(FakeCapture):
    """Serves a synthetic landscape frame: left half red, right half blue."""

    def read(self):
        import numpy
        frame = numpy.zeros((200, 400, 3), dtype=numpy.uint8)
        frame[:, :200] = (0, 0, 255)
        frame[:, 200:] = (255, 0, 0)
        return True, frame


@pytest.mark.parametrize("rotation,size,red_probe", [
    (0, (400, 200), (50, 100)),
    (90, (200, 400), (100, 50)),
    (180, (400, 200), (350, 100)),
    (270, (200, 400), (100, 350)),
])
def test_real_opencv_rotation_and_jpeg_output(monkeypatch, rotation, size, red_probe):
    cv2 = pytest.importorskip("cv2")
    Image = pytest.importorskip("PIL.Image")
    module = types.SimpleNamespace(
        VideoCapture=RealFrameCapture, CAP_V4L2=cv2.CAP_V4L2, CAP_ANY=cv2.CAP_ANY,
        rotate=cv2.rotate, imencode=cv2.imencode, IMWRITE_JPEG_QUALITY=cv2.IMWRITE_JPEG_QUALITY,
        ROTATE_90_CLOCKWISE=cv2.ROTATE_90_CLOCKWISE, ROTATE_180=cv2.ROTATE_180, ROTATE_90_COUNTERCLOCKWISE=cv2.ROTATE_90_COUNTERCLOCKWISE,
    )
    monkeypatch.setattr(cam, "load_cv2", lambda: module)
    with Image.open(io.BytesIO(capture_jpeg("0", CaptureSettings(rotation=rotation, warmup_frames=1)))) as decoded:
        assert decoded.format == "JPEG" and decoded.size == size
        red, _, blue = decoded.convert("RGB").getpixel(red_probe)
        assert red > 200 and blue < 60
