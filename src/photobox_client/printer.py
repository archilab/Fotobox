"""Printing the finished strip on a USB receipt printer with ESC/POS commands (ADR-035).

Measured with an Epson TM-m30III on 80 mm paper: 576 dots per line at 203 dpi. The kernel's usblp driver
makes it /dev/usb/lp0. The strip comes from the server already as 1-bit (`profile=thermal`), is held in
memory only, and goes straight to the printer: nothing is written to disk.

Each strip is sent once. A printer that is missing, out of paper or not answering only means the strip is
shown on the screen instead, as before.
"""
from __future__ import annotations

import glob
import io
import os
import select
import time
from pathlib import Path

from PIL import Image, ImageOps

DEVICE_PATTERN = "/dev/usb/lp*"
PAPER_DOTS = 576  # 80 mm paper at 203 dpi
BAND_LINES = 256  # lines per raster command, well inside the printer's buffer
WRITE_SECONDS = 30  # a printer that takes longer is treated as failed
STATUS_SECONDS = 1.0
INITIALIZE = b"\x1b@"
FEED_AND_CUT = b"\x1bd\x04" + b"\x1dVB\x00"  # feed 4 lines, then feed to the cutter and cut partially
PAPER_SENSOR_STATUS = b"\x10\x04\x04"  # DLE EOT 4: roll paper sensor
PAPER_END_BITS = 0x60


class PrinterError(Exception):
    """The strip could not be printed. The message is for the operator."""


class NoPaper(PrinterError):
    pass


def find_printer(pattern: str = DEVICE_PATTERN) -> UsbPrinter | None:
    """The first printer this user may write to, or None."""
    for path in sorted(glob.glob(pattern)):
        if os.access(path, os.W_OK):
            return UsbPrinter(Path(path))
    return None


def paper_state(reply: bytes) -> str:
    """'no paper', 'ready', or 'unknown' when the printer gave no usable answer."""
    if len(reply) != 1 or reply[0] & 0x93 != 0x12:  # the fixed bits of a DLE EOT reply
        return "unknown"
    return "no paper" if reply[0] & PAPER_END_BITS else "ready"


def raster(image: Image.Image, paper_dots: int = PAPER_DOTS) -> bytes:
    """ESC/POS bytes for one image, centred on the paper, followed by a cut."""
    gray = image.convert("L")
    if gray.width > paper_dots:
        gray = gray.resize((paper_dots, round(gray.height * paper_dots / gray.width)), Image.Resampling.LANCZOS)
        gray = gray.convert("1", dither=Image.Dither.FLOYDSTEINBERG).convert("L")
    page = Image.new("L", (paper_dots, gray.height), 255)
    page.paste(gray, ((paper_dots - gray.width) // 2, 0))
    # In ESC/POS a set bit is a black dot; in Pillow's 1-bit images a set bit is white.
    dots = ImageOps.invert(page).convert("1", dither=Image.Dither.NONE)
    row_bytes = paper_dots // 8
    data = dots.tobytes()
    out = bytearray(INITIALIZE)
    for top in range(0, dots.height, BAND_LINES):
        lines = min(BAND_LINES, dots.height - top)
        out += b"\x1dv0\x00" + row_bytes.to_bytes(2, "little") + lines.to_bytes(2, "little")
        out += data[top * row_bytes:(top + lines) * row_bytes]
    return bytes(out + FEED_AND_CUT)


class UsbPrinter:
    def __init__(self, path: Path, clock=time.monotonic) -> None:
        self.path, self.clock = path, clock

    def status(self) -> str:
        """Ask whether there is paper: 'ready', 'no paper' or 'unknown'. Prints nothing."""
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_NONBLOCK)
        except OSError:
            return "unknown"
        try:
            os.write(fd, PAPER_SENSOR_STATUS)
            if select.select([fd], [], [], STATUS_SECONDS)[0]:
                return paper_state(os.read(fd, 8))
            return "unknown"
        except OSError:
            return "unknown"
        finally:
            os.close(fd)

    def print_strip(self, png: bytes) -> None:
        """Print a strip given as an image file in memory. Raises PrinterError."""
        try:
            data = raster(Image.open(io.BytesIO(png)))
        except (OSError, ValueError) as exc:
            raise PrinterError("the strip could not be read") from exc
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:
            raise PrinterError(f"the printer could not be opened: {exc.strerror}") from exc
        deadline = self.clock() + WRITE_SECONDS
        try:
            sent = 0
            while sent < len(data):
                left = deadline - self.clock()
                if left <= 0:
                    raise PrinterError("the printer did not take the strip in time")
                if not select.select([], [fd], [], left)[1]:
                    continue
                try:
                    sent += os.write(fd, data[sent:sent + 4096])
                except BlockingIOError:
                    continue
            # usblp cancels a write still in flight when the device is closed. The last write carries
            # the cut, so wait until the printer has taken it: the device is writable again then.
            while not select.select([], [fd], [], max(0.0, deadline - self.clock()))[1]:
                if self.clock() >= deadline:
                    raise PrinterError("the printer did not finish in time")
        except OSError as exc:
            raise PrinterError(f"printing failed: {exc.strerror}") from exc
        finally:
            os.close(fd)
