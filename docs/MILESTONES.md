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
- [x] Approve each model's source, size and license before download
- [x] Run mflux on FLUX.2 klein 4B with fictional faces. Qwen-Image-Edit-2511 ran on shapes only; its face run and the ComfyUI comparison were dropped (ADR-014)
- [x] Use only synthetic or expressly authorized portraits, kept out of Git
- [x] Record measured results; never estimate or fabricate them (see `benchmarks/ai_feasibility/README.md`; continues with each new run)
**Acceptance:** written results and a go/no-go decision, reviewed before M2 starts. **Met: go for FLUX.2 klein 4B (ADR-014).**

## M1 — Raspberry Pi camera and networking
- [x] Webcam selection by index and stable `/dev/v4l/by-id` path
- [x] Camera probing (formats, frame size, FPS, optional focus/exposure)
- [x] Retry and proper resource cleanup; practical JPEG quality/rotation tests
- [x] Add per-device authentication **before remotely binding**
- [x] Test connectivity from the real Pi to the Mac on the trusted LAN (ADR-010)
- [x] Run `scripts/m1_acceptance.sh` on the Pi: passed 2026-11-07 (see `docs/HARDWARE.md`)
**Acceptance:** 20 consecutive CLI captures with correct orientation; invalid camera returns clear error; authenticated Pi→Mac upload succeeds. **Met for a scene without people, at the default 640×480.** Repeated camera reconnection is still untested. The ADR-010 review is recorded in `docs/SECURITY.md`, and its findings 1–4 are fixed (ADR-013).

## M2 — Session persistence and upload hardening
- [x] Define explicit session state transition table and concurrent access strategy (`docs/ARCHITECTURE.md`, ADR-015)
- [x] Idempotency key for uploads; atomic write + DB transaction consistency
- [x] Upload validation, max dimensions, per-device throttling (device tokens landed in M1)
- [x] Migrate normalized consent records with version and external attestation evidence
**Acceptance:** repeat request does not duplicate; unauthorized cannot download; restart doesn't lose uploaded state. **Met on 2026-11-07, from the real Pi to the MacBook server over the LAN, with synthetic files.** The camera was not used in this run. The client's retry after a lost connection is covered by automated tests only.

## M3 — Pool selection, deletion, withdrawal
- [x] Clear scope-based pool permission and audit trail
- [x] Make seed import admin-only; validity checks, weighted random fairness
- [x] Separate temporary session images from reusable pool files
- [x] Automatic periodic deletion and background cleanup of images and output, done early (ADR-013)
- [x] Cleanup of temp jobs, done in M4
- [x] Private revocation workflow for future public users: a code for the participant, carried out by the operator (ADR-016)
**Acceptance:** expired/revoked never match; synthetic test images purge; pool-empty with no seed yields clear error. **Met on 2026-11-07: by automated tests, plus pool matching, withdrawal by code and seed fallback from the real Pi with synthetic files.** The camera was not used in this run.

## M4 — AI provider abstraction
- [x] `ImageGenerationProvider` interface (input two portraits, prompt config, frame count, output images, metrics)
- [x] Durable queue, worker, progress/status, timeouts, cancellation and bounded retries; cleanup of temp jobs; withdrawal stops queued and running jobs
- [x] FLUX.2 klein 4B adapter through mflux (ADR-014); speed measured with fictional portraits
- [x] Measure identity retention on an agreed evaluation set, including real webcam photos with documented consent
- [x] Deterministic mock ONLY in developer mode, visibly labelled, and never used as an automatic fallback
**Acceptance:** provider swap does not change API; job survives service restart; real model creates two-person scene without identity swap on an agreed evaluation set. **Met on 2026-11-07:** provider swap (tests), restart mid-job and cancellation (real FLUX.2 from the Pi), and the evaluation set of 5 pairs, scored by the owner at an average of 4.6 out of 5 with no swaps (`docs/TEST_PLAN.md`). Two real faces together remain untested. The prompts have since changed to photo booth micro-stories (ADR-019), which are not yet scored.

## M5 — Photostrip composition and quality
- [x] 3/4 separate frames from image provider. The optional contact-sheet crop is not implemented, because providers deliver separate frames
- [x] Resolution and blank-frame checks; a bad frame is regenerated with a new seed
- [x] Consistent subjects across frames: judged by eye in M4's evaluation set, and passed. **Not checked automatically**, because that would need face comparison, which `docs/ARCHITECTURE.md` rules out
- [x] Printer-agnostic layout with physical dimensions deferred until hardware phase
- [x] grayscale, brightness, contrast, dithering profile outputs
**Acceptance:** 3/4 frames and expected geometry; print-ready PNG; failures don't create corrupt results. **Met on 2026-11-07 by automated tests, plus a real FLUX.2 strip from the Pi in all three profiles.** "Print-ready" means a valid PNG in each profile. It has not been printed, because the printer belongs to a later phase.

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

