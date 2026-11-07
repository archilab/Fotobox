import json
import httpx
import pytest
from photobox_client import cli
from photobox_client.camera import CameraError

SESSION_ID = "11111111-1111-1111-1111-111111111111"
TOKEN = "test-token-value"


@pytest.fixture
def api(monkeypatch):
    """Replace the network with an in-memory fake and record the requests made."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v1/sessions":
            return httpx.Response(201, json={"id": SESSION_ID, "status": "awaiting_consent"})
        if request.url.path.endswith("/denied"):
            return httpx.Response(401, json={"detail": "valid device token required"})
        return httpx.Response(200, json={"status": "ok"})

    real_client = httpx.Client
    monkeypatch.setattr(cli.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setenv(cli.TOKEN_ENV, TOKEN)
    return requests


@pytest.fixture
def synthetic_file(tmp_path):
    path = tmp_path / "synthetic.jpg"
    path.write_bytes(b"synthetic test bytes")
    return path


@pytest.fixture
def camera_must_not_run(monkeypatch):
    def fail(*_):
        raise AssertionError("camera used")

    monkeypatch.setattr(cli, "capture_jpeg", fail)


def test_capture_without_attestation_stops_before_camera_or_network(api, synthetic_file, camera_must_not_run, capsys):
    for arguments in (["capture"], ["capture", "--file", str(synthetic_file)], ["capture", "--pool-consent"]):
        with pytest.raises(SystemExit) as caught:
            cli.main(arguments)
        assert caught.value.code == 2
    assert "--attest-documented-consent" in capsys.readouterr().err
    assert api == []


def test_capture_without_token_stops_before_camera_or_network(api, camera_must_not_run, monkeypatch, capsys):
    monkeypatch.delenv(cli.TOKEN_ENV)
    with pytest.raises(SystemExit) as caught:
        cli.main(["capture", "--attest-documented-consent"])
    assert caught.value.code == 2
    assert cli.TOKEN_ENV in capsys.readouterr().err
    assert api == []


@pytest.mark.parametrize("extra,pool", [([], False), (["--pool-consent"], True)])
def test_capture_with_attestation_sends_consent_and_keeps_pool_separate(api, synthetic_file, extra, pool):
    cli.main(["capture", "--file", str(synthetic_file), "--attest-documented-consent", "--no-process", *extra])
    consent = next(r for r in api if r.url.path.endswith("/consent"))
    assert json.loads(consent.content) == {"processing": True, "pool": pool, "documented_externally": True}


def test_every_capture_request_carries_the_token(api, synthetic_file, capsys):
    cli.main(["capture", "--file", str(synthetic_file), "--attest-documented-consent"])
    assert len(api) == 4
    assert all(r.headers["Authorization"] == f"Bearer {TOKEN}" for r in api)
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err


def test_token_file_takes_precedence_and_warns_when_too_open(api, tmp_path, capsys):
    token_file = tmp_path / "device.token"
    token_file.write_text("from-file\n")
    token_file.chmod(0o644)
    cli.main(["--token-file", str(token_file), "status", SESSION_ID])
    assert api[0].headers["Authorization"] == "Bearer from-file"
    assert "chmod 600" in capsys.readouterr().err
    token_file.chmod(0o600)
    cli.main(["--token-file", str(token_file), "status", SESSION_ID])
    assert "chmod 600" not in capsys.readouterr().err


def test_camera_settings_reach_the_camera(api, monkeypatch):
    seen = {}

    def fake_capture(camera, settings):
        seen.update(camera=camera, settings=settings)
        return b"jpeg"

    monkeypatch.setattr(cli, "capture_jpeg", fake_capture)
    cli.main(["capture", "--attest-documented-consent", "--no-process", "--camera", "usb-Acme", "--rotate", "90", "--width", "1920", "--height", "1080", "--jpeg-quality", "80", "--attempts", "5"])
    assert seen["camera"] == "usb-Acme"
    settings = seen["settings"]
    assert (settings.rotation, settings.width, settings.height, settings.jpeg_quality, settings.attempts) == (90, 1920, 1080, 80, 5)


def test_invalid_rotation_is_rejected(api):
    with pytest.raises(SystemExit):
        cli.main(["camera-test", "--rotate", "45"])


def test_camera_error_on_capture_is_clear_and_uploads_nothing(api, monkeypatch, capsys):
    def fail(*_):
        raise CameraError("Camera 7 could not be opened")

    monkeypatch.setattr(cli, "capture_jpeg", fail)
    with pytest.raises(SystemExit) as caught:
        cli.main(["capture", "--attest-documented-consent", "--camera", "7"])
    assert caught.value.code == 1
    assert "Camera 7 could not be opened" in capsys.readouterr().err
    assert api == []


def test_camera_test_counts_failures_and_saves_only_on_request(monkeypatch, tmp_path, capsys):
    results = iter([b"one", CameraError("no frame"), b"three"])

    def fake_capture(*_):
        value = next(results)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(cli, "capture_jpeg", fake_capture)
    save_dir = tmp_path / "saved"
    with pytest.raises(SystemExit) as caught:
        cli.main(["camera-test", "--count", "3", "--save-dir", str(save_dir)])
    assert caught.value.code == 1
    assert "Captures succeeded: 2/3" in capsys.readouterr().out
    assert sorted(p.name for p in save_dir.iterdir()) == ["camera-test-001.jpg", "camera-test-003.jpg"]


def test_camera_test_writes_nothing_without_save_dir(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "capture_jpeg", lambda *_: b"jpeg")
    cli.main(["camera-test", "--count", "2"])
    assert list(tmp_path.iterdir()) == []


def test_server_rejection_shows_detail_without_token(api, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["status", "denied"])
    assert caught.value.code == 1
    err = capsys.readouterr().err
    assert "401" in err and "valid device token required" in err
    assert TOKEN not in err


def test_other_commands_do_not_need_a_token(api, monkeypatch):
    monkeypatch.delenv(cli.TOKEN_ENV)
    cli.main(["health"])
    assert [r.url.path for r in api] == ["/api/v1/health"]
    assert "Authorization" not in api[0].headers
