# Tests and acceptance

**Included automated tests:** basic API health, consent gating, upload, seed matching, mock result PNG, no-seed error, renderer frame count, `.env` loading, provider rejection, CLI consent attestation, database connection closing, device tokens, session ownership, camera selection, retries, rotation, probing (simulated cameras; rotation and JPEG also use real OpenCV when installed), the AI benchmark tool (fake commands, no real model), retention, untracked files, file permissions, the pixel limit, session states, concurrent requests, repeated requests, atomic writes, upload limits, consent records, database migration, restart recovery, pool permission, separate pool files, seed import, fair selection, withdrawal, the audit trail, the provider boundary, the job queue, retries, timeouts, cancellation, resuming after a restart, and the mflux adapter against a stand-in program.

**Run:** `pytest -q`.

**Manual Pi acceptance test:** `scripts/m1_acceptance.sh` (see `docs/HARDWARE.md`) with an authorized volunteer or synthetic source; confirm image orientation by eye. For a full strip, also inspect the PNG and verify the visible `MOCK` label. Passed once on 2026-11-07 (see `docs/HARDWARE.md`); that run used `--no-process`. The M2 run produced a full strip from the Pi with a synthetic file, so a full strip from a real camera capture is still untested.

**Real model run (2026-11-07):** FLUX.2 from the Pi with fictional portraits: a complete strip, a restart mid-job and a cancellation. Not part of `pytest`, because it needs the model and takes minutes.

**Outstanding test categories:** user consent proofs; EXIF stripping verification; repeated camera reconnection; AI identity retention on an agreed evaluation set; 50-run performance; image provenance, prompt safety and wrong-face checks.

Do not use real participant images in CI, screenshots or fixtures.
