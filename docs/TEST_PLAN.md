# Tests and acceptance

**Included automated tests:** basic API health, consent gating, upload, seed matching, mock result PNG, no-seed error, renderer frame count, `.env` loading, provider rejection, CLI consent attestation and database connection closing.

**Run:** `pytest -q`.

**Manual Pi smoke test:** `camera-test --camera 0`; test capture with authorized volunteer/synthetic source; confirm image orientation and server receipt; inspect png and verify visible `MOCK` label.

**Outstanding test categories:** device authentication; user consent proofs; automated retention and revocation; EXIF stripping verification; duplicate-session upload; corrupted images; concurrent workers; repeated camera reconnection; AI identity retention; 50-run performance; image provenance, prompt safety and wrong-face checks.

Do not use real participant images in CI, screenshots or fixtures.
