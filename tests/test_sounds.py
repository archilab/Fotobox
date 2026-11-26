"""Soft sounds at a few moments (ADR-047): which screen carries which sound, and when the booth stays silent."""
from __future__ import annotations

import re
from pathlib import Path

import httpx

from photobox_client import views
from photobox_client.kiosk import SILENT_SCREENS, Kiosk
from photobox_client.settings import Settings
from photobox_client.webui import WebUI

UI = Path(__file__).parent.parent / "src" / "photobox_client" / "ui"


class Screen:
    def __init__(self):
        self.states = []

    def view(self, state):
        self.states.append(state)

    def show(self, text):
        self.states.append({"screen": "menu", "text": text})


def kiosk(volume):
    screen = Screen()
    return Kiosk(None, lambda: b"", screen, None, volume=volume), screen


def test_the_moments_carry_their_sounds():
    assert views.countdown(3, "3")["sound"] == "count"
    assert views.flash()["sound"] == "shutter"
    assert views.strip("strip/1", None)["sound"] == "ready"
    assert views.video_request("Essen", 20, 120)["sound"] == "call"
    invitation = {"generated_at": "2026-11-26T10:00:00+00:00",
                  "message": {"key": "partner_waiting", "until": "2026-11-26T10:00:20+00:00",
                              "texts": {"de": {"hero": "H", "support": "S"}, "en": {"hero": "H", "support": "S"}}},
                  "locations": [{"label": "Essen", "invite_until": "2026-11-26T10:00:20+00:00"}]}
    assert views.start(invitation, "plain")["sound"] == "call"


def test_quiet_screens_carry_no_sound():
    assert "sound" not in views.start(None, "plain")
    assert "sound" not in views.resting()
    assert "sound" not in views.listening(10, 30, "plain")
    assert "sound" not in views.video_link("Essen", 120)
    assert "sound" not in views.message("text", key=True)


def test_the_kiosk_passes_sound_and_volume_to_the_page():
    booth, screen = kiosk(60)
    booth.present(views.countdown(2, "2"))
    assert (screen.states[-1]["sound"], screen.states[-1]["volume"]) == ("count", 60)
    booth.present(views.start(None, "plain"))
    assert "sound" not in screen.states[-1] and screen.states[-1]["volume"] == 60  # for the press tick


def test_switched_off_the_page_gets_neither_sound_nor_volume():
    booth, screen = kiosk(0)
    booth.present(views.flash())
    assert "sound" not in screen.states[-1] and "volume" not in screen.states[-1]


def test_silent_screens_get_no_sound_and_no_volume_even_if_asked():
    booth, screen = kiosk(90)
    for state in (views.resting(), views.listening(10, 30, "plain"), views.video_link("Essen", 120)):
        booth.present(state | {"sound": "ready"})
        assert "sound" not in screen.states[-1] and "volume" not in screen.states[-1], state["screen"]


def test_the_page_knows_the_same_silent_screens():
    script = (UI / "sounds.js").read_text(encoding="utf-8")
    silent = set(re.findall(r'"(\w+)"', re.search(r"const SILENT = new Set\(\[(.*?)\]\)", script).group(1)))
    assert silent == set(SILENT_SCREENS)


def test_the_page_plays_every_sound_the_screens_name():
    script = (UI / "sounds.js").read_text(encoding="utf-8")
    played = set(re.findall(r"^    (\w+): \(", script, flags=re.MULTILINE))
    named = {"count", "shutter", "ready", "call", "tick"}
    assert named <= played
    assert "fetch(" not in script and "Audio(" not in script.replace("new AudioContext(", "")  # made here, no files


def test_the_page_ticks_when_it_sends_a_press():
    app = (UI / "app.js").read_text(encoding="utf-8")
    assert "PhotoboxSounds.follow(state)" in app and "PhotoboxSounds.tick()" in app


def test_the_sound_script_is_served_from_the_booth():
    ui = WebUI().start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{ui.port}", timeout=5) as c:
            script, page = c.get("/sounds.js"), c.get("/")
    finally:
        ui.stop()
    assert script.status_code == 200 and "AudioContext" in script.text
    assert '<script src="sounds.js"></script>' in page.text


def test_sound_settings_are_kept_and_fall_back(tmp_path):
    path = tmp_path / "settings.env"
    assert (Settings.load(path).sounds, Settings.load(path).sound_volume, Settings.load(path).volume) == (True, 60, 60)
    Settings(sounds=False, sound_volume=30).save(path)
    loaded = Settings.load(path)
    assert (loaded.sounds, loaded.sound_volume, loaded.volume) == (False, 30, 0)
    path.write_text("PHOTOBOX_SOUNDS=yes\nPHOTOBOX_SOUND_VOLUME=400\n")
    assert Settings.load(path).volume == 60
