"""Optional microphone and on-device speech-to-text (spec section 15.4, ADR-026, milestone M-VOICE-1).

Nothing here is needed to take a photo: a missing microphone or a failed transcription only means no
message. Audio exists only in memory, is never written to disk or sent anywhere, and is released as
soon as it has been transcribed. Recording happens only inside `PushToTalk.talk`, after a deliberate
key press, and only while the caller's `window_open` says a message window is open. The kiosk reaches
this only through `conversation.py`, for a window the server opened for a live pair (ADR-027).

    python -m photobox_client.speech list
    python -m photobox_client.speech test
"""
from __future__ import annotations
import argparse
import array
import io
import re
import statistics
import subprocess
import threading
import time
import unicodedata
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from . import views

SAMPLE_RATE = 16000
SAMPLE_BYTES = 2
MAX_SECONDS = 8
TRANSCRIBE_TIMEOUT_SECONDS = 10
ENTER_DEBOUNCE_SECONDS = 0.3
HOME_SPEECH = Path.home() / "photobox-speech"
DEFAULT_COMMAND = HOME_SPEECH / "whisper-cli"
DEFAULT_MODEL = HOME_SPEECH / "ggml-base.bin"
# Speech detection: 30 ms frames, louder than both a fixed floor and three times the room's own noise.
FRAME_SAMPLES = 480
MIN_SPEECH_LEVEL = 150  # measured with a RØDE NT-USB Mini: a quiet room is about 25, normal speech peaks near 1300
MIN_SPEECH_SECONDS = 0.25
ANNOTATION = re.compile(r"\[[^\]]*\]|\([^)]*\)|\*[^*]*\*|♪+")
# What whisper is known to write for silence or noise, learned from subtitled video.
INVENTED = ("vielen dank fürs zuschauen", "danke fürs zuschauen", "untertitel", "untertitelung", "bis zum nächsten mal",
            "thanks for watching", "thank you for watching", "subtitles by", "please subscribe")


class SpeechError(Exception):
    """Speech could not be turned into text. The message is safe to show; it never holds speech."""


class MicrophoneError(SpeechError):
    pass


class LocalSpeechTranscriber(Protocol):
    def transcribe(self, pcm: bytes, language: str, timeout: float) -> str:
        """Return the text heard in mono 16 kHz 16-bit audio, without sending it anywhere. Raises SpeechError."""
        ...


def speech_levels(pcm: bytes) -> dict:
    """Volume figures of a recording. Numbers only: nothing here can reveal what was said."""
    samples = array.array("h")
    samples.frombytes(pcm[:len(pcm) - len(pcm) % SAMPLE_BYTES])
    levels = []
    for start in range(0, len(samples) - FRAME_SAMPLES + 1, FRAME_SAMPLES):
        frame = samples[start:start + FRAME_SAMPLES]
        levels.append((sum(s * s for s in frame) / FRAME_SAMPLES) ** 0.5)
    if not levels:
        return {"loudest": 0, "room": 0, "threshold": MIN_SPEECH_LEVEL, "voice_seconds": 0.0}
    # The quietest fifth of the recording shows how loud the room is on its own.
    floor = statistics.quantiles(levels, n=5)[0] if len(levels) >= 5 else min(levels)
    threshold = max(MIN_SPEECH_LEVEL, 3 * floor)
    voice = sum(level >= threshold for level in levels) * FRAME_SAMPLES / SAMPLE_RATE
    return {"loudest": round(max(levels)), "room": round(floor), "threshold": round(threshold), "voice_seconds": round(voice, 2)}


def has_speech(pcm: bytes) -> bool:
    """Whether the audio plausibly contains a voice. Silence and steady noise must never reach the model."""
    return speech_levels(pcm)["voice_seconds"] >= MIN_SPEECH_SECONDS


def clean_transcript(text: str) -> str:
    """Keep only what was said: no annotations such as [music], and nothing whisper is known to invent."""
    kept = " ".join(ANNOTATION.sub(" ", text).split())
    if not any(unicodedata.category(ch)[0] in "LN" for ch in kept):
        return ""
    lowered = kept.casefold()
    return "" if any(phrase in lowered for phrase in INVENTED) else kept


