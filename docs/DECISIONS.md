# Architecture Decision Records

## ADR-001: Minimal Pi hardware
Phase 1 is one Raspberry Pi with standard UVC webcam only. No touchscreen/printer until later.

## ADR-002: Central Mac Studio service
FastAPI hosts API/metadata/matching; AI worker lives on Mac Studio via replaceable provider interface.

## ADR-003: Portrait pool
When no live counterpart exists, use authorized temporary portrait pool, then explicitly authorized seed images. Live matching introduced only with multi-box phase.

## ADR-004: Delivery output
Artifact is a vertically rendered PNG stored on server and retrievable by internal Pi client. This is NOT a public digital image retrieval product.

## ADR-005: MVP mock
The runnable vertical slice generates an unmistakably labelled synthetic mock composition. Model benchmarking and true two-person photographic composition postponed to AI milestone.

## ADR-006: Privacy separation
Consent for immediate strip and later pool use are separate purposes. CLI consent is external test procedure only; touchscreen UI comes later.

## ADR-007: Explicit operator attestation in the CLI
`capture` no longer sends `documented_externally: true` automatically. The operator must pass `--attest-documented-consent`, and without it the CLI stops before using the camera or network. This is still an operator assertion, not proof of consent.

## ADR-008: Development host and early AI test
Development runs on a MacBook Pro M3 (64GB) with the Pi on the same LAN; the server is ported to the Mac Studio M3 Ultra (256GB) before M6, and NetBird or Tailscale replaces the plain LAN later. Host-specific values stay in configuration. Because identity retention is the largest product risk, a standalone AI benchmark runs alongside M1, outside the application code, and its result is reviewed before M2. MacBook results show feasibility only; timings are measured again on the Mac Studio.

## ADR-009: Standard-library `.env` loading
The server reads `.env` with a small built-in parser instead of adding a dependency. Variables already set in the shell take precedence. Loading happens at server startup, not at import, so tests are not affected by a developer's local `.env`.

## ADR-010: Plain LAN with device tokens during development (accepted risk)
**Status: chosen by the project owner on 2026-11-07; reviewed the same day (see `docs/SECURITY.md`), and the required fixes are in ADR-013.** `CLAUDE.md` asks for authentication/TLS and a review before remote access. For development only, the server binds to the Mac's LAN address with device tokens but without encryption, so portraits and tokens can be read by others on that network. This is limited to a trusted LAN and to synthetic or expressly authorized images. It does not cover exhibition visitors or public networks. NetBird or Tailscale replaces it later and needs no code change, only a different server address.

## ADR-011: Deny-by-default device authentication
Authentication runs in middleware and covers every path except `health`, including `/docs`. A route dependency would be too late, because FastAPI parses multipart bodies before running dependencies, letting unauthenticated clients spool uploads to disk. Tokens are 256-bit random values stored as SHA-256 hashes; a slow password hash is not needed for values with this much entropy. `box_id` now comes from the authenticated device, not the request body. The `POST /admin/cleanup` endpoint was removed in favour of a local admin command, which leaves no admin surface on the network.

## ADR-012: Headless OpenCV on the client
The `client-camera` extra uses `opencv-python-headless`, because the CLI needs no GUI functions and the Pi client must not depend on a display.

## ADR-013: Strips expire with their portraits, and cleanup runs automatically
The ADR-010 review found that strips outlived retention even though each one contains portraits. A strip is now unavailable as soon as its own or its pool-matched portrait expires, and cleanup then deletes it. Seed images do not count, because they are not temporary. So a strip without pool consent lasts about one hour. The server runs cleanup at startup and every `PHOTOBOX_CLEANUP_INTERVAL_SECONDS` (default 300), which brings forward the basic part of M3's periodic deletion; revocation and queued jobs remain in M3. Cleanup also deletes files in `portraits` and `generated` that no record refers to, once they are an hour old, so requests still in progress are left alone. Storage folders are 0700 and stored files 0600, regardless of umask.

## ADR-014: FLUX.2 klein 4B through mflux as the first image model
**Go, decided by the project owner on 2026-11-07, who judged the quality sufficient** after two runs on fictional faces, including two similar-looking people (see `benchmarks/ai_feasibility/README.md`). FLUX.2 klein 4B is Apache 2.0 licensed, takes about 40–45 s per image and peaks at about 10 GB of MLX memory on the MacBook. The Qwen-Image-Edit-2511 run on faces and the ComfyUI comparison are dropped. The proposed numeric thresholds and `scores.csv` were not used, so this is the owner's overall judgement rather than a score. Known limits at this decision: similar faces drift toward each other, and real webcam photos were untested. M4's evaluation set later passed, including one real person (`docs/TEST_PLAN.md`); two real faces together are still untested. The model is reached only through the M4 provider adapter, never from route handlers, and `MOCK` stays until then.

