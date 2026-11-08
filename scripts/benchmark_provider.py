#!/usr/bin/env python3
"""Time the configured image model on this machine, with drawn test faces only.

    PHOTOBOX_PROVIDER=mflux-flux2 python scripts/benchmark_provider.py [frames]

Uses the booth's own provider and prompts. The pictures go to a temporary folder and are deleted.
"""
from __future__ import annotations
import statistics
import sys
import tempfile
import time
from pathlib import Path
from PIL import Image, ImageDraw
from photobox_server.config import get_provider, load_env_file
from photobox_server.prompts import default_content, prompts_for
from photobox_server.providers import FrameRequest, create_provider


def drawn_face(path: Path, skin: str, hair: str) -> None:
    image = Image.new("RGB", (768, 1024), "#808890")
    draw = ImageDraw.Draw(image)
    draw.ellipse((214, 180, 554, 640), fill=skin)
    draw.pieslice((194, 150, 574, 560), 180, 360, fill=hair)
    for x in (300, 468):
        draw.ellipse((x - 22, 380, x + 22, 410), fill="#222222")
    draw.arc((314, 470, 454, 570), 20, 160, fill="#803030", width=8)
    draw.rectangle((120, 700, 648, 1024), fill="#30506a")
    image.save(path, "PNG")


def main() -> int:
    load_env_file()
    frames = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    provider = create_provider(get_provider())
    prompts = prompts_for(frames, 1234, default_content())
    seconds, memory = [], []
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        drawn_face(root / "a.png", "#e0b090", "#402010")
        drawn_face(root / "b.png", "#a07050", "#101010")
        started = time.perf_counter()
        for index in range(frames):
            output = root / f"frame-{index}.png"
            metrics = provider.generate_frame(FrameRequest(root / "a.png", root / "b.png", prompts[index], index, frames, 1234, output, 900), lambda: False)
            with Image.open(output) as check:
                check.verify()
            seconds.append(metrics.seconds)
            memory.append(metrics.peak_memory_gb)
            print(f"frame {index + 1}: {metrics.seconds:.1f} s, peak memory {metrics.peak_memory_gb} GB", flush=True)
        total = time.perf_counter() - started
    print(f"{frames} frames with {provider.name}: {total:.0f} s in all, {statistics.mean(seconds):.1f} s per frame", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
