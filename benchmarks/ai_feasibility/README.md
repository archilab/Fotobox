# AI feasibility test

**Question:** can a local model put two real people into one scene, across 3–4 frames, keeping both recognizable and without swapping them?

This folder stands alone: nothing here is imported by the application, and its dependencies stay out of `pyproject.toml`. **Both candidates have run once on synthetic shapes (see Smoke test). They have not been tried on faces, so the question above is still open.**

## Candidates

Facts below come from the model cards and mflux READMEs, read on 2026-11-07. Check them again before downloading.

| Model | License | Download | Why test it |
|---|---|---|---|
| [Qwen-Image-Edit-2511](https://huggingface.co/Qwen/Qwen-Image-Edit-2511) | Apache 2.0 | ~58 GB full weights | The card claims fusion of two separate person images into a group shot |
| [FLUX.2 klein 4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) | Apache 2.0 | ~15 GB | Fast, with multi-reference editing and low memory use |

Not in the first round:

- **FLUX.2 klein 9B:** [non-commercial license](https://huggingface.co/black-forest-labs/FLUX.2-klein-9B) and gated download.
- **Qwen-Image 2.1:** secondary sources report a non-commercial license; the model card has not been checked.
- **FLUX 3 Image Klein:** listed by mflux, but its license and details are not confirmed from a primary source.
- **InstantID, PuLID and similar:** these work from face embeddings, which `docs/ARCHITECTURE.md` says to avoid.

## Runtimes

- **mflux (MLX) goes first.** It has a CLI for both candidates: `mflux-generate-qwen-edit` and `mflux-generate-flux2-edit`.
- **ComfyUI is not implemented here yet.** It needs a workflow exported from a real installation, so it follows once mflux results exist. One user [reports](https://lilting.ch/en/articles/comfyui-qwen-mps-bf16-slowdown) a large Qwen slowdown on Apple Silicon after a ComfyUI update; that is a single report on an M1 Max, not a finding.

## Before running

1. Approve each download (model source, size, license).
2. Provide two portraits that are synthetic or expressly authorized for this test. Keep them outside the repository.
3. Install mflux in its own environment so it does not touch the project:
   ```bash
   uv tool install mflux
   ```

## Running

Check the commands, then run. Copy `config.example.json` to adjust steps, scenes or backends.

```bash
python3 benchmarks/ai_feasibility/benchmark.py --config benchmarks/ai_feasibility/config.example.json --portrait-a /path/a.jpg --portrait-b /path/b.jpg --dry-run
```
```bash
python3 benchmarks/ai_feasibility/benchmark.py --config benchmarks/ai_feasibility/config.example.json --portrait-a /path/a.jpg --portrait-b /path/b.jpg
```

Each run writes to `runs/<timestamp>/`, which Git ignores:

- `results.json`: status, time and memory per image. Saved after each image, so a crash keeps earlier results.
- `review.html` and `scores.csv`: reference portraits beside each output, and a sheet to fill in.

## Scoring

Score by eye using `scores.csv`. No face embeddings are computed. The go/no-go thresholds below are **proposals until you agree to them**:

| Criterion | Proposed bar for "go" |
|---|---|
| Both people recognizable | Average of at least 4 out of 5 |
| Identity swap or merged faces | At most 1 output in 10 |
| Same person across scenes | Recognizably the same in a full strip of 4 |
| Reliability | Every run finishes on the MacBook, or the failure is explained |

If the only failures are out-of-memory on 64GB, mark that model "Studio only" rather than rejecting it.

## Smoke test (2026-11-07)

This shows that the tools, flags and memory work. It says nothing about identity: the inputs were two plain shape figures, one red and one blue, with no faces. Each model produced one image at 768×1024 and seed 11, using mflux 0.24.0, 8-bit quantization and the commands in `config.example.json`, on the MacBook Pro (Apple M3 Max, 64GB). Other apps were running.

| | FLUX.2 klein 4B | Qwen-Image-Edit-2511 |
|---|---|---|
| Steps | 4 | 30 |
| Time per step | ~10–14 s | ~71 s |
| Generation time | ~40 s | ~35.5 min |
| Total including first download | 575 s | 3793 s |
| Peak MLX memory (reported by mflux) | 10.30 GB | 39.07 GB |
| System memory used, before → peak | 32.86 → 42.75 GiB | 20.47 → 55.02 GiB |
| Output, judged by eye | Both figures present, colors partly mixed: both circles red, one body blue | Both figures present, colors kept apart |

One image per model can't rank them. The color mixing in the FLUX.2 output is only a reason to check for merged faces closely in the real run.

## What the measurements mean

- **Time and memory are for this machine.** Results on the MacBook do not predict the Mac Studio, so timings are measured again after the port.
- **Peak RSS can understate memory use**, because Metal allocations may not all appear in it. `peak_system_used_gib` is sampled system-wide, so other running apps affect it: close them and compare it with `system_used_before_gib`.
- **Never estimate a result.** Record only what was measured.

## Privacy

Outputs show real faces, so treat them like portraits: never commit or share them. Delete the run folder after you record your scores, keeping only `results.json` and `scores.csv`, which contain no image data.
