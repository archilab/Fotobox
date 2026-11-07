import json
import httpx
import pytest
from photobox_client import cli

SESSION_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def api(monkeypatch):
    """Replace the network with an in-memory fake and record the requests made."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v1/sessions":
            return httpx.Response(201, json={"id": SESSION_ID, "status": "awaiting_consent"})
        return httpx.Response(200, json={"status": "ok"})

    real_client = httpx.Client
    monkeypatch.setattr(cli.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    return requests


@pytest.fixture
def synthetic_file(tmp_path):
    path = tmp_path / "synthetic.jpg"
    path.write_bytes(b"synthetic test bytes")
    return path


def test_capture_without_attestation_stops_before_camera_or_network(api, synthetic_file, monkeypatch, capsys):
    def camera_must_not_run(_index):
        raise AssertionError("camera used without attestation")

    monkeypatch.setattr(cli, "take_photo", camera_must_not_run)
    for arguments in (["capture"], ["capture", "--file", str(synthetic_file)], ["capture", "--pool-consent"]):
        with pytest.raises(SystemExit) as caught:
            cli.main(arguments)
        assert caught.value.code == 2
    assert "--attest-documented-consent" in capsys.readouterr().err
    assert api == []


@pytest.mark.parametrize("extra,pool", [([], False), (["--pool-consent"], True)])
def test_capture_with_attestation_sends_consent_and_keeps_pool_separate(api, synthetic_file, extra, pool):
    cli.main(["capture", "--file", str(synthetic_file), "--attest-documented-consent", "--no-process", *extra])
    consent = next(r for r in api if r.url.path.endswith("/consent"))
    assert json.loads(consent.content) == {"processing": True, "pool": pool, "documented_externally": True}


def test_other_commands_do_not_need_attestation(api):
    cli.main(["health"])
    assert [r.url.path for r in api] == ["/api/v1/health"]
