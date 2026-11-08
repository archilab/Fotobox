"""Notices whether someone is at the box, on the Pi only (ADR-029, milestone D2). Off by default.

Runs only while the start screen is showing, and hands the camera back before any photo. Frames are
analysed where they are read and dropped at once: they are never kept, written or sent, and the only
result is whether a person seems to be there. There are no faces, identities, counts or tracking here.
A failure of the camera or the model only means no signal; the booth carries on.

    python -m photobox_client.person test     # shows yes or no on your terminal, nothing is saved
    python -m photobox_client.person test --scores --area 0.25,0,0.75,1     # to set up the picture area
"""
from __future__ import annotations
import argparse
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

MODEL_PATH = Path.home() / "photobox-person" / "nanodet.onnx"
FRAME_WIDTH, FRAME_HEIGHT = 640, 360  # small on purpose
FRAMES_PER_SECOND = 3
THRESHOLD = 0.5  # measured in benchmarks/person_detect
ON_SECONDS = 2.0
OFF_SECONDS = 5.0
WARMUP_FRAMES = 8  # the camera settles its exposure first
RETRY_SECONDS = 30
THREADS = 2  # leaves the other cores to the browser
PERSON_CLASS = 0  # in the COCO classes NanoDet was trained on


class Model(Protocol):
    def score(self, frame: Any) -> float: ...


class NanoDet:
    """How sure the model is that a person is in the frame, from 0 to 1. Nothing else."""
    SIZE = 416

    def __init__(self, path: Path = MODEL_PATH) -> None:
        import cv2
        import numpy as np
        self.cv2, self.np = cv2, np
        cv2.setNumThreads(THREADS)
        self.net = cv2.dnn.readNet(str(path))
        self.outputs = self.net.getUnconnectedOutLayersNames()
        self.mean = np.array([103.53, 116.28, 123.675], dtype=np.float32)
        self.std = np.array([57.375, 57.12, 58.395], dtype=np.float32)

    def _letterbox(self, frame: Any) -> Any:
        height, width = frame.shape[:2]
        scale = self.SIZE / max(height, width)
        image = self.cv2.resize(frame, (round(width * scale), round(height * scale)))
        top, left = (self.SIZE - image.shape[0]) // 2, (self.SIZE - image.shape[1]) // 2
        return self.cv2.copyMakeBorder(image, top, self.SIZE - image.shape[0] - top, left, self.SIZE - image.shape[1] - left, self.cv2.BORDER_CONSTANT, value=0)

    def score(self, frame: Any) -> float:
        rgb = self.cv2.cvtColor(self._letterbox(frame), self.cv2.COLOR_BGR2RGB).astype(self.np.float32)
        self.net.setInput(((rgb - self.mean) / self.std).transpose(2, 0, 1)[None])
        best = 0.0
        for output in self.net.forward(self.outputs):
            if output.shape[-1] == 80:
                people = output[..., PERSON_CLASS].astype(self.np.float64)
                if people.min() < 0 or people.max() > 1:
                    people = 1 / (1 + self.np.exp(-self.np.clip(people, -100, 100)))
                best = max(best, float(people.max()))
        return best


class Smoother:
    """Turns frame-by-frame scores into a steady answer: slow to say yes, slower to say no."""

    def __init__(self, threshold: float = THRESHOLD, on_seconds: float = ON_SECONDS, off_seconds: float = OFF_SECONDS, min_frames: int = 3) -> None:
        self.threshold, self.on_seconds, self.off_seconds, self.min_frames = threshold, on_seconds, off_seconds, min_frames
        self.recent: deque[tuple[float, bool]] = deque()
        self.present = False
        self.last_seen: float | None = None

    def update(self, score: float, now: float) -> bool:
        self.recent.append((now, score >= self.threshold))
        while now - self.recent[0][0] > self.on_seconds:
            self.recent.popleft()
        seen = sum(hit for _, hit in self.recent)
        if len(self.recent) >= self.min_frames and seen * 2 > len(self.recent):
            self.present, self.last_seen = True, now
        elif self.present and self.last_seen is not None and now - self.last_seen >= self.off_seconds:
            self.present = False
        return self.present


@dataclass(frozen=True)
class Area:
    """The part of the picture to look at, as fractions: the inside of the box, without the passers-by."""
    left: float = 0.0
    top: float = 0.0
    right: float = 1.0
    bottom: float = 1.0

    @classmethod
    def parse(cls, text: str) -> "Area":
        """From 'left,top,right,bottom'. Empty or nonsense means the whole picture."""
        try:
            left, top, right, bottom = (float(part) for part in text.split(","))
        except ValueError:
            return cls()
        if not 0 <= left < right <= 1 or not 0 <= top < bottom <= 1:
            return cls()
        return cls(left, top, right, bottom)

    def crop(self, frame: Any) -> Any:
        height, width = frame.shape[:2]
        return frame[int(self.top * height):max(int(self.bottom * height), int(self.top * height) + 1),
                     int(self.left * width):max(int(self.right * width), int(self.left * width) + 1)]


