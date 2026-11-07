"""Optionally counts faces in a frame, to limit how many people a model invented (PHOTOBOX_MAX_FACES).

Only a number comes out. Nothing identifies or compares anyone, and nothing about a face is stored.
Detection is imperfect: hidden or blurred faces are often missed, so this can only catch too many
faces, never prove there are two. The settings were chosen on 36 frames (see docs/DECISIONS.md).
"""
from __future__ import annotations
from pathlib import Path
from .config import ConfigError

CASCADE = "haarcascade_frontalface_alt2.xml"
# A frame is measured at the size the settings were chosen at, whatever the provider produced.
DETECTION_WIDTH = 1024
_detector = None


def _load():
    global _detector
    if _detector is None:
        import cv2
        _detector = cv2.CascadeClassifier(cv2.data.haarcascades + CASCADE)
        if _detector.empty():
            raise ConfigError(f"could not load the face detector {CASCADE}")
    return _detector


def ensure_available() -> None:
    """Called at startup, so a missing detector stops the server rather than every job."""
    try:
        import cv2
        if not hasattr(cv2, "CascadeClassifier"):
            raise ImportError("OpenCV 5 has no CascadeClassifier")
    except ImportError as exc:
        raise ConfigError("the face-count check needs OpenCV 4: pip install -e '.[server-ai]'") from exc
    _load()


def _distinct(boxes: list[tuple[int, int, int, int]]) -> int:
    """Count boxes, treating heavily overlapping ones as the same face."""
    kept: list[tuple[int, int, int, int]] = []
    for box in sorted(boxes, key=lambda b: -b[2] * b[3]):
        x, y, w, h = box
        for other_x, other_y, other_w, other_h in kept:
            overlap = max(0, min(x + w, other_x + other_w) - max(x, other_x)) * max(0, min(y + h, other_y + other_h) - max(y, other_y))
            if overlap >= 0.3 * min(w * h, other_w * other_h):
                break
        else:
            kept.append(box)
    return len(kept)


def count_faces(path: Path) -> int:
    import numpy as np
    from PIL import Image
    with Image.open(path) as image:
        gray = image.convert("L")
        if gray.width != DETECTION_WIDTH:
            gray = gray.resize((DETECTION_WIDTH, round(gray.height * DETECTION_WIDTH / gray.width)))
    boxes = _load().detectMultiScale(np.array(gray), scaleFactor=1.08, minNeighbors=5, minSize=(40, 40))
    return _distinct([tuple(int(v) for v in box) for box in boxes])
