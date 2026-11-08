#!/usr/bin/env python3
"""Compare person detection models on the Pi, live from its camera (ADR-029, milestone D1).

    ~/photobox/.venv/bin/python live_benchmark.py          # at the booth, in a terminal
    ~/photobox/.venv/bin/python live_benchmark.py --dry    # no camera: checks the models run
    ~/photobox/.venv/bin/python live_benchmark.py --empty  # only the empty booth, three longer measurements

Nothing is recorded. Each frame is analysed at once and dropped; only scores and timings are kept. Run it
yourself, in front of the camera, and only while nobody else is in view. Results go to results.json.
"""
from __future__ import annotations
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path
import cv2
import numpy as np

HERE = Path(__file__).parent
THREADS = 2  # leaves the other cores to the browser on the same Pi
FRAME = (640, 360)
THRESHOLDS = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
SECONDS = 10
GET_READY = 10
LOAD_SECONDS = 30
LOAD_FPS = 3
SCENES = [
    ("empty_1", False, "Niemand im Bild: geh bitte aus dem Sichtfeld der Kamera."),
    ("standing_centre", True, "Stell dich mittig vor die Kamera und bleib ruhig stehen."),
    ("standing_side", True, "Stell dich an den Rand des Bildes."),
    ("turned_away", True, "Dreh der Kamera den Rücken zu."),
    ("close", True, "Geh ganz nah an die Kamera heran."),
    ("far", True, "Geh so weit weg, wie die Box tief ist."),
    ("partly_visible", True, "Steh so, dass nur eine Körperhälfte im Bild ist."),
    ("moving", True, "Beweg dich im Bild: wenden, winken, hin und her."),
    ("empty_2", False, "Niemand im Bild: geh bitte wieder aus dem Sichtfeld."),
]

EMPTY_NOTE = "Niemand im Bild, auch du nicht: ENTER drücken, dann aus dem Sichtfeld gehen und ganz draußen bleiben."
EMPTY_SCENES = [("empty_a", False, EMPTY_NOTE), ("empty_b", False, EMPTY_NOTE), ("empty_c", False, EMPTY_NOTE)]


def letterbox(frame: np.ndarray, size: int) -> np.ndarray:
    height, width = frame.shape[:2]
    scale = size / max(height, width)
    resized = cv2.resize(frame, (round(width * scale), round(height * scale)))
    top = (size - resized.shape[0]) // 2
    left = (size - resized.shape[1]) // 2
    return cv2.copyMakeBorder(resized, top, size - resized.shape[0] - top, left, size - resized.shape[1] - left, cv2.BORDER_CONSTANT, value=0)


def sigmoid(values: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-np.clip(values.astype(np.float64), -100, 100)))


class MediaPipePerson:
    name = "mediapipe_person"

    def __init__(self, models: Path) -> None:
        self.net = cv2.dnn.readNet(str(models / "person.onnx"))
        self.outputs = self.net.getUnconnectedOutLayersNames()

    def score(self, frame: np.ndarray) -> float:
        rgb = cv2.cvtColor(letterbox(frame, 224), cv2.COLOR_BGR2RGB).astype(np.float32) / 255 * 2 - 1
        self.net.setInput(rgb.transpose(2, 0, 1)[None])
        scores = next(o for o in self.net.forward(self.outputs) if o.shape[-1] == 1)
        return float(sigmoid(scores).max())


class NanoDetPerson:
    name = "nanodet_person"
    MEAN = np.array([103.53, 116.28, 123.675], dtype=np.float32)
    STD = np.array([57.375, 57.12, 58.395], dtype=np.float32)

    def __init__(self, models: Path) -> None:
        self.net = cv2.dnn.readNet(str(models / "nanodet.onnx"))
        self.outputs = self.net.getUnconnectedOutLayersNames()

    def score(self, frame: np.ndarray) -> float:
        rgb = cv2.cvtColor(letterbox(frame, 416), cv2.COLOR_BGR2RGB).astype(np.float32)
        self.net.setInput(((rgb - self.MEAN) / self.STD).transpose(2, 0, 1)[None])
        best = 0.0
        for output in self.net.forward(self.outputs):
            if output.shape[-1] == 80:
                person = output[..., 0]  # class 0 of COCO is "person"
                if person.min() < 0 or person.max() > 1:
                    person = sigmoid(person)
                best = max(best, float(person.max()))
        return best


