"""Deterministic development stand-in. NOT AI: every frame is visibly labelled as a mock."""
from __future__ import annotations
import time
from collections.abc import Callable
from PIL import Image, ImageDraw, ImageOps
from .base import FrameMetrics, FrameRequest, GenerationCancelled

FRAME_SIZE = (524, 400)


class MockProvider:
    name = "mock"
    strip_footer = "CONNECTED PHOTOBOX - DEVELOPMENT MOCK"

    def generate_frame(self, request: FrameRequest, should_cancel: Callable[[], bool]) -> FrameMetrics:
        if should_cancel():
            raise GenerationCancelled()
        started = time.monotonic()
        half = (FRAME_SIZE[0] // 2, FRAME_SIZE[1])
        with Image.open(request.portrait_a) as opened_a, Image.open(request.portrait_b) as opened_b:
            a = ImageOps.fit(opened_a.convert("RGB"), half)
            b = ImageOps.fit(opened_b.convert("RGB"), half)
        left, right = (a, b) if request.frame_index % 2 == 0 else (b, a)
        frame = Image.new("RGB", FRAME_SIZE, "white")
        frame.paste(left, (0, 0))
        frame.paste(right, (half[0], 0))
        ImageDraw.Draw(frame).text((8, 8), f"MOCK / NOT AI - FRAME {request.frame_index + 1}", fill="red", stroke_width=1, stroke_fill="white")
        frame.save(request.output, "PNG")
        return FrameMetrics(seconds=time.monotonic() - started)
