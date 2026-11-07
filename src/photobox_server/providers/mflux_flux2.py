"""FLUX.2 klein 4B through the mflux command line tool (ADR-014).

mflux runs as a separate process, installed on its own (`uv tool install mflux`), so its
dependencies stay out of this project and a timeout or cancellation can stop it completely.
"""
from __future__ import annotations
import os
import re
import shutil
import signal
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from PIL import Image, UnidentifiedImageError
from ..config import ConfigError, int_setting
from .base import FrameMetrics, FrameRequest, GenerationCancelled, GenerationError

POLL_SECONDS = 0.5
WIDTH, HEIGHT = 768, 1024
PEAK_MEMORY = re.compile(r"Peak MLX memory:\s*([0-9.]+)\s*GB")


def _stop(process: subprocess.Popen) -> None:
    """Stop the process and anything it started."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
    except ProcessLookupError:
        pass


class MfluxFlux2Provider:
    name = "mflux-flux2"
    strip_footer = "CONNECTED PHOTOBOX - AI GENERATED"

    def __init__(self) -> None:
        command = os.getenv("PHOTOBOX_MFLUX_COMMAND", "mflux-generate-flux2-edit")
        resolved = shutil.which(command)
        if resolved is None:
            raise ConfigError(f"{command!r} was not found. Install mflux with `uv tool install mflux`, or set PHOTOBOX_MFLUX_COMMAND to its full path")
        self.command = resolved
        self.steps = int_setting("PHOTOBOX_MFLUX_STEPS", 4, 1, 100)
        self.quantize = int_setting("PHOTOBOX_MFLUX_QUANTIZE", 8, 3, 8)

    def _arguments(self, request: FrameRequest, output: Path) -> list[str]:
        return [
            self.command, "--model", "flux2-klein-4b", "-q", str(self.quantize),
            "--image-paths", str(request.portrait_a), str(request.portrait_b),
            "--prompt", request.prompt, "--steps", str(self.steps), "--seed", str(request.seed),
            "--width", str(WIDTH), "--height", str(HEIGHT), "--no-exif", "--output", str(output),
        ]

    def generate_frame(self, request: FrameRequest, should_cancel: Callable[[], bool]) -> FrameMetrics:
        partial = request.output.with_name(request.output.stem + ".partial.png")
        log_path = request.output.with_name(request.output.stem + ".log")
        started = time.monotonic()
        # Offline: everything is already downloaded, and portraits must never lead to network use.
        environment = {**os.environ, "HF_HUB_OFFLINE": "1"}
        try:
            with open(log_path, "wb") as log:
                process = subprocess.Popen(self._arguments(request, partial), stdout=log, stderr=subprocess.STDOUT, env=environment, start_new_session=True)
                try:
                    while process.poll() is None:
                        if should_cancel():
                            _stop(process)
                            raise GenerationCancelled()
                        if time.monotonic() - started > request.timeout_seconds:
                            _stop(process)
                            raise GenerationError(f"frame timed out after {request.timeout_seconds} s")
                        time.sleep(POLL_SECONDS)
                except BaseException:
                    if process.poll() is None:
                        _stop(process)
                    raise
            if process.returncode != 0:
                raise GenerationError(f"image tool exited with code {process.returncode}")
            try:
                with Image.open(partial) as image:
                    image.verify()
            except (OSError, UnidentifiedImageError, SyntaxError) as exc:
                raise GenerationError("image tool produced no valid image") from exc
            os.replace(partial, request.output)
            found = PEAK_MEMORY.search(log_path.read_text(encoding="utf-8", errors="replace"))
            return FrameMetrics(time.monotonic() - started, float(found.group(1)) if found else None)
        finally:
            # The log can contain the prompt and file paths, so it is not kept.
            log_path.unlink(missing_ok=True)
            partial.unlink(missing_ok=True)
