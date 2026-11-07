# Tests and acceptance

**Included automated tests:** basic API health, consent gating, upload, seed matching, mock result PNG, no-seed error, renderer frame count, `.env` loading, provider rejection, CLI consent attestation, database connection closing, device tokens, session ownership, camera selection, retries, rotation, probing (simulated cameras; rotation and JPEG also use real OpenCV when installed), the AI benchmark tool (fake commands, no real model), and retention, untracked files, file permissions and the pixel limit.

**Run:** `pytest -q`.

**Manual Pi acceptance test:** `scripts/m1_acceptance.sh` (see `docs/HARDWARE.md`) with an authorized volunteer or synthetic source; confirm image orientation by eye. For a full strip, also inspect the PNG and verify the visible `MOCK` label. Passed once on 2026-11-07 (see `docs/HARDWARE.md`); that run used `--no-process`, so a full strip from a real camera capture is still untested.

**Outstanding test categories:** user consent proofs; revocation; EXIF stripping verification; duplicate-session upload; corrupted images; concurrent workers; repeated camera reconnection; AI identity retention; 50-run performance; image provenance, prompt safety and wrong-face checks.

Do not use real participant images in CI, screenshots or fixtures.
