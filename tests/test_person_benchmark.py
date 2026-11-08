"""The person detection benchmark must never keep a picture (ADR-029)."""
import re
from pathlib import Path


def test_the_benchmark_cannot_save_a_picture():
    source = (Path(__file__).parent.parent / "benchmarks/person_detect/live_benchmark.py").read_text(encoding="utf-8")
    assert not re.search(r"imwrite|VideoWriter|imencode|\.save\(|np\.save|socket|httpx|requests", source)
