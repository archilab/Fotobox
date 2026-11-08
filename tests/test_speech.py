"""Microphone capture and local speech-to-text (ADR-026, milestone M-VOICE-1). No real audio or device.

The numbers refer to spec section 15.9.
"""
import array
import inspect
import io
import math
import random
import re
import subprocess
import threading
import wave
from pathlib import Path
import pytest
import photobox_client.kiosk as kiosk_module
import photobox_client.speech as speech
from photobox_client.settings import Settings
from photobox_client.speech import (ENTER_DEBOUNCE_SECONDS, MAX_SECONDS, Microphone, MicrophoneError, MicrophoneInfo, NOT_UNDERSTOOD, PushToTalk,
                                    SpeechError, UNAVAILABLE, WhisperCppTranscriber, clean_transcript, has_speech, list_microphones)

RATE = speech.SAMPLE_RATE


def pcm(samples):
    return array.array("h", [int(s) for s in samples]).tobytes()


def silence(seconds=2):
    return pcm([0] * int(RATE * seconds))


def noise(seconds=2, level=600):
    rng = random.Random(1)
    return pcm(rng.randint(-level, level) for _ in range(int(RATE * seconds)))


def voice(seconds=1.0, level=3000):
    return pcm(level * math.sin(2 * math.pi * 200 * n / RATE) for n in range(int(RATE * seconds)))


def speech_clip():
    return silence(1) + voice(1) + silence(1)


# Telling speech from silence

@pytest.mark.parametrize("audio", [b"", silence(), noise(), noise(level=1500), silence(1) + voice(0.1) + silence(1), b"\x01"])
def test_silence_noise_and_clicks_are_not_speech(audio):
    assert has_speech(audio) is False


@pytest.mark.parametrize("audio", [speech_clip(), silence(1) + voice(1, level=1000) + silence(1), noise(1, level=40) + voice(0.5, level=400) + noise(1, level=40), noise(1, level=100) + voice(1) + noise(1, level=100)])
def test_a_voice_is_speech(audio):
    assert has_speech(audio) is True


def test_levels_are_numbers_only():
    levels = speech.speech_levels(speech_clip())
    assert set(levels) == {"loudest", "room", "threshold", "voice_seconds"} and all(isinstance(v, (int, float)) for v in levels.values())
    assert speech.speech_levels(b"")["voice_seconds"] == 0.0


def test_the_test_command_can_report_levels():
    seen = []
    talk, _, _ = talker(Mic(), Transcriber(), report_levels=seen.append)
    talk.talk()
    assert seen and seen[0]["voice_seconds"] > 0


# What whisper writes

@pytest.mark.parametrize("raw", ["\n * Musik *\n", " [water flowing]", "(music)", "♪♪♪", "[BLANK_AUDIO]", " ... ", "",
                                 "Vielen Dank fürs Zuschauen!", "Untertitel der Amara.org-Community", "Thanks for watching!"])
def test_invented_output_is_dropped(raw):
    assert clean_transcript(raw) == ""


def test_real_words_are_kept():
    assert clean_transcript("\n Schöne Grüße [Musik] an alle!\n") == "Schöne Grüße an alle!"
    assert clean_transcript(" Hello from Berlin, how are you?") == "Hello from Berlin, how are you?"


# The transcriber

class Done:
    def __init__(self, stdout=b" Hallo\n", returncode=0):
        self.stdout, self.returncode = stdout, returncode


def test_audio_reaches_whisper_on_standard_input_as_wav_never_as_a_file():
    seen = {}

    def runner(arguments, **options):
        seen.update(arguments=arguments, **options)
        return Done()

    assert WhisperCppTranscriber(Path("/x/whisper-cli"), Path("/x/m.bin"), runner=runner).transcribe(voice(0.5), "de", 10) == " Hallo\n"
    assert seen["arguments"] == ["/x/whisper-cli", "-m", "/x/m.bin", "-l", "de", "-t", "4", "-nt", "-f", "-"]
    assert seen["timeout"] == 10
    with wave.open(io.BytesIO(seen["input"])) as audio:
        assert (audio.getnchannels(), audio.getframerate(), audio.getsampwidth()) == (1, RATE, 2)


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("x", 10), FileNotFoundError(), None])
def test_transcriber_failures_are_reported_without_detail(failure):
    def runner(*_args, **_options):
        if failure is not None:
            raise failure
        return Done(stdout=b"secret words", returncode=1)

    with pytest.raises(SpeechError) as caught:
        WhisperCppTranscriber(runner=runner).transcribe(b"", "de", 10)
    assert "secret" not in str(caught.value)


