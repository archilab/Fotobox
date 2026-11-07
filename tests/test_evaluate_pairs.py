"""The evaluation-set tool, with a fake provider. Synthetic images only."""
import csv
import importlib.util
import json
import os
import stat
from pathlib import Path
import pytest
from PIL import Image
from photobox_server.providers import FrameMetrics, GenerationError

SCRIPT = Path(__file__).parents[1] / "scripts" / "evaluate_pairs.py"


@pytest.fixture
def tool(monkeypatch):
    spec = importlib.util.spec_from_file_location("evaluate_pairs", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "create_provider", lambda _name: Fake())
    return module


class Fake:
    name, strip_footer = "fake", "FAKE"
    failing_portrait = None

    def generate_frame(self, request, should_cancel):
        if request.portrait_a.name == Fake.failing_portrait:
            raise GenerationError("model failed")
        Image.linear_gradient("L").resize((768, 1024)).save(request.output, "PNG")
        return FrameMetrics(seconds=1.0, peak_memory_gb=10.3)


@pytest.fixture
def pairs(tmp_path):
    Fake.failing_portrait = None
    paths = {}
    for name in ("a1.png", "b1.png", "a2.png", "b2.png"):
        paths[name] = tmp_path / name
        Image.new("RGB", (120, 160), "#777777").save(paths[name])
    config = tmp_path / "pairs.json"
    config.write_text(json.dumps({"pairs": [
        {"id": "first", "source": "fictional", "a": str(paths["a1.png"]), "b": str(paths["b1.png"])},
        {"id": "second", "source": "webcam-consented", "a": str(paths["a2.png"]), "b": str(paths["b2.png"])},
    ]}))
    return config


def test_attestation_is_required(tool, pairs, tmp_path):
    with pytest.raises(SystemExit):
        tool.main(["--pairs", str(pairs), "--out", str(tmp_path / "run")])
    assert not (tmp_path / "run").exists()


def test_results_inside_the_repository_are_refused(tool, pairs):
    with pytest.raises(SystemExit):
        tool.main(["--pairs", str(pairs), "--out", str(SCRIPT.parents[1] / "evaluation-run"), "--attest-documented-consent"])
    assert not (SCRIPT.parents[1] / "evaluation-run").exists()


def test_existing_results_are_not_overwritten(tool, pairs, tmp_path):
    out = tmp_path / "run"
    out.mkdir()
    (out / "keep.txt").write_text("x")
    with pytest.raises(SystemExit):
        tool.main(["--pairs", str(pairs), "--out", str(out), "--attest-documented-consent"])


def test_makes_a_strip_per_pair_and_a_scoring_sheet(tool, pairs, tmp_path):
    out = tmp_path / "run"
    assert tool.main(["--pairs", str(pairs), "--out", str(out), "--attest-documented-consent"]) == 0
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o700
    for pair in ("first", "second"):
        with Image.open(out / pair / "strip.png") as strip:
            assert strip.width == 560
        assert stat.S_IMODE(os.stat(out / pair / "strip.png").st_mode) == 0o600
        assert not (out / pair / "frames").exists()
    with open(out / "scores.csv", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [(r["pair"], r["source"], r["person_a_1to5"]) for r in rows] == [("first", "fictional", ""), ("second", "webcam-consented", "")]
    summary = json.loads((out / "results.json").read_text())
    assert summary["provider"] == "fake" and summary["results"][0]["peak_memory_gb"] == 10.3
    assert str(tmp_path) not in (out / "results.json").read_text()
    assert "first/strip.png" in (out / "review.html").read_text()


def test_one_failed_pair_does_not_stop_the_others(tool, pairs, tmp_path):
    Fake.failing_portrait = "a1.png"
    out = tmp_path / "run"
    assert tool.main(["--pairs", str(pairs), "--out", str(out), "--attest-documented-consent"]) == 1
    results = json.loads((out / "results.json").read_text())["results"]
    assert [r["status"] for r in results] == ["failed", "ok"]
    assert "model failed" in (out / "review.html").read_text()
    assert (out / "second" / "strip.png").exists() and not (out / "first" / "strip.png").exists()


@pytest.mark.parametrize("change", [
    {"source": "found-online"},
    {"id": "../escape"},
    {"a": "/no/such/file.png"},
])
def test_bad_pairs_file_is_refused_before_anything_runs(tool, pairs, tmp_path, capsys, change):
    data = json.loads(pairs.read_text())
    data["pairs"][0].update(change)
    pairs.write_text(json.dumps(data))
    out = tmp_path / "run"
    assert tool.main(["--pairs", str(pairs), "--out", str(out), "--attest-documented-consent"]) == 1
    assert "pairs file" in capsys.readouterr().err and not out.exists()


def test_a_non_image_portrait_is_refused(tool, pairs, tmp_path):
    data = json.loads(pairs.read_text())
    Path(data["pairs"][1]["b"]).write_bytes(b"not an image")
    assert tool.main(["--pairs", str(pairs), "--out", str(tmp_path / "run"), "--attest-documented-consent"]) == 1
    assert not (tmp_path / "run").exists()
