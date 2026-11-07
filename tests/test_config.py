import os
import pytest
from fastapi.testclient import TestClient
from photobox_server.app import app
from photobox_server.config import ConfigError, get_provider, load_env_file


def test_env_file_loads_values_and_skips_comments(tmp_path, monkeypatch):
    monkeypatch.delenv("PHOTOBOX_NUM_FRAMES", raising=False)
    monkeypatch.delenv("PHOTOBOX_STORAGE_DIR", raising=False)
    env = tmp_path / ".env"
    env.write_text('# comment\n\nPHOTOBOX_NUM_FRAMES=3\nPHOTOBOX_STORAGE_DIR="/tmp/with space"\n')
    load_env_file(env)
    assert os.environ["PHOTOBOX_NUM_FRAMES"] == "3"
    assert os.environ["PHOTOBOX_STORAGE_DIR"] == "/tmp/with space"


def test_shell_environment_overrides_env_file(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOBOX_NUM_FRAMES", "4")
    env = tmp_path / ".env"
    env.write_text("PHOTOBOX_NUM_FRAMES=3\n")
    load_env_file(env)
    assert os.environ["PHOTOBOX_NUM_FRAMES"] == "4"


def test_missing_env_file_is_allowed(tmp_path):
    load_env_file(tmp_path / "missing.env")


def test_malformed_env_file_error_does_not_reveal_line_contents(tmp_path):
    env = tmp_path / ".env"
    env.write_text("secret-without-equals\n")
    with pytest.raises(ConfigError) as caught:
        load_env_file(env)
    assert "line 1" in str(caught.value)
    assert "secret-without-equals" not in str(caught.value)


def test_provider_defaults_to_mock(monkeypatch):
    monkeypatch.delenv("PHOTOBOX_PROVIDER", raising=False)
    assert get_provider() == "mock"


@pytest.mark.parametrize("provider", ["comfyui", "mlx", ""])
def test_unimplemented_provider_is_rejected(monkeypatch, provider):
    monkeypatch.setenv("PHOTOBOX_PROVIDER", provider)
    with pytest.raises(ConfigError):
        get_provider()


def test_server_refuses_to_start_with_unimplemented_provider(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PHOTOBOX_STORAGE_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PHOTOBOX_PROVIDER", "comfyui")
    with pytest.raises(ConfigError):
        with TestClient(app):
            pass


def test_server_reads_env_file_at_startup_and_reports_provider(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PHOTOBOX_PROVIDER", raising=False)
    monkeypatch.delenv("PHOTOBOX_STORAGE_DIR", raising=False)
    monkeypatch.delenv("PHOTOBOX_DB_PATH", raising=False)
    (tmp_path / ".env").write_text(f"PHOTOBOX_PROVIDER=mock\nPHOTOBOX_STORAGE_DIR={tmp_path / 'from-env-file'}\n")
    with TestClient(app) as client:
        assert client.get("/api/v1/health").json()["provider"] == "mock"
    assert (tmp_path / "from-env-file" / "photobox.sqlite3").is_file()
