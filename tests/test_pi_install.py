"""The Pi installer and its Mac wrapper, run against stand-ins. Nothing is installed or downloaded."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
INSTALLER = ROOT / "deploy" / "pi" / "install.sh"
SOURCE = INSTALLER.read_text()
WHISPER_HASH = re.search(r"WHISPER_MODEL_SHA256=(\w+)", SOURCE).group(1)
PERSON_HASH = re.search(r"PERSON_MODEL_SHA256=(\w+)", SOURCE).group(1)
COMMIT = re.search(r"WHISPER_COMMIT=(\w+)", SOURCE).group(1)


def tool(folder, name, body):
    path = folder / name
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(0o755)


class Pi:
    def __init__(self, tmp_path):
        self.home = tmp_path.resolve() / "home"
        self.repository = self.home / "photobox"
        (self.repository / "deploy" / "pi").mkdir(parents=True)
        self.script = self.repository / "deploy" / "pi" / "install.sh"
        shutil.copy(INSTALLER, self.script)
        self.log = tmp_path.resolve() / "calls.log"
        self.log.touch()
        self.tools = tmp_path.resolve() / "tools"
        self.tools.mkdir()
        log = f'echo "$0 $*" >> {self.log}'
        python = (f'#!/bin/sh\nif [ "$1" = "-m" ]; then echo "python $*" >> {self.log}; exit 0; fi\n'
                  f'PYTHONPATH={ROOT / "src"} exec {sys.executable} "$@"\n')
        tool(self.tools, "uname", 'echo "${FAKE_ARCH:-aarch64}"')
        tool(self.tools, "id", 'echo "${FAKE_USER:-admin}"')
        tool(self.tools, "getent", '[ -z "$FAKE_OFFLINE" ]')
        tool(self.tools, "sudo", log)
        tool(self.tools, "python3", f'{log}\nmkdir -p .venv/bin\ncat > .venv/bin/python <<\'STUB\'\n{python}STUB\nchmod +x .venv/bin/python')
        tool(self.tools, "git", f'{log}\nif [ "$1" = clone ]; then for last; do :; done; mkdir -p "$last"; else echo "${{FAKE_COMMIT:-{COMMIT}}}"; fi')
        tool(self.tools, "cmake", f'{log}\nif [ "$1" = --build ]; then mkdir -p build-static/bin; echo program > build-static/bin/whisper-cli; fi')
        tool(self.tools, "curl", f'{log}\nwhile [ "$#" -gt 1 ]; do [ "$1" = -o ] && out="$2"; shift; done\n'
                                 'case "$1" in *ggml*) c=whisper;; *) c=person;; esac\n[ -z "$FAKE_CORRUPT" ] || c=corrupt\necho $c > "$out"')
        tool(self.tools, "sha256sum", f'case "$(cat "$1")" in whisper) h={WHISPER_HASH};; person) h={PERSON_HASH};; *) h=0000;; esac\necho "$h  $1"')

    def run(self, *arguments, script=None, **environment):
        env = {k: v for k, v in os.environ.items()} | {"HOME": str(self.home), "PATH": f"{self.tools}:{os.environ['PATH']}"} | environment
        return subprocess.run(["bash", str(script or self.script), *arguments], capture_output=True, text=True, check=False, env=env)

    def calls(self):
        return self.log.read_text()

    @property
    def settings(self):
        return self.home / ".photobox-kiosk.env"


@pytest.fixture
def pi(tmp_path):
    return Pi(tmp_path)


def installed(pi):
    return pi.run()


def test_voice_and_person_detection_are_part_of_the_plain_install(pi):
    result = installed(pi)
    assert result.returncode == 0, result.stderr
    assert (pi.home / "photobox-speech" / "whisper-cli").read_text() == "program\n"
    assert (pi.home / "photobox-speech" / "ggml-base.bin").read_text() == "whisper\n"
    assert (pi.home / "photobox-person" / "nanodet.onnx").read_text() == "person\n"
    calls = pi.calls()
    for package in ("alsa-utils", "cmake", "chromium", "cage", "polkitd"):
        assert package in calls
    assert "install-autonomy.sh" in calls and "--branch v1.7.4" in calls


def test_private_folders_for_models(pi):
    installed(pi)
    for folder in ("photobox-speech", "photobox-person"):
        assert oct((pi.home / folder).stat().st_mode)[-3:] == "700"


def test_nothing_is_switched_on(pi):
    """ADR-029: detection runs only when the operator switches it on."""
    assert pi.run("--server", "192.168.1.27").returncode == 0
    text = pi.settings.read_text()
    assert "PHOTOBOX_PERSON_DETECTION=false" in text and "PHOTOBOX_MICROPHONE=\n" in text
    assert "PHOTOBOX_SERVER=http://192.168.1.27:8880" in text
    assert oct(pi.settings.stat().st_mode)[-3:] == "600"


def test_no_settings_file_without_a_server(pi):
    installed(pi)
    assert not pi.settings.exists()


def test_a_later_run_keeps_the_operators_settings(pi):
    pi.settings.write_text("PHOTOBOX_SERVER=http://10.0.0.1:8880\nPHOTOBOX_CAMERA=/dev/v4l/by-id/cam\nPHOTOBOX_MICROPHONE=NT\nPHOTOBOX_PERSON_DETECTION=true\nPHOTOBOX_PERSON_AREA=0.1,0,0.9,1\n")
    assert pi.run("--server", "10.0.0.2").returncode == 0
    text = pi.settings.read_text()
    for kept in ("10.0.0.2", "/dev/v4l/by-id/cam", "PHOTOBOX_MICROPHONE=NT", "PHOTOBOX_PERSON_DETECTION=true", "0.1,0,0.9,1"):
        assert kept in text


def test_running_twice_does_not_build_or_download_again(pi):
    installed(pi)
    pi.log.write_text("")
    assert installed(pi).returncode == 0
    for tool_call in ("/git clone", "/curl ", "/python3 "):
        assert tool_call not in pi.calls()


def test_a_corrupt_download_is_discarded(pi):
    result = pi.run(FAKE_CORRUPT="1")
    assert result.returncode == 1 and "checksum" in result.stderr
    speech = pi.home / "photobox-speech"
    assert not (speech / "ggml-base.bin").exists() and not (speech / "ggml-base.bin.part").exists()


def test_an_unexpected_source_version_is_refused_and_cleaned_up(pi):
    result = pi.run(FAKE_COMMIT="deadbeef")
    assert result.returncode == 1 and "expected version" in result.stderr
    assert not (pi.home / "photobox-speech" / "whisper-cli").exists()
    assert list(pi.home.glob(".whisper-build.*")) == []


@pytest.mark.parametrize("environment,message", [({"FAKE_ARCH": "armv7l"}, "64-bit"), ({"FAKE_USER": "pi"}, "user admin"), ({"FAKE_OFFLINE": "1"}, "internet")])
def test_stops_before_changing_anything(pi, environment, message):
    result = pi.run(**environment)
    assert result.returncode == 1 and message in result.stderr
    assert pi.calls() == ""


def test_the_code_must_be_in_the_expected_folder(pi, tmp_path):
    other = tmp_path / "elsewhere" / "deploy" / "pi"
    other.mkdir(parents=True)
    shutil.copy(INSTALLER, other / "install.sh")
    result = pi.run(script=other / "install.sh")
    assert result.returncode == 1 and "~/photobox" in result.stderr


def test_check_only_reports(pi):
    result = pi.run("--check", "--server", "10.0.0.2")
    assert result.returncode == 0, result.stderr
    assert "would be built" in result.stdout and "Nothing was changed" in result.stdout
    assert "apt-get" not in pi.calls() and not (pi.home / "photobox-speech").exists() and not pi.settings.exists()


def test_check_recognises_an_installed_box(pi):
    installed(pi)
    out = pi.run("--check").stdout
    assert "Voice model: correct" in out and "Person model: correct" in out and "Voice program: there" in out


def test_restarts_only_when_asked(pi):
    installed(pi)
    assert "reboot" not in pi.calls()
    pi.run("--reboot")
    assert "systemctl reboot" in pi.calls()


@pytest.mark.parametrize("arguments", [["--nonsense"], ["--server"], ["--server", ""]])
def test_bad_options_stop_early(pi, arguments):
    assert pi.run(*arguments).returncode == 1 and pi.calls() == ""


def wrapper(tmp_path, *arguments, ssh_fails=False):
    folder = tmp_path.resolve() / "mac"
    (folder / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "setup_pi.sh", folder / "scripts" / "setup_pi.sh")
    tools = tmp_path.resolve() / "mactools"
    tools.mkdir()
    log = tmp_path.resolve() / "mac.log"
    tool(tools, "git", "echo README.md")
    tool(tools, "tar", "echo archive")
    tool(tools, "ssh", f'cat > /dev/null; printf "%s\\n" "$*" >> {log}\n{"exit 7" if ssh_fails else ""}')
    result = subprocess.run(["bash", str(folder / "scripts" / "setup_pi.sh"), *arguments], capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL, env=os.environ | {"PATH": f"{tools}:{os.environ['PATH']}"})
    return result, log.read_text().splitlines() if log.exists() else []


def test_wrapper_copies_then_installs_with_the_options(tmp_path):
    result, calls = wrapper(tmp_path, "admin@fotobox.local", "--server", "192.168.1.27", "--reboot")
    assert result.returncode == 0, result.stderr
    assert "tar -x -C ~/photobox" in calls[0]
    assert calls[1] == "-t admin@fotobox.local bash ~/photobox/deploy/pi/install.sh --server 192.168.1.27 --reboot"


def test_wrapper_stops_when_copying_fails(tmp_path):
    result, calls = wrapper(tmp_path, "admin@fotobox.local", ssh_fails=True)
    assert result.returncode != 0 and len(calls) == 1


def test_wrapper_needs_an_address(tmp_path):
    assert wrapper(tmp_path)[0].returncode == 2
    (tmp_path / "second").mkdir()
    assert wrapper(tmp_path / "second", "--reboot")[0].returncode == 2
