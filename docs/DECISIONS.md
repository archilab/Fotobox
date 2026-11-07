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
