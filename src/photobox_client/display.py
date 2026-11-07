"""Shows an image on the Pi's screen by writing to the Linux framebuffer, with no window system.

The kiosk's text console shares the framebuffer, so its next screen simply draws over the image.
"""
from __future__ import annotations
import io
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
)


class FramebufferDisplay:
    def __init__(self, device: Path = Path("/dev/fb0"), sysfs: Path = Path("/sys/class/graphics/fb0")) -> None:
        self.device = device
        width, height = (int(v) for v in (sysfs / "virtual_size").read_text().strip().split(","))
        self.size = (width, height)
        self.bits = int((sysfs / "bits_per_pixel").read_text())
        self.stride = int((sysfs / "stride").read_text())
        if self.bits not in (16, 32):
            raise ValueError(f"unsupported framebuffer depth: {self.bits} bits")

    @classmethod
    def open(cls) -> "FramebufferDisplay | None":
        """The display, or None where there is no usable framebuffer, such as on a Mac."""
        try:
            display = cls()
            with open(display.device, "r+b"):
                pass
            return display
        except (OSError, ValueError):
            return None

    def pack(self, image: Image.Image) -> bytes:
        """Convert to the framebuffer's pixel format, row by row with its stride."""
        import numpy as np
        rgb = np.asarray(image.convert("RGB"), dtype=np.uint16)
        if self.bits == 16:
            # RGB565, little-endian.
            pixels = ((rgb[..., 0] >> 3) << 11) | ((rgb[..., 1] >> 2) << 5) | (rgb[..., 2] >> 3)
            rows = pixels.astype("<u2").view(np.uint8).reshape(image.height, image.width * 2)
        else:
            # XRGB8888, little-endian: bytes are blue, green, red, unused.
            bgrx = np.zeros((image.height, image.width, 4), dtype=np.uint8)
            bgrx[..., 0], bgrx[..., 1], bgrx[..., 2] = rgb[..., 2], rgb[..., 1], rgb[..., 0]
            rows = bgrx.reshape(image.height, image.width * 4)
        padding = self.stride - rows.shape[1]
        if padding:
            rows = np.pad(rows, ((0, 0), (0, padding)))
        return rows.tobytes()

    def show(self, image: Image.Image) -> None:
        canvas = image if image.size == self.size else ImageOps.pad(image.convert("RGB"), self.size, color="black")
        with open(self.device, "r+b") as framebuffer:
            framebuffer.write(self.pack(canvas))


def _font(size: int) -> ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size=size)


def result_screen(strip_png: bytes, code: str | None, size: tuple[int, int]) -> Image.Image:
    """The finished strip, as large as fits, with the withdrawal code beside it."""
    width, height = size
    canvas = Image.new("RGB", size, "black")
    with Image.open(io.BytesIO(strip_png)) as opened:
        strip = opened.convert("RGB")
    margin = height // 30
    strip.thumbnail((width // 2 - 2 * margin, height - 2 * margin), Image.Resampling.LANCZOS)
    left = width // 4 - strip.width // 2
    canvas.paste(strip, (max(margin, left), (height - strip.height) // 2))
    draw = ImageDraw.Draw(canvas)
    x, y = width // 2 + margin, height // 4
    title, body, small = _font(height // 12), _font(height // 28), _font(height // 40)
    draw.text((x, y), "Fertig!  Done!", fill="white", font=title)
    y += height // 7
    if code:
        draw.text((x, y), "Dein Widerrufscode / Your withdrawal code:", fill="white", font=body)
        y += height // 16
        draw.text((x, y), code, fill="#ffd34d", font=body)
        y += height // 12
        draw.text((x, y), "Notiere ihn, um deine Bilder loeschen zu lassen.", fill="white", font=small)
        y += height // 28
        draw.text((x, y), "Write it down to have your images deleted.", fill="white", font=small)
    else:
        draw.text((x, y), "Zum Loeschen deiner Bilder: Betreuung fragen.", fill="white", font=small)
        y += height // 28
        draw.text((x, y), "To have your images deleted, ask the staff.", fill="white", font=small)
    return canvas