## ADR-015: State changes through conditional updates, on one server process
M2 uses SQLite itself rather than a separate lock: each state change is a conditional `UPDATE`, which one concurrent request wins, and which holds the write lock until the file and record commit. Repeats are recognized by an `Idempotency-Key`, stored on the session or portrait rather than in a general table, because each request type has one natural owner. The upload limit lives in memory, so it resets on restart and only works with one process, which is acceptable for one trusted Pi; `docs/ARCHITECTURE.md` requires one process. Consent moved from two flags to one record per purpose, with a policy version and an optional outside reference, and existing data is migrated by versioned migrations (`docs/DATA_MODEL.md`). This still records an operator's statement, not proof of consent.

## ADR-016: Pool copies, admin-only seeds, operator-run withdrawal
Pool consent now creates a second file in `pool_portraits`, with its own lifetime, so a session image can expire within an hour while the pool copy lasts longer. Portraits stored in `portraits` as pool images before this change stay usable until they expire. Matching checks expiry and an unwithdrawn pool consent at the moment of use. Among eligible images, each is chosen with weight `1 / (1 + use_count)`, so less-used images come up more often without any being excluded, and pool images are preferred over seeds.

Seeds come only from `admin seed add`, which requires a rights attestation and reference, validates the image and removes its metadata. Files placed directly in the folder are ignored, which also means seeds from before this change must be imported again. Strips containing a removed or no longer registered seed are deleted.

Withdrawal is a local admin action with no HTTP endpoint, because phase 1 has no public retrieval or self-service. At consent, the operator receives a random code, once, to give to the participant; only its hash is stored. Withdrawal, by code or session id, marks consent withdrawn and deletes the session's images and every strip containing them, including other sessions' strips. A render in progress cannot complete afterwards. The audit trail records consent, use, deletion and seed events with opaque ids only. It has no retention limit yet, and that needs a decision before public use.

## ADR-017: Asynchronous jobs in SQLite, one worker thread, mflux as a subprocess
`POST /process` now only queues, for every provider, and clients poll the session; a model taking minutes per strip cannot hold a request open, and the API must not depend on the provider. Jobs live in `generation_jobs`, so no separate queue service is needed. One worker thread inside the server runs one job at a time, because a model uses most of the memory. The interface works per frame, so a retry or restart continues from finished frames. Attempts are bounded and also count interruptions, so a job that keeps crashing the server stops.

The FLUX.2 adapter runs mflux as a separate process in its own process group. This keeps its dependencies out of the project, and lets a timeout, cancellation or shutdown stop it completely. It runs with `HF_HUB_OFFLINE=1`, because generating from portraits must never involve the network, and its log is discarded because it can contain paths. A strip is only kept if the images it was started with are still valid at the end, and otherwise the job retries with a new counterpart.

The mock now requires `PHOTOBOX_DEVELOPER_MODE=true`. A failed job fails visibly and never falls back to the mock, so a mock can never be mistaken for AI output. Strips show their origin in the footer.

## ADR-018: Output profiles made on request, frames checked as images only
Only the colour strip is stored. Grayscale and thermal versions are made when requested, so there are no further copies of people's images to expire or withdraw. Frame checks cover resolution, readability and blank images. They deliberately do not compare the people across frames, because that would need face comparison, which the architecture rules out, so subject consistency is judged by eye. The layout is in pixels, and `PHOTOBOX_STRIP_WIDTH` and the thermal settings stay provisional until a printer is chosen: dithering should happen at the printer's real pixel width.

## ADR-019: Photo booth micro-stories, extra people allowed, face count optional
At the owner's request, strips look like a photo booth rather than four documentary scenes: one plain backdrop per strip, close 4:3 frames shot at eye level, even borders and a dated footer. Each strip tells one of 100 micro-stories (`photobox_server/stories.py`), chosen with its backdrop from the job's random seed, so a resumed job keeps both. All frames share a seed. The two participants are the main characters, and the opening frame keeps both faces clearly visible. Invented extra people are allowed, adults only. Stories avoid romance, unkindness about looks, danger and weapons, because matches can pair strangers; tests check for this.

A face count (`photobox_server/faces.py`, OpenCV 4 Haar cascade `alt2`) can limit invented people through `PHOTOBOX_MAX_FACES`, and is off by default since extras are welcome. It only counts faces and never identifies anyone. Its settings were chosen on 36 earlier frames, where it caught both frames with an invented third person, with one false alarm. In a later run it caught the one invented person and had no false alarms. It needs the `server-ai` extra, because OpenCV 5 no longer includes the cascade classifier.

The M4 evaluation scores (`docs/TEST_PLAN.md`) were for the earlier scenes and do not carry over to these prompts.
