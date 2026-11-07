"""The boundary between the job runner and any image model. Nothing here names a model."""
from __future__ import annotations
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class GenerationError(Exception):
    """A frame could not be generated. The message must contain no personal or image data."""


class GenerationCancelled(Exception):
    """Generation stopped because `should_cancel` returned True."""


@dataclass(frozen=True)
class FrameRequest:
    portrait_a: Path
    portrait_b: Path
    prompt: str
    frame_index: int
    frame_count: int
    seed: int
    output: Path
    timeout_seconds: int


@dataclass(frozen=True)
class FrameMetrics:
    seconds: float
    peak_memory_gb: float | None = None


class ImageGenerationProvider(Protocol):
    """Generates one frame at a time, so an interrupted job can resume from the frames it has."""

    name: str
    # Printed on every strip, so that its origin stays visible.
    strip_footer: str
    # The most faces a frame may show, or None if this provider's frames need no count.
    max_faces: int | None

    def generate_frame(self, request: FrameRequest, should_cancel: Callable[[], bool]) -> FrameMetrics:
        """Write a complete image to `request.output`, or raise GenerationError or GenerationCancelled."""
        ...