## Brought forward from phase 2 (ADR-020)
- [x] Keyboard kiosk on the Pi's screen: on-screen consent notice, confirmation, countdown, generation timer, withdrawal code
- [x] Finished strip shown on the screen for 20 s while no printer is connected
- [x] Setup menu on the box: WiFi, server address, pairing with a code, camera, PIN (ADR-021)
- [x] Operator dashboard on the Mac: boxes, queue, strips, versioned consent notice and stories, seeds, pairing, audit (ADR-022)
- [ ] Print instead of showing when a printer is connected (needs the printer's model, connection and paper width)
- [ ] Accessibility (larger text, languages, refusal path reviewed), legal review of the notice

## Social presence and live encounters (ADR-023, ADR-024; spec in `docs/INTERACTION_EXTENSION.md`)
Each milestone stops for the owner's review.
- [x] **S0** Contract: spec in `docs/`, typed events, TTL rules, message catalog (German and English), state fixtures
- [x] **S1** Presence backend: events from the real session flow, per-box projection, `GET /api/v1/presence/snapshot`, box labels, kiosk idle screen
- [x] **P-1** Timed pool consent at the kiosk: a second question, 24 h, its own consent record, pool page in the dashboard
- [x] **S2** Dashboard preview of the presence screen with a SIMULATION banner, server-sent events
- [x] **S3** Simulator for 2-5 boxes, developer mode only
- [x] **L-1** Live matching across boxes: 20 s wait, then pool, then seed; identical strip for both; tested with a second kiosk on the Mac
- [x] **L-2** Interactive waiting screen: searching, matched with {location}, the partner's progress
- [x] **M-VOICE-0** Messaging contract and simulator, live pairs only (ADR-025; no real box can send yet)
- [x] **M-VOICE-1** Microphone and local speech-to-text on the Pi, whisper.cpp base after a benchmark (ADR-026). The kiosk is not connected yet. Met on 2026-11-08: spoken sentences became text on the real microphone with no audio leaving the Pi
- [x] **M-VOICE-2** Real relay between paired boxes, message filtering, kiosk connected (ADR-027, off by default). Met on 2026-11-08 over real HTTP with two boxes and a third that received nothing; typed text stood in for speech
- [ ] **M-VOICE-3** Done: on-screen prompts, countdown and received text in German and English; daily counts on the dashboard; proposed consent wording. **Still open, on site:** two real booths with a microphone together, microphone level and noisy-room testing, an alternative for visitors who cannot speak, review of the consent wording before public use
**S1 acceptance:** a box that is only online is never shown as active, and an ended or expired session loses LIVE at once.

## Browser screen on the box (ADR-028)
Each milestone stops for the owner's review.
- [x] **U0** Design prototype: every screen in HTML at 1920 x 1080, checked in a browser
- [x] **U1** The kiosk describes screens as states; the text kiosk is unchanged
- [x] **U2** Local UI server on 127.0.0.1 and the live page, a whole visit with a test image on the Mac
- [x] **U3** Chromium and Cage on the Pi, service, fallback to the text kiosk (tested), start time and memory measured
- [x] **U4** Polish on the real television: overall size halved, header renamed, browser translation switched off

## Person detection at the box (ADR-029)
Each milestone stops for the owner's review. Off by default.
- [x] **D0** Decision recorded, spec and privacy rules changed, sign drafted
- [x] **D1** Benchmark of person detection models on the Pi next to the running browser: NanoDet chosen (`benchmarks/person_detect/README.md`); one person and one room so far
- [x] **D2** Detector in the client, sharing the camera with the photo, frames in memory only. Tried on the real camera on 2026-11-08: detected, cleared, and the camera was free for a photo
- [x] **D3** `person_nearby` on the server, sent to the other boxes in real time. Measured over HTTP on 2026-11-08: about 0.2 s; not yet on real boxes
- [x] **D4** Start screen wording, detection area and calibration at the box, activity on the dashboard. Switched on at the Pi on 2026-11-08 and reports reached the server; not yet watched on a second real box

## Later phases — NOT in v0.5 MVP
- Phase 2: touchscreen and accessible consent + countdown (keyboard kiosk already exists, ADR-020)
- Phase 3: thermoprinter + idempotent printing
- Phase 4: multi-Pi online presence and real-time matching are brought forward above (ADR-024); both sites getting printed output stays in phase 4
