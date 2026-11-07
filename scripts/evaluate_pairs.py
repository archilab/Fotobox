"""Make one strip per portrait pair for M4's evaluation set, and a page to score them by eye.

    PHOTOBOX_PROVIDER=mflux-flux2 python scripts/evaluate_pairs.py --pairs pairs.json --out /outside/repo/run --attest-documented-consent

pairs.json: {"pairs": [{"id": "similar-men", "source": "fictional", "a": "/path/a.png", "b": "/path/b.png"}]}
`source` is "fictional" or "webcam-consented". Uses the application's provider, prompts, frame checks
and strip layout. No face comparison is computed. Results show faces: keep them out of Git and delete
the folder after scoring, keeping only results.json and scores.csv.
"""
from __future__ import annotations
import argparse
import csv
import html
import json
import shutil
import sys
import time
from pathlib import Path
from PIL import Image
from photobox_server.config import get_frame_count, get_provider, load_env_file
from photobox_server.core import validate_image, write_private_image
from photobox_server.prompts import PROMPT_VERSION, prompts_for
from photobox_server.providers import FrameRequest, GenerationError, create_provider
from photobox_server.strip import dated_footer, frame_problem, render_strip

REPOSITORY = Path(__file__).resolve().parents[1]
SOURCES = {"fictional", "webcam-consented"}
SCORE_COLUMNS = ["pair", "source", "person_a_1to5", "person_b_1to5", "swapped_yes_no", "merged_yes_no", "same_people_all_frames_yes_no", "notes"]
FRAME_TIMEOUT_SECONDS = 600
MAX_FRAME_ATTEMPTS = 3


def load_pairs(path: Path) -> list[dict]:
    pairs = json.loads(path.read_text(encoding="utf-8"))["pairs"]
    seen = set()
    for pair in pairs:
        if pair["source"] not in SOURCES:
            raise ValueError(f"pair {pair['id']!r}: source must be one of {sorted(SOURCES)}")
        if not pair["id"].replace("-", "").isalnum() or pair["id"] in seen:
            raise ValueError(f"pair id {pair['id']!r} must be unique, with only letters, digits and '-'")
        seen.add(pair["id"])
        for side in ("a", "b"):
            validate_image(Path(pair[side]).read_bytes())
    return pairs


def make_strip(provider, pair: dict, seed: int, folder: Path) -> dict:
    frames_folder = folder / "frames"
    frames_folder.mkdir(parents=True, mode=0o700)
    started = time.monotonic()
    images, frame_seconds, peak, rejected = [], [], None, []
    try:
        total = get_frame_count()
        prompts = prompts_for(total, seed)
        for index in range(total):
            output = frames_folder / f"frame-{index + 1}.png"
            # A bad frame gets new seeds, as the application's retries do.
            for attempt in range(MAX_FRAME_ATTEMPTS):
                request = FrameRequest(Path(pair["a"]), Path(pair["b"]), prompts[index], index, total, seed + 1000 * attempt, output, FRAME_TIMEOUT_SECONDS)
                metrics = provider.generate_frame(request, lambda: False)
                problem = frame_problem(output, getattr(provider, "max_faces", None))
                if problem is None:
                    break
                print(f"  frame {index + 1} attempt {attempt + 1}: {problem}", flush=True)
                rejected.append({"frame": index + 1, "problem": problem})
            else:
                raise GenerationError(f"frame {index + 1} failed its check: {problem}")
            frame_seconds.append(round(metrics.seconds, 2))
            peak = metrics.peak_memory_gb or peak
            with Image.open(output) as image:
                images.append(image.convert("RGB"))
        write_private_image(render_strip(images, dated_footer(provider.strip_footer)), folder / "strip.png", "PNG")
    finally:
        shutil.rmtree(frames_folder, ignore_errors=True)
    return {"seconds": round(time.monotonic() - started, 2), "frame_seconds": frame_seconds, "peak_memory_gb": peak, "rejected_frames": rejected}


def write_review(out: Path, results: list[dict], pairs: list[dict]) -> None:
    rows = []
    for pair, result in zip(pairs, results):
        strip = f'<img class="strip" src="{html.escape(pair["id"])}/strip.png" alt="strip">' if result["status"] == "ok" else f"<p>Failed: {html.escape(result['error'])}</p>"
        portraits = "".join(f'<img class="ref" src="{html.escape(Path(pair[side]).resolve().as_uri())}" alt="portrait {side}">' for side in ("a", "b"))
        rows.append(f"<section><h2>{html.escape(pair['id'])} ({html.escape(pair['source'])})</h2><div>{portraits}</div>{strip}</section>")
    (out / "review.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>Evaluation set</title>"
        "<style>body{font:15px system-ui;margin:24px}section{margin-bottom:48px}.ref{height:220px;margin-right:12px}.strip{width:420px;display:block;margin-top:12px}</style>"
        "<h1>M4 evaluation set</h1><p>Score each pair by eye in scores.csv.</p>" + "".join(rows),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pairs", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="New folder outside the repository")
    parser.add_argument("--seed", type=int, default=1000)
    parser.add_argument("--attest-documented-consent", action="store_true", help="Required: every real person shown gave documented consent to this test, or the portraits are fictional")
    args = parser.parse_args(argv)
    if not args.attest_documented_consent:
        parser.error("requires --attest-documented-consent: use it only when every real person shown gave documented consent, or the portraits are fictional")
    out = args.out.resolve()
    if out.is_relative_to(REPOSITORY):
        parser.error("--out must be outside the repository, because results show faces")
    if out.exists() and any(out.iterdir()):
        parser.error("--out must be a new or empty folder")
    load_env_file()
    try:
        pairs = load_pairs(args.pairs)
    except (OSError, KeyError, ValueError) as exc:
        print(f"Error in pairs file: {exc}", file=sys.stderr)
        return 1
    provider = create_provider(get_provider())
    out.mkdir(parents=True, exist_ok=True)
    out.chmod(0o700)
    results = []
    for number, pair in enumerate(pairs):
        print(f"{pair['id']}: generating", flush=True)
        result = {"pair": pair["id"], "source": pair["source"], "status": "ok"}
        try:
            result |= make_strip(provider, pair, args.seed + 101 * number, out / pair["id"])
            print(f"  ok in {result['seconds']} s", flush=True)
        except GenerationError as exc:
            result |= {"status": "failed", "error": str(exc)}
            print(f"  FAILED: {exc}", flush=True)
        results.append(result)
    summary = {"provider": provider.name, "prompt_version": PROMPT_VERSION, "seed": args.seed, "results": results}
    (out / "results.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    with open(out / "scores.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(SCORE_COLUMNS)
        for result in results:
            writer.writerow([result["pair"], result["source"]] + [""] * (len(SCORE_COLUMNS) - 2))
    write_review(out, results, pairs)
    print(f"Review page: {out / 'review.html'}")
    return 1 if any(r["status"] != "ok" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