class PersonDetector:
    """Runs only between `resume` and `pause`. `pause` returns once the camera has been released."""

    def __init__(self, open_camera: Callable[[], Any], make_model: Callable[[], Model], on_change: Callable[[bool], None],
                 *, area: Area = Area(), fps: int = FRAMES_PER_SECOND, clock: Callable[[], float] = time.monotonic, retry_seconds: float = RETRY_SECONDS,
                 on_score: Callable[[float], None] | None = None) -> None:
        self.open_camera, self.make_model, self.on_change = open_camera, make_model, on_change
        self.area, self.interval, self.clock, self.retry_seconds = area, 1 / fps, clock, retry_seconds
        self.on_score = on_score
        self.model: Model | None = None
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.present = False

    def resume(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            return
        self.stop.clear()
        self.thread = threading.Thread(target=self._run, name="photobox-person", daemon=True)
        self.thread.start()

    def pause(self) -> None:
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
        self._tell(False)

    def _tell(self, present: bool) -> None:
        if present != self.present:
            self.present = present
            try:
                self.on_change(present)
            except Exception:
                pass  # telling others is never worth stopping for

    def _run(self) -> None:
        while not self.stop.is_set():
            try:
                self._watch()
            except Exception:
                self._tell(False)  # no signal, not a false one; never the reason, which could hold paths
            self.stop.wait(self.retry_seconds)

    def _watch(self) -> None:
        if self.model is None:
            self.model = self.make_model()
        smoother = Smoother()
        camera = self.open_camera()
        try:
            for _ in range(WARMUP_FRAMES):
                if self.stop.is_set():
                    return
                camera.read()
            while not self.stop.is_set():
                started = self.clock()
                ok, frame = camera.read()
                if not ok:
                    raise RuntimeError("no picture")
                score = self.model.score(self.area.crop(frame))
                del frame
                if self.on_score is not None:
                    self.on_score(score)
                self._tell(smoother.update(score, self.clock()))
                self.stop.wait(max(0.0, self.interval - (self.clock() - started)))
        finally:
            camera.release()


def create_detector(camera: str, area: str, on_change: Callable[[bool], None], model_path: Path = MODEL_PATH,
                    on_score: Callable[[float], None] | None = None) -> PersonDetector | None:
    """A detector for this box, or None if its model is not installed."""
    if not model_path.is_file():
        return None
    from .camera import open_stream

    def open_camera() -> Any:
        return open_stream(camera, FRAME_WIDTH, FRAME_HEIGHT)

    return PersonDetector(open_camera, lambda: NanoDet(model_path), on_change, area=Area.parse(area), on_score=on_score)


def main(argv: list[str] | None = None) -> int:
    from .camera import CaptureSettings, capture_jpeg
    from .settings import Settings
    parser = argparse.ArgumentParser(description="Try the person detector. Nothing is saved or sent.")
    parser.add_argument("command", choices=["test"])
    parser.add_argument("--config", type=Path, default=Path.home() / ".photobox-kiosk.env")
    parser.add_argument("--area", help="left,top,right,bottom as fractions, instead of the saved area")
    parser.add_argument("--scores", action="store_true", help="Also print the model's score, about once a second")
    args = parser.parse_args(argv)
    settings = Settings.load(args.config)
    scores: list[float] = []

    def collect(score: float) -> None:
        scores.append(score)
        if args.scores and len(scores) >= FRAMES_PER_SECOND:
            print(f"Punktzahl {max(scores):.2f} (Schwelle {THRESHOLD})", flush=True)
            scores.clear()

    def show(present: bool) -> None:
        print("JA: Es scheint jemand da zu sein." if present else "NEIN: Niemand erkannt.", flush=True)

    detector = create_detector(settings.camera, args.area if args.area is not None else settings.person_area, show, on_score=collect)
    if detector is None:
        print(f"The model is not installed at {MODEL_PATH}.")
        return 1
    print("Läuft. Nur du im Bild, bitte. ENTER beendet den Test und prüft die Übergabe an die Fotokamera.")
    detector.resume()
    input()
    detector.pause()
    photo = capture_jpeg(settings.camera, CaptureSettings(width=1920, height=1080))
    print(f"Kamera-Übergabe in Ordnung: Ein Foto ließ sich sofort aufnehmen ({len(photo) // 1024} KB, verworfen).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
