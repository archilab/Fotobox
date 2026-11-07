# Tests and acceptance

**Included automated tests:** basic API health, consent gating, upload, seed matching, mock result PNG, no-seed error, renderer frame count, `.env` loading, provider rejection, CLI consent attestation, database connection closing, device tokens, session ownership, camera selection, retries, rotation, probing (simulated cameras; rotation and JPEG also use real OpenCV when installed) and the AI benchmark tool (fake commands, no real model).

**Run:** `pytest -q`.

**Manual Pi acceptance test:** `scripts/m1_acceptance.sh` (see `docs/HARDWARE.md`) with an authorized volunteer or synthetic source; confirm image orientation by eye. For a full strip, also inspect the PNG and verify the visible `MOCK` label.

**Outstanding test categories:** user consent proofs; automated retention and revocation; EXIF stripping verification; duplicate-session upload; corrupted images; concurrent workers; repeated camera reconnection; AI identity retention; 50-run performance; image provenance, prompt safety and wrong-face checks.

Do not use real participant images in CI, screenshots or fixtures.
