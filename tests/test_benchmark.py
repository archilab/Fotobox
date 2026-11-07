"""The benchmark tool stands alone, so it is loaded from its file. These tests use no real model."""
import importlib.util
import json
import sys
from pathlib import Path
import pytest

BENCHMARK_DIR = Path(__file__).resolve().parents[1] / "benchmarks" / "ai_feasibility"
spec = importlib.util.spec_from_file_location("ai_benchmark", BENCHMARK_DIR / "benchmark.py")
benchmark = importlib.util.module_from_spec(spec)
sys.modules["ai_benchmark"] = benchmark
spec.loader.exec_module(benchmark)

WRITE_PNG = "import sys; open(sys.argv[1],'wb').write(b'\\x89PNG\\r\\n\\x1a\\n')"


def config_for(*code_snippets):
    return {
        "seeds": [1],
        "scenes": [{"id": "cafe", "prompt": "Image 1 and {braces} <b>Image 2</b>; rm -rf x"}],
        "backends": [{"name": f"backend{i}", "command": [sys.executable, "-c", code, "{output}"]} for i, code in enumerate(code_snippets)],
    }


@pytest.fixture
def portraits(tmp_path):
    a, b = tmp_path / "secret-name-a.jpg", tmp_path / "secret-name-b.jpg"
    a.write_bytes(b"synthetic")
    b.write_bytes(b"synthetic")
    return a, b


def run(tmp_path, portraits, config, timeout=30):
    return benchmark.run_benchmark(config, *portraits, tmp_path / "run", timeout)


def test_successful_run_is_measured_and_recorded(tmp_path, portraits):
    results = run(tmp_path, portraits, config_for(WRITE_PNG))
    assert results[0]["status"] == "ok"
    assert results[0]["seconds"] >= 0 and results[0]["peak_rss_gib"] >= 0
    saved = json.loads((tmp_path / "run" / "results.json").read_text())
    assert saved["summary"][0] == {"backend": "backend0", "runs": 1, "succeeded": 1, "median_seconds": results[0]["seconds"], "max_peak_system_used_gib": results[0]["peak_system_used_gib"]}


def test_results_do_not_contain_portrait_paths(tmp_path, portraits):
    run(tmp_path, portraits, config_for(WRITE_PNG))
    assert "secret-name" not in (tmp_path / "run" / "results.json").read_text()


def test_failures_are_recorded_and_later_backends_still_run(tmp_path, portraits):
    results = run(tmp_path, portraits, config_for("import sys; sys.exit(3)", WRITE_PNG))
    assert (results[0]["status"], results[0]["exit_code"]) == ("failed", 3)
    assert results[1]["status"] == "ok"


def test_exit_zero_without_a_valid_image_is_not_success(tmp_path, portraits):
    results = run(tmp_path, portraits, config_for("import sys; open(sys.argv[1],'wb').write(b'not an image')"))
    assert results[0]["status"] == "no_valid_output"


def test_timeout_kills_the_run(tmp_path, portraits):
    results = run(tmp_path, portraits, config_for("import time; time.sleep(30)"), timeout=0.5)
    assert results[0]["status"] == "timeout"
    assert results[0]["seconds"] < 10


def test_missing_program_is_reported(tmp_path, portraits):
    config = config_for(WRITE_PNG)
    config["backends"][0]["command"] = ["/nonexistent/photobox-program", "{output}"]
    assert run(tmp_path, portraits, config)[0]["status"] == "not_runnable"


def test_prompt_is_passed_as_one_argument_without_a_shell(tmp_path, portraits):
    code = "import sys; open(sys.argv[1],'wb').write(b'\\x89PNG\\r\\n\\x1a\\n'); open(sys.argv[1]+'.txt','w').write(sys.argv[2])"
    config = config_for(code)
    config["backends"][0]["command"].append("{prompt}")
    results = run(tmp_path, portraits, config)
    assert Path(results[0]["output"] + ".txt").read_text() == config["scenes"][0]["prompt"]


def test_review_page_escapes_text_and_scores_sheet_lists_only_successes(tmp_path, portraits):
    run(tmp_path, portraits, config_for(WRITE_PNG, "import sys; sys.exit(1)"))
    page = (tmp_path / "run" / "review.html").read_text()
    assert "<b>Image 2</b>" not in page and "&lt;b&gt;Image 2&lt;/b&gt;" in page
    scores = (tmp_path / "run" / "scores.csv").read_text().splitlines()
    assert len(scores) == 2 and scores[1].startswith("backend0,cafe,1,")


def test_dry_run_runs_nothing(tmp_path, portraits, capsys):
    config = config_for("raise SystemExit('must not run')")
    assert benchmark.run_benchmark(config, *portraits, tmp_path / "run", 30, dry_run=True) == []
    assert "backend" not in capsys.readouterr().err
    assert not (tmp_path / "run" / "results.json").exists()


def test_existing_run_folder_is_never_overwritten(tmp_path, portraits, capsys):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config_for(WRITE_PNG)))
    existing = tmp_path / "existing"
    existing.mkdir()
    code = benchmark.main(["--config", str(config_path), "--portrait-a", str(portraits[0]), "--portrait-b", str(portraits[1]), "--out", str(existing)])
    assert code == 2 and "already exists" in capsys.readouterr().err


def test_unknown_placeholder_is_rejected(tmp_path):
    path = tmp_path / "config.json"
    config = config_for(WRITE_PNG)
    config["backends"][0]["command"].append("{typo}")
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="typo"):
        benchmark.load_config(path)


def test_example_config_is_valid_and_builds_expected_commands(tmp_path, portraits, capsys):
    config = benchmark.load_config(BENCHMARK_DIR / "config.example.json")
    assert len(config["scenes"]) == 4
    benchmark.run_benchmark(config, *portraits, tmp_path / "run", 30, dry_run=True)
    printed = capsys.readouterr().out
    assert "mflux-generate-qwen-edit" in printed and "mflux-generate-flux2-edit" in printed