def open_camera():
    from photobox_client.camera import _apply_size, _open, load_cv2, resolve_source
    from photobox_client.settings import Settings
    camera = _open(load_cv2(), resolve_source(Settings.load(Path.home() / ".photobox-kiosk.env").camera))
    _apply_size(cv2, camera, *FRAME)
    return camera


def next_frame(camera) -> np.ndarray:
    if camera is None:
        return np.random.randint(0, 255, (FRAME[1], FRAME[0], 3), np.uint8)
    ok, frame = camera.read()
    if not ok:
        raise RuntimeError("the camera returned no picture")
    return frame


def summary(scores: list[float], times: list[float], person_expected: bool) -> dict:
    points = statistics.quantiles(scores, n=20) if len(scores) >= 2 else scores * 19
    return {
        "frames": len(scores), "person_expected": person_expected,
        "score_p5": round(points[0], 3), "score_median": round(statistics.median(scores), 3), "score_p95": round(points[-1], 3),
        "detected_share": {str(t): round(sum(s >= t for s in scores) / len(scores), 3) for t in THRESHOLDS},
        "ms_mean": round(1000 * statistics.mean(times), 1), "ms_p95": round(1000 * sorted(times)[int(len(times) * .95)], 1),
    }


def temperature() -> str:
    try:
        return subprocess.run(["vcgencmd", "measure_temp"], capture_output=True, text=True, timeout=3).stdout.strip()
    except OSError:
        return "unknown"


def measure_load(detector, camera, seconds: float) -> dict:
    """CPU use at the planned rate. Every frame is dropped straight after it is scored."""
    start_cpu, start = time.process_time(), time.monotonic()
    frames = 0
    while time.monotonic() - start < seconds:
        tick = time.monotonic()
        detector.score(next_frame(camera))
        frames += 1
        time.sleep(max(0, 1 / LOAD_FPS - (time.monotonic() - tick)))
    return {"fps": round(frames / (time.monotonic() - start), 2), "cpu_percent_of_one_core": round(100 * (time.process_time() - start_cpu) / (time.monotonic() - start), 1), "temperature_after": temperature(), "load_average": os.getloadavg()[0]}


def main() -> int:
    dry = "--dry" in sys.argv
    empty_only = "--empty" in sys.argv
    scenes = EMPTY_SCENES if empty_only else SCENES
    seconds = 20 if empty_only else SECONDS
    cv2.setNumThreads(THREADS)
    models = HERE / "models"
    detectors = [MediaPipePerson(models), NanoDetPerson(models)]
    camera = None if dry else open_camera()
    results: dict = {"scenes": {}, "load": {}, "temperature_start": temperature(), "threads": THREADS, "frame": FRAME}
    try:
        for name, expected, instruction in scenes:
            if not dry:
                input(f"\n{instruction}\nENTER drücken, dann hast du {GET_READY} Sekunden ... ")
                time.sleep(GET_READY)
                print(f"  Messung läuft, {seconds} Sekunden ...", flush=True)
            scores = {d.name: [] for d in detectors}
            times = {d.name: [] for d in detectors}
            start = time.monotonic()
            while time.monotonic() - start < (0.5 if dry else seconds):
                frame = next_frame(camera)
                for detector in detectors:
                    tick = time.perf_counter()
                    scores[detector.name].append(detector.score(frame))
                    times[detector.name].append(time.perf_counter() - tick)
                del frame
            results["scenes"][name] = {d.name: summary(scores[d.name], times[d.name], expected) for d in detectors}
            print("  fertig", flush=True)
        if not dry and not empty_only:
            input("\nLetzter Schritt, 1 Minute: Bitte geh aus dem Bild und drücke ENTER ... ")
            time.sleep(GET_READY)
        for detector in [] if empty_only else detectors:
            results["load"][detector.name] = measure_load(detector, camera, 2 if dry else LOAD_SECONDS)
    finally:
        if camera is not None:
            camera.release()
    (HERE / ("results-dry.json" if dry else "results-empty.json" if empty_only else "results.json")).write_text(json.dumps(results, indent=1), encoding="utf-8")
    print("\nFertig. Es wurden keine Bilder gespeichert.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
