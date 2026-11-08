"""The Mac installer, run against stand-ins. Nothing is installed, downloaded or started."""
import os
import pty
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent


def tool(folder, name, body):
    path = folder / name
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(0o755)


class Mac:
    def __init__(self, tmp_path, folder="photobox-server"):
        base = tmp_path.resolve()
        self.home = base / "home"
        self.repository = self.home / folder
        (self.repository / "scripts").mkdir(parents=True)
        self.home.mkdir(exist_ok=True)
        shutil.copy(ROOT / "scripts" / "setup_mac.sh", self.repository / "scripts")
        shutil.copy(ROOT / ".env.example", self.repository)
        shutil.copy(ROOT / "consent-notice.example.txt", self.repository)
        shutil.copytree(ROOT / "deploy" / "mac", self.repository / "deploy" / "mac")
        self.log = base / "calls.log"
        self.log.touch()
        self.tools = base / "tools"
        self.tools.mkdir()
        self.model = self.home / ".cache/huggingface/hub/models--black-forest-labs--FLUX.2-klein-4B"
        log = f'echo "$(basename "$0") $*" >> {self.log}'
        python = (f'#!/bin/sh\nif [ "$1" = "-m" ]; then echo "python $*" >> {self.log}; exit 0; fi\n'
                  f'PYTHONPATH={ROOT / "src"} exec {sys.executable} "$@"\n')
        flux = f'{log}\nmkdir -p {self.model}/snapshots\nwhile [ "$#" -gt 1 ]; do [ "$1" = --output ] && touch "$2"; shift; done'
        tool(self.tools, "uname", 'echo "${FAKE_ARCH:-arm64}"')
        tool(self.tools, "python3", f'if [ "$1" = -c ]; then [ -z "$FAKE_OLD_PYTHON" ]; exit $?; fi\n{log}\nmkdir -p .venv/bin\ncat > .venv/bin/python <<\'STUB\'\n{python}STUB\nchmod +x .venv/bin/python')
        tool(self.tools, "uv", f'{log}\nmkdir -p "$HOME/.local/bin"\nprintf "#!/bin/sh\\n" > "$HOME/.local/bin/mflux-generate-flux2-edit"\nchmod +x "$HOME/.local/bin/mflux-generate-flux2-edit"\ncat > "$HOME/.local/bin/mflux-generate-flux2" <<\'STUB\'\n#!/bin/sh\n{flux}\nSTUB\nchmod +x "$HOME/.local/bin/mflux-generate-flux2"')
        tool(self.tools, "route", '[ -n "$FAKE_OFFLINE" ] || echo "   interface: en7"')
        tool(self.tools, "ipconfig", '[ -n "$FAKE_OFFLINE" ] || echo 10.0.0.5')
        tool(self.tools, "tmutil", log)
        tool(self.tools, "open", f'{log}\nif [ "$1" = -e ] && [ -n "$FAKE_EDIT" ]; then sed -i "" "s/TODO-OPERATOR/filled/g" "$2"; fi')
        tool(self.tools, "curl", f'{log}\n[ -z "$FAKE_UNHEALTHY" ]')
        tool(self.tools, "sleep", ":")
        marker = base / "loaded"
        tool(self.tools, "launchctl", f'{log}\ncase "$1" in print) [ -f {marker}-"$(basename "$2")" ];; bootstrap) touch {marker}-"$(basename "$3" .plist)";; esac')

    def environment(self, extra):
        base = {k: v for k, v in os.environ.items() if not k.startswith("PHOTOBOX_")}
        return base | {"HOME": str(self.home), "PATH": f"{self.tools}:/usr/bin:/bin"} | extra

    def run(self, *arguments, **extra):
        return subprocess.run(["bash", str(self.repository / "scripts" / "setup_mac.sh"), *arguments], capture_output=True, text=True, check=False,
                              stdin=subprocess.DEVNULL, env=self.environment(extra), timeout=60)

    def run_at_terminal(self, answers, **extra):
        leader, terminal = pty.openpty()
        os.write(leader, answers.encode())
        try:
            return subprocess.run(["bash", str(self.repository / "scripts" / "setup_mac.sh")], capture_output=True, text=True, check=False,
                                  stdin=terminal, env=self.environment(extra), timeout=60)
        finally:
            os.close(terminal)
            os.close(leader)

    def calls(self):
        return self.log.read_text()

    @property
    def env_file(self):
        return self.repository / ".env"


@pytest.fixture
def mac(tmp_path):
    return Mac(tmp_path)


def test_a_new_mac_gets_program_tool_model_settings_and_services(mac):
    result = mac.run()
    assert result.returncode == 0, result.stderr
    calls = mac.calls()
    assert "pip install --quiet -e .[server-ai]" in calls
    assert "uv tool install --python 3.12 mflux==0.24.0" in calls
    assert "mflux-generate-flux2 --model flux2-klein-4b" in calls
    assert "tmutil addexclusion storage" in calls
    assert calls.count("launchctl bootstrap") == 2
    assert not (mac.home / ".photobox-model-test.png").exists()
    assert "setup_pi.sh admin@fotobox.local --server 10.0.0.5 --reboot" in result.stdout


