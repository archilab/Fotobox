#!/usr/bin/env python3
"""Compare local speech-to-text engines on the Pi: accuracy, time and memory (spec section 15.4).

    venv/bin/python benchmark.py AUDIO_DIR BENCH_DIR RESULT.json

Each clip runs in its own process, so its peak memory is measured separately. Times separate loading
the model from transcribing, because the kiosk would load a model once. Results hold scores only,
never the recognized text, so a run with a person's own speech leaves no transcript behind.
"""
import json
import os
import re
import statistics
import subprocess
import sys
import time
import unicodedata
import wave
from pathlib import Path

THREADS = "4"
VOSK = {"de": "vosk-model-small-de-0.15", "en": "vosk-model-small-en-us-0.15"}
TIMING = re.compile(r"(load|total) time\s*=\s*([\d.]+) ms")


def words(text):
    text = unicodedata.normalize("NFC", text).casefold()
    return re.sub(r"[^\w\s']", " ", text).split()


def word_error_rate(expected, heard):
    previous = list(range(len(heard) + 1))
    for i, word in enumerate(expected, 1):
        current = [i]
        for j, other in enumerate(heard, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (word != other)))
        previous = current
    return previous[-1] / max(1, len(expected))


def vosk_child(model, wav_path):
    """Runs in the measured child process."""
    import vosk
    vosk.SetLogLevel(-1)
    started = time.perf_counter()
    loaded = vosk.Model(model)
    load_seconds = time.perf_counter() - started
    started = time.perf_counter()
    with wave.open(wav_path, "rb") as audio:
        recognizer = vosk.KaldiRecognizer(loaded, audio.getframerate())
        while data := audio.readframes(4000):
            recognizer.AcceptWaveform(data)
    text = json.loads(recognizer.FinalResult())["text"]
    print(json.dumps({"text": text, "load": load_seconds, "transcribe": time.perf_counter() - started}))


def measured(command):
    """Run a command, returning its stdout, stderr and peak memory in MB."""
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out = process.stdout.read()
    err = process.stderr.read()
    _, status, usage = os.wait4(process.pid, 0)
    process.returncode = os.waitstatus_to_exitcode(status)
    if process.returncode != 0:
        raise RuntimeError(f"{command[0]} exited with {process.returncode}")
    return out, err, usage.ru_maxrss / 1024


def run_vosk(bench, clip, path):
    out, _, memory = measured([sys.executable, __file__, "--vosk-child", str(bench / "models" / VOSK[clip["language"]]), str(path)])
    result = json.loads(out)
    return result["text"], result["load"], result["transcribe"], memory


def run_whisper(model):
    def run(bench, clip, path):
        command = [str(bench / "src/build/bin/whisper-cli"), "-m", str(bench / "models" / f"ggml-{model}.bin"),
                   "-l", clip["language"], "-t", THREADS, "-nt", "-f", str(path)]
        out, err, memory = measured(command)
        times = {name: float(value) / 1000 for name, value in TIMING.findall(err)}
        return " ".join(out.split()), times["load"], times["total"] - times["load"], memory
    return run


def main():
    audio, bench, output = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    clips = json.loads((audio / "manifest.json").read_text(encoding="utf-8"))
    engines = {"vosk-small": run_vosk, "whisper-tiny": run_whisper("tiny"), "whisper-base": run_whisper("base")}
    results = {}
    for name, engine in engines.items():
        for language in sorted({clip["language"] for clip in clips}):
            rows = []
            for clip in (c for c in clips if c["language"] == language):
                path = audio / clip["file"]
                with wave.open(str(path), "rb") as handle:
                    length = handle.getnframes() / handle.getframerate()
                heard, load, transcribe, memory = engine(bench, clip, path)
                rows.append({"wer": word_error_rate(words(clip["text"]), words(heard)), "load": load, "transcribe": transcribe, "memory": memory, "length": length})
            summary = {
                "clips": len(rows),
                "word_error_rate": round(statistics.mean(r["wer"] for r in rows), 3),
                "transcribe_seconds_mean": round(statistics.mean(r["transcribe"] for r in rows), 2),
                "transcribe_seconds_max": round(max(r["transcribe"] for r in rows), 2),
                "load_seconds_mean": round(statistics.mean(r["load"] for r in rows), 2),
                "peak_memory_mb": round(max(r["memory"] for r in rows)),
                "audio_seconds_mean": round(statistics.mean(r["length"] for r in rows), 2),
            }
            results[f"{name}/{language}"] = summary
            print(f"{name:13} {language}  {summary}", flush=True)
    output.write_text(json.dumps(results, indent=1), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--vosk-child":
        vosk_child(sys.argv[2], sys.argv[3])
    else:
        main()
