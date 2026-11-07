"""Image generation providers. Selected only by configuration, never in route handlers."""
from __future__ import annotations
from .base import FrameMetrics, FrameRequest, GenerationCancelled, GenerationError, ImageGenerationProvider

__all__ = ["FrameMetrics", "FrameRequest", "GenerationCancelled", "GenerationError", "ImageGenerationProvider", "create_provider"]


def create_provider(name: str) -> ImageGenerationProvider:
    """Create a provider by its configured name. Raises ConfigError if it cannot run here."""
    if name == "mock":
        from .mock import MockProvider
        return MockProvider()
    if name == "mflux-flux2":
        from .mflux_flux2 import MfluxFlux2Provider
        return MfluxFlux2Provider()
    raise ValueError(f"unknown provider {name!r}")