def to_wav(pcm: bytes) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(SAMPLE_BYTES)
        audio.setframerate(SAMPLE_RATE)
        audio.writeframes(pcm)
    return out.getvalue()


class WhisperCppTranscriber:
    """whisper.cpp, chosen by benchmark (benchmarks/voice_stt). Audio goes to it on standard input."""

    def __init__(self, command: Path = DEFAULT_COMMAND, model: Path = DEFAULT_MODEL, threads: int = 4,
                 runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
        self.command, self.model, self.threads, self.runner = command, model, threads, runner

    def transcribe(self, pcm: bytes, language: str, timeout: float) -> str:
        arguments = [str(self.command), "-m", str(self.model), "-l", language, "-t", str(self.threads), "-nt", "-f", "-"]
        try:
            result = self.runner(arguments, input=to_wav(pcm), capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise SpeechError("speech recognition took too long") from None
        except OSError:
            raise SpeechError("speech recognition is not installed") from None
        if result.returncode != 0:
            raise SpeechError("speech recognition failed")
        return result.stdout.decode("utf-8", "replace")


@dataclass(frozen=True)
class MicrophoneInfo:
    device: str  # by card name, which stays the same when USB numbers change
    name: str


def list_microphones(runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> list[MicrophoneInfo]:
    try:
        result = runner(["arecord", "-l"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return []
    found = re.findall(r"^card \d+: (\S+) \[([^\]]*)\], device (\d+):", result.stdout, flags=re.MULTILINE)
    return [MicrophoneInfo(f"plughw:CARD={card},DEV={device}", name) for card, name, device in found]


class Microphone:
    """Records through ALSA into memory. Only `record` ever opens the device."""

    def __init__(self, device: str, popen: Callable[..., subprocess.Popen] = subprocess.Popen) -> None:
        self.device, self.popen = device, popen

    def record(self, stop: threading.Event, seconds: int = MAX_SECONDS) -> bytes:
        command = ["arecord", "-q", "-D", self.device, "-f", "S16_LE", "-r", str(SAMPLE_RATE), "-c", "1", "-t", "raw", "-d", str(seconds)]
        try:
            process = self.popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError:
            raise MicrophoneError("no microphone tool found") from None
        chunks: list[bytes] = []

        def read() -> None:
            while data := process.stdout.read(SAMPLE_RATE // 4):
                chunks.append(data)

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        stopped_by_us = False
        try:
            while process.poll() is None and not stop.wait(0.05):
                pass
        finally:
            if process.poll() is None:
                stopped_by_us = True  # arecord reports an error when it is stopped by a signal
                process.terminate()
            process.wait(timeout=2)
            reader.join(timeout=2)
        if not chunks or not (stopped_by_us or process.returncode == 0):
            raise MicrophoneError("the microphone did not work")
        return b"".join(chunks)


class Keys(Protocol):
    def flush(self) -> None: ...
    def read_key(self, timeout: float | None) -> str | None: ...


class Screen(Protocol):
    def show(self, text: str) -> None: ...


# German first, then English, in plain letters, like the kiosk's other screens.
NOT_UNDERSTOOD = "Das habe ich nicht verstanden. Bitte versuch es noch einmal.\nI couldn't understand that. Try again."
UNAVAILABLE = "Sprachnachrichten sind gerade nicht verfuegbar.\nVoice messaging unavailable."
CONVERTING = "DEINE STIMME WIRD ZU TEXT ...\nTURNING YOUR VOICE INTO TEXT ..."


class PushToTalk:
    """One message: ENTER starts it, ENTER stops it, and the limit cuts it off (spec section 15.2)."""

    def __init__(self, microphone: Microphone, transcriber: LocalSpeechTranscriber, keys: Keys, screen: Screen,
                 window_open: Callable[[], bool], *, language: str = "auto", enter: str = "enter",
                 limit_seconds: int = MAX_SECONDS, timeout: float = TRANSCRIBE_TIMEOUT_SECONDS,
                 clock: Callable[[], float] = time.monotonic, report_levels: Callable[[dict], None] | None = None) -> None:
        self.microphone, self.transcriber, self.keys, self.screen = microphone, transcriber, keys, screen
        self.window_open, self.language, self.enter = window_open, language, enter
        self.limit_seconds, self.timeout, self.clock = limit_seconds, timeout, clock
        self.report_levels = report_levels

    def _capture(self) -> bytes | None:
        """Record until ENTER, the limit or the window's end. None if the window closed or the microphone failed."""
        stop = threading.Event()
        result: dict[str, object] = {}

        def record() -> None:
            try:
                result["audio"] = self.microphone.record(stop, self.limit_seconds)
            except SpeechError as exc:
                result["error"] = exc

        worker = threading.Thread(target=record, daemon=True)
        worker.start()
        started = self.clock()
        shown = None
        closed = False
        while worker.is_alive():
            left = max(0, self.limit_seconds - int(self.clock() - started))
            if left != shown:
                shown = left
                views.present(self.screen, views.listening(left, self.limit_seconds, f"ICH HOERE ZU · noch {left} Sekunden\nLISTENING · {left} seconds left\n\nENTER = fertig / Press ENTER when you are done."))
            if not self.window_open():
                closed = True
                break
            if self.clock() - started >= self.limit_seconds + 1:
                break
            key = self.keys.read_key(0.1)
            # A held key repeats, and must not end the recording it just started.
            if key == self.enter and self.clock() - started >= ENTER_DEBOUNCE_SECONDS:
                break
        stop.set()
        worker.join(timeout=3)
        if closed or not self.window_open():
            return None
        if "error" in result:
            views.present(self.screen, views.message(UNAVAILABLE))
            return None
        return result.get("audio")  # type: ignore[return-value]

    def talk(self) -> str | None:
        """Record and transcribe one message. Returns its text, or None if there is nothing to send."""
        if not self.window_open():
            return None
        audio = self._capture()
        if audio is None:
            return None
        views.present(self.screen, views.converting(CONVERTING))
        if self.report_levels is not None:
            self.report_levels(speech_levels(audio))
        try:
            if not has_speech(audio):
                views.present(self.screen, views.message(NOT_UNDERSTOOD))
                return None
            text = clean_transcript(self.transcriber.transcribe(audio, self.language, self.timeout))
        except SpeechError:
            views.present(self.screen, views.message(NOT_UNDERSTOOD))
            return None
        finally:
            del audio
        if not self.window_open():
            return None  # the strip finished meanwhile: a late message is dropped
        if not text:
            views.present(self.screen, views.message(NOT_UNDERSTOOD))
            return None
        return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Microphone and speech-to-text test. Nothing is sent or saved.")
    parser.add_argument("command", choices=["list", "test"])
    parser.add_argument("--device", help="Microphone, as shown by 'list'")
    parser.add_argument("--language", default="auto", help="de, en or auto (default)")
    args = parser.parse_args(argv)
    microphones = list_microphones()
    if args.command == "list":
        for microphone in microphones:
            print(f"{microphone.device}\t{microphone.name}")
        if not microphones:
            print("No microphone found.")
        return 0
    from .kiosk import ENTER, TerminalKeys
    import sys
    device = args.device or next((m.device for m in microphones if "BRIO" not in m.device), None)
    if device is None:
        print("No microphone found.")
        return 1

    class Plain:
        def show(self, text: str) -> None:
            print(text)

    with TerminalKeys(sys.stdin.fileno()) as keys:
        print(f"Microphone: {device}\nPress ENTER to speak for up to {MAX_SECONDS} seconds. ESC or Ctrl+C to quit.")
        keys.flush()
        if keys.wait({ENTER}, None) != ENTER:
            return 0
        started = time.monotonic()
        text = PushToTalk(Microphone(device), WhisperCppTranscriber(), keys, Plain(), lambda: True, language=args.language, enter=ENTER,
                          report_levels=lambda levels: print(f"Levels: {levels} (a voice needs at least {MIN_SPEECH_SECONDS} s above the threshold)")).talk()
        print(f"Recognized in {time.monotonic() - started:.1f} s including speaking: {text!r}" if text else "Nothing to send.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