def test_settings_use_the_real_model_and_stay_private(mac):
    mac.run()
    text = mac.env_file.read_text()
    assert "PHOTOBOX_PROVIDER=mflux-flux2" in text and "PHOTOBOX_DEVELOPER_MODE=false" in text
    assert f"PHOTOBOX_MFLUX_COMMAND={mac.home}/.local/bin/mflux-generate-flux2-edit" in text
    assert "PHOTOBOX_INTERFACE=en7" in text
    assert oct(mac.env_file.stat().st_mode)[-3:] == "600"
    assert oct((mac.repository / "storage").stat().st_mode)[-3:] == "700"


def test_service_files_name_this_folder(mac):
    mac.run()
    text = (mac.home / "Library/LaunchAgents/com.photobox.server.plist").read_text()
    assert f"{mac.repository}/scripts/run_server.sh" in text and "REPOSITORY" not in text


def test_without_a_terminal_nothing_is_attested_and_the_rest_is_listed(mac):
    """Rights and the consent text are a person's statements, never a script's."""
    out = mac.run().stdout
    assert "--attest-rights" not in mac.calls() and "set-password" not in mac.calls()
    assert "Still to do:" in out and "Consent notice" in out and "Counterparts" in out and "Dashboard password" in out
    assert "TODO-OPERATOR" in (mac.repository / "consent-notice.txt").read_text()


def test_at_a_terminal_a_person_completes_the_three_steps(mac):
    result = mac.run_at_terminal("\nyes\n", FAKE_EDIT="1")
    assert result.returncode == 0, result.stderr
    calls = mac.calls()
    assert calls.count("seed add") == 2 and "--rights-reference FICTIONAL-AI --attest-rights" in calls
    assert "dashboard set-password" in calls
    assert not (mac.home / "counterpart-1.png").exists()
    assert "Still to do" not in result.stdout


def test_only_the_word_yes_confirms_the_rights(mac):
    result = mac.run_at_terminal("\ny\n", FAKE_EDIT="1")
    assert "--attest-rights" not in mac.calls()
    assert (mac.home / "counterpart-1.png").exists() and "Counterparts: none were added" in result.stdout


def test_a_notice_with_placeholders_does_not_count(mac):
    out = mac.run_at_terminal("\nyes\n").stdout
    assert "Consent notice: replace both" in out


def test_running_again_restarts_nothing_and_keeps_settings(mac):
    mac.run()
    mac.env_file.write_text(mac.env_file.read_text() + "PHOTOBOX_NUM_FRAMES=3\n")
    before = mac.env_file.read_text()
    mac.log.write_text("")
    assert mac.run().returncode == 0
    calls = mac.calls()
    for call in ("bootstrap", "bootout", "uv tool", "mflux-generate", "python3 "):
        assert call not in calls
    assert mac.env_file.read_text() == before


def test_an_interrupted_model_download_is_continued(mac):
    mac.run()
    (mac.model / "blobs").mkdir(parents=True)
    (mac.model / "blobs" / "abc.incomplete").touch()
    mac.log.write_text("")
    result = mac.run()
    assert "mflux-generate-flux2" in mac.calls() and result.returncode == 1 and "did not finish" in result.stderr


def test_a_changed_service_file_replaces_the_service(mac):
    mac.run()
    target = mac.home / "Library/LaunchAgents/com.photobox.server.plist"
    target.write_text("old")
    mac.log.write_text("")
    mac.run()
    assert mac.calls().count("launchctl bootout") == 1 and mac.calls().count("launchctl bootstrap") == 1


def test_check_changes_nothing(mac):
    result = mac.run("--check")
    assert result.returncode == 0, result.stderr
    assert "would be downloaded" in result.stdout and "Nothing was changed" in result.stdout
    assert mac.calls().count("launchctl print") == 2
    assert "bootstrap" not in mac.calls()
    assert not mac.env_file.exists() and not (mac.repository / "storage").exists() and not (mac.repository / ".venv").exists()


def test_check_reports_an_installed_mac(mac):
    mac.run()
    out = mac.run("--check").stdout
    assert "Settings: there" in out and "Service server: running" in out and "Consent notice ready: no" in out and "Counterparts: 0" in out


@pytest.mark.parametrize("extra,message", [({"FAKE_ARCH": "x86_64"}, "Apple Silicon"), ({"FAKE_OLD_PYTHON": "1"}, "3.11"), ({"FAKE_OFFLINE": "1"}, "no network")])
def test_stops_before_changing_anything(mac, extra, message):
    result = mac.run(**extra)
    assert result.returncode == 1 and message in result.stderr
    assert mac.calls() == "" and not mac.env_file.exists()


def test_an_existing_environment_does_not_need_a_recent_system_python(mac):
    mac.run()
    result = mac.run("--check", FAKE_OLD_PYTHON="1")
    assert result.returncode == 0 and "Python environment: there" in result.stdout


def test_refuses_folders_background_services_cannot_read(tmp_path):
    mac = Mac(tmp_path, "Documents/photobox")
    result = mac.run()
    assert result.returncode == 1 and "~/photobox-server" in result.stderr and mac.calls() == ""


def test_a_silent_server_is_reported(mac):
    result = mac.run(FAKE_UNHEALTHY="1")
    assert result.returncode == 1 and "did not answer" in result.stderr


def test_unknown_options_stop(mac):
    assert mac.run("--nonsense").returncode == 1 and mac.calls() == ""
