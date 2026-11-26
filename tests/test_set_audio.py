"""The booth's sound setting for the video link (ADR-045, deploy/pi/set-audio.sh)."""
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "deploy" / "pi" / "set-audio.sh"


def run(tmp_path, *arguments):
    return subprocess.run(["sh", str(SCRIPT), *arguments], capture_output=True, text=True, env={**os.environ, "HOME": str(tmp_path)})


def test_hdmi_playback_and_webcam_recording(tmp_path):
    result = run(tmp_path, "vc4hdmi0", "BRIO")
    assert result.returncode == 0, result.stderr
    text = (tmp_path / ".asoundrc").read_text()
    assert 'slave.pcm "hdmi:CARD=vc4hdmi0,DEV=0"' in text and 'slave.pcm "hw:CARD=BRIO,DEV=0"' in text
    assert "type asym" in text and oct((tmp_path / ".asoundrc").stat().st_mode)[-3:] == "600"


def test_a_usb_speaker_is_used_directly(tmp_path):
    assert run(tmp_path, "Speaker", "BRIO").returncode == 0
    assert 'slave.pcm "hw:CARD=Speaker,DEV=0"' in (tmp_path / ".asoundrc").read_text()


def test_odd_names_and_missing_cards_change_nothing(tmp_path):
    for arguments in (("vc4hdmi0",), ('x"; rm -rf /', "BRIO"), ("vc4hdmi0", "")):
        assert run(tmp_path, *arguments).returncode == 2
    assert not (tmp_path / ".asoundrc").exists()
