import pytest
from PIL import Image
from photobox_server.core import render_strip


@pytest.mark.parametrize("count", [3, 4])
def test_three_and_four_frames(count):
    frames = [Image.new("RGB", (524, 400), "white") for _ in range(count)]
    result = render_strip(frames, "FOOTER")
    assert result.width == 560 and result.height > count * 400


def test_frame_proportions_are_kept():
    frames = [Image.new("RGB", (768, 1024), "white") for _ in range(4)]
    result = render_strip(frames, "FOOTER")
    assert result.height > 4 * 698


@pytest.mark.parametrize("count", [0, 2, 5])
def test_other_frame_counts_are_refused(count):
    with pytest.raises(ValueError):
        render_strip([Image.new("RGB", (10, 10))] * count, "FOOTER")
