#!/usr/bin/env python3
"""Standalone AI feasibility benchmark. Standard library only; not imported by the application.

Runs image-generation commands from a config file, measures them, and writes results.json
plus a review page for scoring by eye. See README.md in this folder.
"""
from __future__ import annotations
import argparse
import html
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SIGNATURE = b"\xff\xd8\xff"
PLACEHOLDERS = {"portrait_a", "portrait_b", "prompt", "seed", "output"}
GIB = 1024 ** 3


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if not config.get("backends") or not config.get("scenes") or not config.get("seeds"):
        raise ValueError("config needs non-empty 'backends', 'scenes' and 'seeds'")
    for backend in config["backends"]:
        for part in backend["command"]:
            unknown = set(re.findall(r"\{(\w+)\}", part)) - PLACEHOLDERS
            if unknown:
                raise ValueError(f"backend {backend['name']!r} uses unknown placeholders: {sorted(unknown)}")
    return config


def build_command(template: list[str], values: dict[str, str]) -> list[str]:
    # Plain replacement, never a shell: prompts cannot inject commands or break on braces.
    command = []
    for part in template:
        for key, value in values.items():
            part = part.replace("{" + key + "}", value)
        command.append(part)
    return command


def system_memory_used_bytes() -> int | None:
    """Memory in use system-wide on macOS. Metal allocations do not all appear in process RSS."""
    if sys.platform != "darwin":
        return None
    try:
        text = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    size = re.search(r"page size of (\d+) bytes", text)
    if not size:
        return None
    total = 0
    for label in ("Pages active", "Pages wired down", "Pages occupied by compressor"):
        match = re.search(rf"{label}:\s+(\d+)", text)
        if match:
            total += int(match.group(1))
    return total * int(size.group(1))


class MemorySampler:
    """Records peak system memory during a run, for comparison with the level before it."""

    def __init__(self, interval: float = 1.0) -> None:
        self.interval = interval
        self.baseline = system_memory_used_bytes()
        self.peak = self.baseline
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            used = system_memory_used_bytes()
            if used is not None and (self.peak is None or used > self.peak):
                self.peak = used

    def __enter__(self) -> "MemorySampler":
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join()


def max_rss_bytes(ru_maxrss: int) -> int:
    return ru_maxrss if sys.platform == "darwin" else ru_maxrss * 1024  # Linux reports KiB.


