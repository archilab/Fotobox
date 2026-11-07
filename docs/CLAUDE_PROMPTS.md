# Ready-to-use Claude Code prompts

## Begin
> Read `CLAUDE.md` and all files in `docs/`. Inspect the repository without modifying it. Run the existing tests. Explain which aspects are currently real, mock, or missing. Propose the smallest patch required to meet milestone M1. Do not add touchscreen, printer, or a second Pi.

## M1
> Implement milestone M1 in `docs/MILESTONES.md` with complete, runnable code and tests. Support generic USB UVC webcams on Pi. Keep server localhost-only unless you implement authentication and document secure networking. Run tests and summarize test evidence.

## M2
> Implement milestone M2 strictly. Include state machine, restart recovery, authenticated API, upload idempotency, migration and privacy tests. Produce complete files, not pseudocode. Run tests.

## M4 benchmark
> Before selecting an AI provider, research the local Mac Studio runtime and verify candidate model availability/license. Set up reproducible benchmark script with synthetic or explicitly consented portraits. Compare identity retention, images quality, VRAM/unified memory use and timing; record empirical measurements. Do not fabricate benchmarks.

## Review
> Review privacy/security risks in changed code against `docs/PRIVACY.md` and `docs/SECURITY.md`. List severity, file/line, reproduction and fix. Do not claim compliance automatically.