# Finding and using a microphone

LISTING = """**** List of CAPTURE Hardware Devices ****
card 0: BRIO [Logitech BRIO], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
card 3: Mini [RØDE NT-USB Mini], device 0: USB Audio [USB Audio]
"""


def test_microphones_are_named_by_card_not_by_number():
    done = type("R", (), {"stdout": LISTING})
    assert list_microphones(lambda *_a, **_k: done) == [
        MicrophoneInfo("plughw:CARD=BRIO,DEV=0", "Logitech BRIO"), MicrophoneInfo("plughw:CARD=Mini,DEV=0", "RØDE NT-USB Mini")]


def test_no_recording_tool_means_no_microphones():
    def missing(*_a, **_k):
        raise FileNotFoundError()
    assert list_microphones(missing) == []


class FakeProcess:
    def __init__(self, data=b"\x01\x00" * 4000, exits_by_itself=None):
        self.stdout = io.BytesIO(data)
        self.running = exits_by_itself is None
        self.code = exits_by_itself
        self.returncode = exits_by_itself
        self.terminated = False

    def poll(self):
        return None if self.running else self.code

    def terminate(self):
        self.terminated, self.running, self.code = True, False, 1  # as arecord does when stopped

    def wait(self, timeout=None):
        self.returncode = self.code