def run_command(command: list[str], log_path: Path, timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    with log_path.open("wb") as log, MemorySampler() as memory:
        try:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        except OSError as exc:
            return {"status": "not_runnable", "error": type(exc).__name__, "seconds": 0.0, "peak_rss_gib": None, "peak_system_used_gib": None, "system_used_before_gib": None}
        timed_out = False
        while True:
            pid, wait_status, usage = os.wait4(process.pid, os.WNOHANG)
            if pid:
                break
            if time.monotonic() - started > timeout:
                timed_out = True
                process.kill()
                _, wait_status, usage = os.wait4(process.pid, 0)
                break
            time.sleep(0.2)
        process.returncode = os.waitstatus_to_exitcode(wait_status)  # Already reaped above.
    status = "timeout" if timed_out else ("ok" if process.returncode == 0 else "failed")
    return {
        "status": status,
        "exit_code": process.returncode,
        "seconds": round(time.monotonic() - started, 2),
        "peak_rss_gib": round(max_rss_bytes(usage.ru_maxrss) / GIB, 2),
        "peak_system_used_gib": round(memory.peak / GIB, 2) if memory.peak is not None else None,
        "system_used_before_gib": round(memory.baseline / GIB, 2) if memory.baseline is not None else None,
    }


def is_image(path: Path) -> bool:
    if not path.is_file():
        return False
    with path.open("rb") as handle:
        start = handle.read(8)
    return start.startswith(PNG_SIGNATURE) or start.startswith(JPEG_SIGNATURE)


def summarize(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary = []
    for backend in sorted({r["backend"] for r in results}):
        rows = [r for r in results if r["backend"] == backend]
        good = [r for r in rows if r["status"] == "ok"]
        memory = [r["peak_system_used_gib"] for r in good if r["peak_system_used_gib"] is not None]
        summary.append({
            "backend": backend,
            "runs": len(rows),
            "succeeded": len(good),
            "median_seconds": statistics.median(r["seconds"] for r in good) if good else None,
            "max_peak_system_used_gib": max(memory) if memory else None,
        })
    return summary


def write_review(run_dir: Path, results: list[dict[str, Any]], portrait_a: Path, portrait_b: Path) -> None:
    """Write a side-by-side page and a CSV to fill in. Scoring is by eye, with no face embeddings."""
    rows = []
    for r in results:
        name = html.escape(f"{r['backend']} / {r['scene']} / seed {r['seed']}")
        if r["status"] == "ok":
            body = f'<img src="{html.escape(Path(r["output"]).name, quote=True)}" alt="output">'
        else:
            body = f"<p>{html.escape(r['status'])}: see {html.escape(r['log'])}</p>"
        prompt = html.escape(r["prompt"])
        rows.append(f"<section><h2>{name}</h2><p>{prompt}</p><p>{r['seconds']} s</p>{body}</section>")
    page = (
        "<!doctype html><meta charset=utf-8><title>AI benchmark review</title>"
        "<style>body{font:15px system-ui;margin:24px}img{max-height:420px;max-width:100%}"
        "section{border-top:1px solid #aaa;padding:12px 0}.refs img{max-height:220px;margin-right:12px}</style>"
        "<h1>AI benchmark review</h1><p>Contains personal images: stays local, delete after scoring.</p>"
        f'<div class=refs><h2>Reference portraits</h2><img src="{html.escape(portrait_a.resolve().as_uri(), quote=True)}" alt="A">'
        f'<img src="{html.escape(portrait_b.resolve().as_uri(), quote=True)}" alt="B"></div>' + "".join(rows)
    )
    (run_dir / "review.html").write_text(page, encoding="utf-8")
    lines = ["backend,scene,seed,person_a_1to5,person_b_1to5,identity_swap_yes_no,realism_1to5,notes"]
    lines += [f"{r['backend']},{r['scene']},{r['seed']},,,,," for r in results if r["status"] == "ok"]
    (run_dir / "scores.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value)


def run_benchmark(config: dict[str, Any], portrait_a: Path, portrait_b: Path, run_dir: Path, timeout: float, dry_run: bool = False) -> list[dict[str, Any]]:
    run_dir.mkdir(parents=True, exist_ok=dry_run)
    results: list[dict[str, Any]] = []
    for backend in config["backends"]:
        for scene in config["scenes"]:
            for seed in config["seeds"]:
                stem = f"{safe_name(backend['name'])}__{safe_name(scene['id'])}__seed{seed}"
                output = run_dir / f"{stem}.png"
                command = build_command(backend["command"], {
                    "portrait_a": str(portrait_a), "portrait_b": str(portrait_b),
                    "prompt": scene["prompt"], "seed": str(seed), "output": str(output),
                })
                if dry_run:
                    print(subprocess.list2cmdline(command))
                    continue
                print(f"Running {stem}", flush=True)
                record = run_command(command, run_dir / f"{stem}.log", timeout)
                if record["status"] == "ok" and not is_image(output):
                    record["status"] = "no_valid_output"
                # Records hold no portrait paths or image data.
                results.append({"backend": backend["name"], "scene": scene["id"], "seed": seed, "prompt": scene["prompt"], "output": str(output), "log": f"{stem}.log", **record})
                write_results(run_dir, config, results)
                print(f"  {record['status']} in {record['seconds']} s", flush=True)
    if not dry_run:
        write_review(run_dir, results, portrait_a, portrait_b)
    return results


def write_results(run_dir: Path, config: dict[str, Any], results: list[dict[str, Any]]) -> None:
    # Rewritten after every run, so a crash or a long overnight run keeps what it measured.
    document = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "host": os.uname().machine + " " + sys.platform,
        "note": config.get("note", ""),
        "summary": summarize(results),
        "runs": results,
    }
    (run_dir / "results.json").write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--portrait-a", type=Path, required=True, help="Synthetic or expressly authorized portrait")
    parser.add_argument("--portrait-b", type=Path, required=True, help="Synthetic or expressly authorized portrait")
    parser.add_argument("--out", type=Path, help="New run folder (default: a timestamped folder under runs/)")
    parser.add_argument("--timeout", type=float, default=1800, help="Seconds allowed per image (default: 1800)")
    parser.add_argument("--dry-run", action="store_true", help="Print the commands without running them")
    args = parser.parse_args(argv)
    for portrait in (args.portrait_a, args.portrait_b):
        if not portrait.is_file():
            print(f"Error: portrait not found: {portrait}", file=sys.stderr)
            return 2
    try:
        config = load_config(args.config)
    except (OSError, ValueError, KeyError) as exc:
        print(f"Error: invalid config: {exc}", file=sys.stderr)
        return 2
    run_dir = args.out or here / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    if not args.dry_run and run_dir.exists():
        print(f"Error: run folder already exists: {run_dir}", file=sys.stderr)
        return 2
    results = run_benchmark(config, args.portrait_a, args.portrait_b, run_dir, args.timeout, args.dry_run)
    if args.dry_run:
        return 0
    print(json.dumps(summarize(results), indent=2))
    print(f"Review page: {run_dir / 'review.html'}")
    return 0 if all(r["status"] == "ok" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
