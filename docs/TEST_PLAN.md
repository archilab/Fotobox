# Tests and acceptance

**Included automated tests:** basic API health, consent gating, upload, seed matching, mock result PNG, no-seed error, renderer frame count, `.env` loading, provider rejection, CLI consent attestation, database connection closing, device tokens, session ownership, camera selection, retries, rotation, probing (simulated cameras; rotation and JPEG also use real OpenCV when installed), the AI benchmark tool (fake commands, no real model), retention, untracked files, file permissions, the pixel limit, session states, concurrent requests, repeated requests, atomic writes, upload limits, consent records, database migration, restart recovery, pool permission, separate pool files, seed import, fair selection, withdrawal, the audit trail, the provider boundary, the job queue, retries, timeouts, cancellation, resuming after a restart, the mflux adapter against a stand-in program, strip geometry, frame checks, output profiles and protection against corrupt results.

**Run:** `pytest -q`.

**Manual Pi acceptance test:** `scripts/m1_acceptance.sh` (see `docs/HARDWARE.md`) with an authorized volunteer or synthetic source; confirm image orientation by eye. For a full strip, also inspect the PNG and verify the visible `MOCK` label. Passed once on 2026-11-07 (see `docs/HARDWARE.md`); that run used `--no-process`. The M2 run produced a full strip from the Pi with a synthetic file, so a full strip from a real camera capture is still untested.

**Real model run (2026-11-07):** FLUX.2 from the Pi with fictional portraits: a complete strip, a restart mid-job, a cancellation, and fetching a strip in all three profiles. Not part of `pytest`, because it needs the model and takes minutes.

## M4 evaluation set
Agreed with the project owner on 2026-11-07: **5 pairs, one strip per pair.** At least 2 pairs must contain a real webcam photo with documented consent, and the set must include similar-looking people. Each strip is scored by eye with `scripts/evaluate_pairs.py`, which writes `review.html` and `scores.csv` to a folder outside the repository. It computes no face comparison.

Pass bars, confirmed by the owner on 2026-11-07: both people recognizable at an average of at least 4 out of 5; no swapped or merged people in any strip; the same people across all frames of a strip.

**Result: passed on 2026-11-07, scored by the owner by eye.** Average 4.6 out of 5, with no swaps or merged faces, and the same people across all frames.

| Pair | Source | Time | Person A | Person B |
|---|---|---|---|---|
| different-people | fictional | 169 s | 5 | 5 |
| similar-men | fictional | 188 s | 5 | 5 |
| similar-women | fictional | 188 s | 5 | 5 |
| webcam-with-different-person | webcam, operator's own consent | 200 s | 4 | 4 |
| webcam-with-similar-person | webcam, operator's own consent | 221 s | 4 | 4 |

Limits of this result: the webcam pairs use one real person, the operator, with a fictional counterpart, from one photo taken from below and slightly tilted, so **two real faces together remain untested**. The webcam strips scored lower than the fictional ones. During generation of the set, the assistant's own impression was that the real face drifted more than this score suggests. The owner's score decides, but this is worth rechecking with more real participants before public use. Evaluation folders are not covered by the server's automatic deletion: delete them by hand after scoring, keeping only `results.json` and `scores.csv`.

These scores were for the earlier documentary scenes (`scenes-1`). The photo booth micro-stories (ADR-019) have only been tried with fictional pairs and are not scored yet.

**Kiosk:** automated tests cover the key flow, discarding of early presses, the notice and its version, refusals, the timer and the terminal input. A run on the real Pi used a test image, not the camera. Showing a strip on the real monitor was confirmed by eye.

**Outstanding test categories:** user consent proofs; EXIF stripping verification; repeated camera reconnection; AI identity retention with two real people; 50-run performance; image provenance, prompt safety and wrong-face checks; a real print.

Do not use real participant images in CI, screenshots or fixtures.
