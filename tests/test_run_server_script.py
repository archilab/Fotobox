"""The start script finds its network interface and port in .env. No server is started."""
import os
import shutil
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "run_server.sh"


def run(tmp_path, env_file=None, **environment):
    (tmp_path / "scripts").mkdir()
    shutil.copy(SCRIPT, tmp_path / "scripts" / "run_server.sh")
    python = tmp_path / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text('#!/bin/sh\necho "$@"\n')
    python.chmod(0o755)
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "ipconfig").write_text('#!/bin/sh\necho "10.0.0.$(printf %s "$2" | tr -d en)"\n')
    (tools / "ipconfig").chmod(0o755)
    if env_file is not None:
        (tmp_path / ".env").write_text(env_file)
    clean = {k: v for k, v in os.environ.items() if k not in ("PHOTOBOX_INTERFACE", "PHOTOBOX_PORT")}
    clean["PATH"] = f"{tools}:{clean['PATH']}"
    result = subprocess.run(["bash", str(tmp_path / "scripts" / "run_server.sh")], capture_output=True, text=True, check=False, env=clean | environment)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_defaults_without_a_settings_file(tmp_path):
    assert "--host 10.0.0.0 --port 8880" in run(tmp_path)


def test_settings_file_chooses_interface_and_port(tmp_path):
    assert "--host 10.0.0.1 --port 9000" in run(tmp_path, "PHOTOBOX_INTERFACE=en1\nPHOTOBOX_PORT=9000\n")


def test_quotes_in_the_settings_file_are_ignored(tmp_path):
    assert "--host 10.0.0.1 " in run(tmp_path, 'PHOTOBOX_INTERFACE="en1"\n')


def test_the_environment_wins_over_the_file(tmp_path):
    assert "--host 10.0.0.2 " in run(tmp_path, "PHOTOBOX_INTERFACE=en1\n", PHOTOBOX_INTERFACE="en2")
