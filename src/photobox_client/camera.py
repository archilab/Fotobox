"""USB webcam access through OpenCV: selection, probing and reliable single-frame capture."""
from __future__ import annotations
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BY_ID_DIR = Path("/dev/v4l/by-id")
ROTATIONS = (0, 90, 180, 270)
PROBE_SIZES = ((640, 480), (1280, 720), (1920, 1080), (2560, 1440), (3840, 2160))


class CameraError(Exception):
    """A camera problem with a message intended for the operator."""


@dataclass(frozen=True)
class CaptureSettings:
    width: int | None = None
    height: int | None = None
    rotation: int = 0
    jpeg_quality: int = 90
    warmup_frames: int = 8
    attempts: int = 3
    retry_delay: float = 1.0


def load_cv2() -> Any:
    try:
        import cv2
    except ImportError as exc:
        raise CameraError("OpenCV is not installed. Run: pip install -e '.[client-camera]'") from exc
    return cv2


def list_stable_paths(by_id_dir: Path = BY_ID_DIR) -> list[Path]:
    """Return stable device paths. Webcams often list a second, metadata-only entry."""
    if not by_id_dir.is_dir():
        return []
    return sorted(by_id_dir.iterdir())


def resolve_source(value: str, by_id_dir: Path = BY_ID_DIR) -> int | str:
    """Turn a --camera value (index, device path or by-id name) into an OpenCV source."""
    if value.isdecimal():
        return int(value)
    if "/" in value and not value.startswith("/dev/"):
        raise CameraError(f"Invalid camera {value!r}: use an index, a /dev/ path or a name from camera-list")
    path = Path(value) if value.startswith("/dev/") else by_id_dir / value
    if not path.exists():
        known = ", ".join(p.name for p in list_stable_paths(by_id_dir)) or "none found"
        raise CameraError(f"Camera {value!r} does not exist. Stable camera names: {known}")
    # by-id entries are symlinks; OpenCV's V4L2 backend expects the real device node.
    return os.path.realpath(path)


def _open(cv2: Any, source: int | str) -> Any:
    backend = cv2.CAP_V4L2 if sys.platform.startswith("linux") else cv2.CAP_ANY
    camera = cv2.VideoCapture(source, backend)
    if not camera.isOpened():
        camera.release()
        raise CameraError(f"Camera {source} could not be opened (missing, busy or no permission)")
    return camera


def _apply_size(cv2: Any, camera: Any, width: int | None, height: int | None) -> None:
    if width is None and height is None:
        return
    # USB webcams usually offer high resolutions only in compressed MJPG mode.
    camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    if width is not None:
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    if height is not None:
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, height)


def open_stream(camera: str, width: int, height: int) -> Any:
    """Open the camera for reading frames. The caller must release it."""
    cv2 = load_cv2()
    device = _open(cv2, resolve_source(camera))
    _apply_size(cv2, device, width, height)
    return device


def _rotate(cv2: Any, frame: Any, rotation: int) -> Any:
    codes = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
    return frame if rotation == 0 else cv2.rotate(frame, codes[rotation])


def _capture_once(cv2: Any, source: int | str, settings: CaptureSettings) -> bytes:
    camera = _open(cv2, source)
    try:
        _apply_size(cv2, camera, settings.width, settings.height)
        # Early frames are often dark while auto exposure settles, so only the last one counts.
        ok, frame = False, None
        for _ in range(settings.warmup_frames):
            ok, frame = camera.read()
        if not ok or frame is None or frame.size == 0:
            raise CameraError(f"Camera {source} returned no frame")
        rotated = _rotate(cv2, frame, settings.rotation)
        ok, encoded = cv2.imencode(".jpg", rotated, [int(cv2.IMWRITE_JPEG_QUALITY), settings.jpeg_quality])
        if not ok:
            raise CameraError("JPEG encoding failed")
        return encoded.tobytes()
    finally:
        camera.release()


def capture_jpeg(camera: str, settings: CaptureSettings = CaptureSettings()) -> bytes:
    """Capture one JPEG, reopening the camera between attempts to recover from USB errors."""
    if settings.rotation not in ROTATIONS:
        raise CameraError(f"Rotation must be one of {ROTATIONS}")
    if not 1 <= settings.jpeg_quality <= 100:
        raise CameraError("JPEG quality must be between 1 and 100")
    if settings.warmup_frames < 1 or settings.attempts < 1:
        raise CameraError("Warm-up frames and attempts must be at least 1")
    # A name that does not exist will not start existing on retry, so this fails immediately.
    source = resolve_source(camera)
    cv2 = load_cv2()
    last_error: CameraError | None = None
    for attempt in range(1, settings.attempts + 1):
        try:
            return _capture_once(cv2, source, settings)
        except CameraError as exc:
            last_error = exc
            if attempt < settings.attempts:
                time.sleep(settings.retry_delay)
    raise CameraError(f"{last_error} (after {settings.attempts} attempts)")


def _control(cv2: Any, camera: Any, name: str) -> float | None:
    # OpenCV reports unsupported controls as -1, or as 0 on some backends.
    value = camera.get(getattr(cv2, name))
    return None if value == -1 else value


def _fourcc_text(value: float) -> str:
    code = int(value)
    return "".join(chr((code >> (8 * i)) & 0xFF) for i in range(4)).strip("\x00 ") or "unknown"


def probe_camera(camera: str) -> dict[str, Any]:
    """Report what the camera offers. Sizes are what it accepted when asked, not a full list."""
    source = resolve_source(camera)
    cv2 = load_cv2()
    device = _open(cv2, source)
    try:
        ok, frame = device.read()
        report: dict[str, Any] = {
            "source": source,
            "backend": device.getBackendName(),
            "frame_read": bool(ok and frame is not None),
            "width": int(device.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(device.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "fps": device.get(cv2.CAP_PROP_FPS),
            "format": _fourcc_text(device.get(cv2.CAP_PROP_FOURCC)),
            "autofocus": _control(cv2, device, "CAP_PROP_AUTOFOCUS"),
            "focus": _control(cv2, device, "CAP_PROP_FOCUS"),
            "auto_exposure": _control(cv2, device, "CAP_PROP_AUTO_EXPOSURE"),
            "exposure": _control(cv2, device, "CAP_PROP_EXPOSURE"),
        }
        accepted: list[str] = []
        for width, height in PROBE_SIZES:
            _apply_size(cv2, device, width, height)
            if int(device.get(cv2.CAP_PROP_FRAME_WIDTH)) == width and int(device.get(cv2.CAP_PROP_FRAME_HEIGHT)) == height:
                accepted.append(f"{width}x{height}")
        report["accepted_sizes"] = accepted
        return report
    finally:
        device.release()
