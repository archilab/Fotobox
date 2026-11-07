"""Composing frames into a strip, and output profiles. Knows nothing about how frames were made.

Physical print dimensions are deferred to the hardware phase (see docs/MILESTONES.md): the layout
is in pixels, and `PHOTOBOX_STRIP_WIDTH` should then be set to the printer's width.
"""
from __future__ import annotations
from datetime import date
from pathlib import Path
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps, ImageStat, UnidentifiedImageError
from .config import float_setting, int_setting
from .faces import count_faces

PROFILES = ("color", "grayscale", "thermal")
MIN_FRAME_SIDE = 256
# Below this, a frame is one flat tone: a blank or failed image rather than a scene.
MIN_FRAME_CONTRAST = 1.0


def validate_settings() -> None:
    """Called at startup, so a bad setting stops the server rather than a job."""
    strip_width()
    thermal_settings()


def strip_width() -> int:
    return int_setting("PHOTOBOX_STRIP_WIDTH", 560, 320, 2400)


def thermal_settings() -> tuple[float, float]:
    return (
        float_setting("PHOTOBOX_THERMAL_BRIGHTNESS", 1.1, 0.1, 3.0),
        float_setting("PHOTOBOX_THERMAL_CONTRAST", 1.3, 0.1, 3.0),
    )


def frame_problem(path: Path, max_faces: int | None = None) -> str | None:
    """Describe why a frame cannot be used, or return None. Counts faces if asked; never identifies anyone."""
    try:
        with Image.open(path) as image:
            image.load()
            if min(image.size) < MIN_FRAME_SIDE:
                return "too small"
            if ImageStat.Stat(image.convert("L")).stddev[0] < MIN_FRAME_CONTRAST:
                return "blank"
    except (OSError, UnidentifiedImageError, SyntaxError, ValueError):
        return "unreadable"
    if max_faces is not None and count_faces(path) > max_faces:
        return "too many faces"
    return None


def dated_footer(footer: str) -> str:
    return f"{footer}  {date.today():%d.%m.%Y}"


def render_strip(frames: list[Image.Image], footer: str) -> Image.Image:
    """Stack frames into one vertical strip, cropping any frame that differs from the first's shape."""
    if len(frames) not in (3, 4):
        raise ValueError("frames must be 3 or 4")
    width = strip_width()
    # Even borders all round, as on a real strip.
    margin = gap = round(width * 0.03)
    footer_height = max(42, round(width * 0.075))
    frame_width = width - 2 * margin
    frame_height = round(frame_width * frames[0].height / frames[0].width)
    strip = Image.new("RGB", (width, frame_height * len(frames) + gap * (len(frames) - 1) + 2 * margin + footer_height), "white")
    for index, frame in enumerate(frames):
        fitted = ImageOps.fit(frame.convert("RGB"), (frame_width, frame_height), Image.Resampling.LANCZOS)
        strip.paste(fitted, (margin, margin + index * (frame_height + gap)))
    font = ImageFont.load_default(size=max(11, round(width * 0.024)))
    ImageDraw.Draw(strip).text((width / 2, strip.height - margin - footer_height / 2), footer, fill="black", font=font, anchor="mm")
    return strip


def apply_profile(strip: Image.Image, profile: str) -> Image.Image:
    """Return the strip for an output profile. Deterministic, so a profile can be made on request."""
    if profile == "color":
        return strip.convert("RGB")
    grayscale = ImageOps.grayscale(strip)
    if profile == "grayscale":
        return grayscale
    if profile == "thermal":
        brightness, contrast = thermal_settings()
        adjusted = ImageEnhance.Contrast(ImageEnhance.Brightness(grayscale).enhance(brightness)).enhance(contrast)
        return adjusted.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
    raise ValueError(f"unknown profile {profile!r}")
