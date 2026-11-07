"""Writing images to a framebuffer, against a fake device file. Synthetic images only."""
import io
import pytest
from PIL import Image
from photobox_client.display import FramebufferDisplay, result_screen


def framebuffer(tmp_path, width=8, height=4, bits=16, stride=None):
    sysfs = tmp_path / "fb0"
    sysfs.mkdir()
    (sysfs / "virtual_size").write_text(f"{width},{height}\n")
    (sysfs / "bits_per_pixel").write_text(f"{bits}\n")
    (sysfs / "stride").write_text(f"{stride or width * bits // 8}\n")
    device = tmp_path / "fb"
    device.write_bytes(b"\xff" * (stride or width * bits // 8) * height)
    return FramebufferDisplay(device, sysfs)


@pytest.mark.parametrize("color,expected", [((255, 0, 0), b"\x00\xf8"), ((0, 255, 0), b"\xe0\x07"), ((0, 0, 255), b"\x1f\x00"), ((255, 255, 255), b"\xff\xff"), ((0, 0, 0), b"\x00\x00")])
def test_16_bit_pixels_are_rgb565(tmp_path, color, expected):
    display = framebuffer(tmp_path)
    assert display.pack(Image.new("RGB", (8, 4), color))[:2] == expected


def test_32_bit_pixels_are_bgrx(tmp_path):
    display = framebuffer(tmp_path, bits=32)
    assert display.pack(Image.new("RGB", (8, 4), (10, 20, 30)))[:4] == bytes([30, 20, 10, 0])


def test_rows_are_padded_to_the_stride(tmp_path):
    display = framebuffer(tmp_path, width=8, height=4, stride=20)
    data = display.pack(Image.new("RGB", (8, 4), (255, 255, 255)))
    assert len(data) == 80 and data[16:20] == b"\x00" * 4 and data[20:22] == b"\xff\xff"


def test_show_writes_a_letterboxed_image_of_the_screen_size(tmp_path):
    display = framebuffer(tmp_path, width=8, height=4)
    display.show(Image.new("RGB", (2, 4), (255, 255, 255)))
    written = display.device.read_bytes()
    assert len(written) == 64
    first_row = written[:16]
    assert first_row[:2] == b"\x00\x00" and first_row[6:8] == b"\xff\xff"


def test_unsupported_depth_is_refused(tmp_path):
    with pytest.raises(ValueError):
        framebuffer(tmp_path, bits=24)


def test_open_returns_none_without_a_framebuffer(monkeypatch, tmp_path):
    monkeypatch.setattr(FramebufferDisplay.__init__, "__defaults__", (tmp_path / "missing", tmp_path / "missing-sysfs"))
    assert FramebufferDisplay.open() is None


def strip_png():
    out = io.BytesIO()
    Image.new("RGB", (560, 1703), (200, 30, 30)).save(out, "PNG")
    return out.getvalue()


def test_result_screen_shows_the_strip_large_and_the_code_beside_it():
    screen = result_screen(strip_png(), "CODE-123", (3840, 2160))
    assert screen.size == (3840, 2160)
    left = screen.crop((0, 0, 1920, 2160))
    red = sum(1 for p in left.getdata() if p == (200, 30, 30))
    assert red > 0.5 * 2160 * 2160 * 560 / 1703 * 0.8
    right = screen.crop((1920, 0, 3840, 2160))
    assert right.getbbox() is not None


def test_result_screen_without_a_code_still_explains_deletion():
    with_code = result_screen(strip_png(), "CODE-123", (1920, 1080))
    without = result_screen(strip_png(), None, (1920, 1080))
    assert with_code.tobytes() != without.tobytes()
    assert without.crop((960, 0, 1920, 1080)).getbbox() is not None
