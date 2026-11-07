# Milestone plan (implement sequentially with Claude Code)

## M0 — Bootstrap [included scaffold]
- [x] Monorepo, `CLAUDE.md`, project settings, docs and simple tests
- [x] Server boot + health check
- [x] CLI boot and mock endpoint workflow
- [x] Protect sensitive files in `.gitignore`; first commit
- [x] `.env.example`, `.env` loading and startup rejection of unimplemented providers
- [x] Explicit CLI consent attestation; reliable database connection closing
- [x] Verify with `pytest -q` and a clean install on the development MacBook

## Early AI test — alongside M1 (see ADR-008)
- [x] Benchmark tool and model shortlist, in `benchmarks/ai_feasibility/`, outside the application code and its dependencies
- [ ] Approve each model's source, size and license before download
- [ ] Run mflux on both candidates, then add ComfyUI for comparison
- [ ] Use only synthetic or expressly authorized portraits, kept out of Git
- [ ] Record measured results; never estimate or fabricate them
**Acceptance:** written results and a go/no-go decision, reviewed before M2 starts.

## M1 — Raspberry Pi camera and networking
- [x] Webcam selection by index and stable `/dev/v4l/by-id` path
- [x] Camera probing (formats, frame size, FPS, optional focus/exposure)
- [x] Retry and proper resource cleanup; practical JPEG quality/rotation tests
- [x] Add per-device authentication **before remotely binding**
- [ ] Test connectivity from the real Pi to the Mac on the trusted LAN (ADR-010)
- [ ] Run `scripts/m1_acceptance.sh` on the Pi; code so far is tested with simulated cameras only
**Acceptance:** 20 consecutive CLI captures with correct orientation; invalid camera returns clear error; authenticated Pi→Mac upload succeeds.

## M2 — Session persistence and upload hardening
- [ ] Define explicit session state transition table and concurrent access strategy
- [ ] Idempotency key for uploads; atomic write + DB transaction consistency
- [ ] Upload validation, max dimensions, per-device throttling (device tokens landed in M1)
- [ ] Migrate normalized consent records with version and external attestation evidence
**Acceptance:** repeat request does not duplicate; unauthorized cannot download; restart doesn't lose uploaded state.

## M3 — Pool selection, deletion, withdrawal
- [ ] Clear scope-based pool permission and audit trail
- [ ] Make seed import admin-only; validity checks, weighted random fairness
- [ ] Separate temporary session images from reusable pool files
- [ ] Automatic periodic deletion and background cleanup of images, output, temp jobs
- [ ] Private revocation workflow for future public users
**Acceptance:** expired/revoked never match; synthetic test images purge; pool-empty with no seed yields clear error.

## M4 — AI provider abstraction
- [ ] `ImageGenerationProvider` interface (input two portraits, prompt config, frame count, output images, metrics)
- [ ] Durable queue, worker, progress/status, timeouts, cancellation and bounded retries
- [ ] Bench ComfyUI vs direct MLX on Mac Studio with test portraits and measured speed/identity retention
- [ ] Deterministic mock fallback ONLY in developer mode, visibly labelled
**Acceptance:** provider swap does not change API; job survives service restart; real model creates two-person scene without identity swap on an agreed evaluation set.

## M5 — Photostrip composition and quality
- [ ] 3/4 separate frames from image provider, optional contact-sheet crop
- [ ] Frame selection and resolution checks; consistent subjects across frames
- [ ] Printer-agnostic layout with physical dimensions deferred until hardware phase
- [ ] grayscale, brightness, contrast, dithering profile outputs
**Acceptance:** 3/4 frames and expected geometry; print-ready PNG; failures don't create corrupt results.

## Mac Studio port — before M6
- [ ] Move the server to the Mac Studio using configuration only; write a setup guide
- [ ] Run `pytest -q` there with actual dependencies; repeat the AI benchmark
**Acceptance:** tests pass on the Mac Studio and its AI timings are recorded.

## M6 — End-to-end MVP acceptance
- [ ] Test 50 sessions, including upload errors, AI failures, server restart, cleanup
- [ ] Operator runbook / logs without personal info
- [ ] Performance report: camera capture, upload, queue, AI generation, PNG render, end-to-end latency
- [ ] No touchscreen, no printer, no additional Pi in this phase
**Acceptance:** one Pi produces photographic AI PNG strip via Mac backend repeatedly under test conditions.

## Later phases — NOT in v0.5 MVP
- Phase 2: touchscreen and accessible consent + countdown
- Phase 3: thermoprinter + idempotent printing
- Phase 4: multi-Pi online presence, real-time matching, both sites get printed output