def test_recording_is_raw_to_memory_with_a_hard_limit_and_stops_on_request():
    calls, process = [], FakeProcess()
    stop = threading.Event()
    stop.set()
    audio = Microphone("plughw:CARD=Mini,DEV=0", lambda command, **_k: calls.append(command) or process).record(stop)
    assert audio and process.terminated  # being stopped by us is not a failure
    assert calls[0] == ["arecord", "-q", "-D", "plughw:CARD=Mini,DEV=0", "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "raw", "-d", "8"]


@pytest.mark.parametrize("process", [FakeProcess(b"", exits_by_itself=1), FakeProcess(b"\x00\x00", exits_by_itself=1)])
def test_a_microphone_that_fails_is_reported(process):
    with pytest.raises(MicrophoneError):
        Microphone("x", lambda *_a, **_k: process).record(threading.Event())


def test_a_missing_recording_tool_is_reported():
    def missing(*_a, **_k):
        raise FileNotFoundError()
    with pytest.raises(MicrophoneError):
        Microphone("x", missing).record(threading.Event())


# Push to talk

class Screen:
    def __init__(self):
        self.shown = []

    def show(self, text):
        self.shown.append(text)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Keys:
    """Each read advances the clock; `presses` maps a time to a key."""

    def __init__(self, clock, presses=None):
        self.clock, self.presses = clock, dict(presses or {})

    def flush(self):
        pass

    def read_key(self, timeout):
        self.clock.now += timeout
        for moment in sorted(self.presses):
            if self.clock.now >= moment:
                return self.presses.pop(moment)
        return None


class Mic:
    def __init__(self, audio=None, error=None):
        self.audio, self.error = audio if audio is not None else speech_clip(), error
        self.calls = []

    def record(self, stop, seconds):
        self.calls.append(seconds)
        if self.error:
            raise self.error
        stop.wait(2)
        self.stopped = stop.is_set()
        return self.audio


class Transcriber:
    def __init__(self, text=" Hello there", after=None, error=None):
        self.text, self.after, self.error = text, after, error
        self.calls = []

    def transcribe(self, audio, language, timeout):
        self.calls.append((language, timeout))
        if self.after:
            self.after()
        if self.error:
            raise self.error
        return self.text


def talker(mic, transcriber, *, is_open=lambda: True, presses=None, **options):
    clock, screen = Clock(), Screen()
    presses = {0.5: "enter"} if presses is None else presses
    return PushToTalk(mic, transcriber, Keys(clock, presses), screen, is_open, clock=clock, **options), screen, clock


def test_second_enter_stops_and_the_text_is_returned():  # 2
    mic, transcriber = Mic(), Transcriber()
    talk, screen, _ = talker(mic, transcriber, language="de")
    assert talk.talk() == "Hello there"
    assert mic.calls == [MAX_SECONDS] and mic.stopped and transcriber.calls == [("de", 10)]
    assert screen.shown[0].startswith("LISTENING · 8 seconds left") and "TURNING YOUR VOICE INTO TEXT…" in screen.shown


def test_nothing_is_recorded_while_no_window_is_open():  # 1
    mic = Mic()
    talk, _, _ = talker(mic, Transcriber(), is_open=lambda: False)
    assert talk.talk() is None and mic.calls == []


def test_a_held_key_does_not_end_the_recording():  # 2
    mic = Mic()
    talk, _, clock = talker(mic, Transcriber(), presses={0.1: "enter", 0.2: "enter", 1.0: "enter"})
    talk.talk()
    assert clock.now >= 1.0 > ENTER_DEBOUNCE_SECONDS


def test_other_keys_are_ignored():
    mic = Mic()
    talk, _, clock = talker(mic, Transcriber(), presses={0.5: "x", 0.6: "esc", 1.2: "enter"})
    talk.talk()
    assert clock.now >= 1.2


def test_the_limit_cuts_off_a_recording():  # 3
    mic = Mic()
    talk, screen, clock = talker(mic, Transcriber(), presses={})
    assert talk.talk() == "Hello there"
    assert MAX_SECONDS <= clock.now <= MAX_SECONDS + 1.5 and mic.stopped
    assert any("1 seconds left" in text for text in screen.shown)


def test_window_closing_during_recording_discards_it():  # 10
    calls = iter([True, True, True, False])
    transcriber = Transcriber()
    talk, _, _ = talker(Mic(), transcriber, is_open=lambda: next(calls, False), presses={})
    assert talk.talk() is None and transcriber.calls == []


def test_window_closing_during_transcription_drops_the_text():  # 3, 10
    state = {"open": True}
    transcriber = Transcriber(after=lambda: state.update(open=False))
    talk, _, _ = talker(Mic(), transcriber, is_open=lambda: state["open"])
    assert talk.talk() is None and transcriber.calls


def test_silence_never_reaches_the_model():
    transcriber = Transcriber()
    talk, screen, _ = talker(Mic(silence(3)), transcriber)
    assert talk.talk() is None and transcriber.calls == [] and screen.shown[-1] == NOT_UNDERSTOOD


@pytest.mark.parametrize("heard", [" * Musik *", "", "Thanks for watching!"])
def test_nothing_usable_heard_sends_nothing(heard):
    talk, screen, _ = talker(Mic(), Transcriber(heard))
    assert talk.talk() is None and screen.shown[-1] == NOT_UNDERSTOOD


def test_a_missing_microphone_only_means_no_message():  # 4
    transcriber = Transcriber()
    talk, screen, _ = talker(Mic(error=MicrophoneError("gone")), transcriber)
    assert talk.talk() is None and transcriber.calls == [] and screen.shown[-1] == UNAVAILABLE


def test_a_failed_transcription_only_means_no_message():  # 4
    talk, screen, _ = talker(Mic(), Transcriber(error=SpeechError("too long")))
    assert talk.talk() is None and screen.shown[-1] == NOT_UNDERSTOOD


# Boundaries

def test_the_module_cannot_save_or_send_audio():  # 5
    source = inspect.getsource(speech)
    assert not re.search(r"(?<![\w.])open\(|write_bytes|httpx|requests|socket|urllib|logging", source.replace("wave.open(out", ""))


def test_the_kiosk_reaches_the_microphone_only_through_a_server_window():  # 1
    """kiosk.py never records by itself; conversation.py does, and only for an open window (test_live_messages.py)."""
    source = inspect.getsource(kiosk_module)
    assert "Microphone(" not in source and "PushToTalk(" not in source and "arecord" not in source


# Settings

def test_microphone_setting_is_kept(tmp_path):
    path = tmp_path / "kiosk.env"
    Settings(server="http://mac:8000", camera="/dev/video0", microphone="plughw:CARD=Mini,DEV=0").save(path)
    assert Settings.load(path).microphone == "plughw:CARD=Mini,DEV=0"
    path.write_text("PHOTOBOX_SERVER=http://mac:8000\n")
    assert Settings.load(path).microphone == ""
