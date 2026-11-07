# Connected Photobox — Development Starter Kit V0.5

**Phase 1 only:** 1 Raspberry Pi + standard UVC USB webcam; Mac Studio hosts API and (later) image-generation worker. **No touchscreen and no printer** in MVP. CLI-operated tests. Final deliverable: vertical PNG photo strip of 3–4 AI-generated frames combining 2 portraits. Multi-box, consent touchscreen and thermal printing are future phases.

## Current implementation status
The included code is a **runnable vertical slice**, not a finished AI system: FastAPI + SQLite session tracking, temporary photo storage, basic seed/visitor matching, synthetic **MOCK** strip rendering and a Raspberry Pi CLI with optional webcam capture. **No real AI model is installed or wired**. The mock is visibly labelled and uses source portraits for development only. Do not mistake it for the final art output.

## Quick start (macOS / Linux, Python 3.11+)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
python -m uvicorn photobox_server.app:app --host 127.0.0.1 --port 8000
```
In another terminal, register a device. Its token is printed once; only a hash is stored. All endpoints except `health` need it.
```bash
source .venv/bin/activate
python -m photobox_server.admin device add pi-01
export PHOTOBOX_DEVICE_TOKEN=PASTE_TOKEN_HERE
python -m photobox_client.cli --server http://127.0.0.1:8000 health
python -m photobox_client.cli --server http://127.0.0.1:8000 capture --file path/to/consented-test-portrait.jpg --attest-documented-consent --pool-consent
python -m photobox_client.cli --server http://127.0.0.1:8000 status SESSION_ID
python -m photobox_client.cli --server http://127.0.0.1:8000 result SESSION_ID --output result.png
pytest -q
```
If no seed portrait exists the capture will fail with a useful error. **Before first run**, add an explicitly licensed/consented portrait to `storage/seed_portraits/` (JPG/PNG), and restart the server. Alternately use `scripts/create_synthetic_seed.py` to make an *illustrative non-person* test image. `capture` refuses to run without `--attest-documented-consent`, which the operator may pass only for synthetic images or when a documented consent procedure took place outside this MVP interface. That flag and `--pool-consent` are developer-only assertions, **not proof of informed consent**.

The server reads `.env` from the folder it starts in; variables already set in the shell take precedence. See `.env.example` for the available settings. An unimplemented `PHOTOBOX_PROVIDER` stops the server at startup.

## Raspberry Pi
Setup and network steps are in `docs/HARDWARE.md`. On the Pi, install with `pip install -e '.[client-camera]'`, store the device token in a file, then:
```bash
python -m photobox_client.cli camera-list
python -m photobox_client.cli camera-probe --camera CAMERA_NAME
python -m photobox_client.cli camera-test --camera CAMERA_NAME --count 20
python -m photobox_client.cli --server http://SERVER_LAN_IP:8000 --token-file ~/.photobox.token capture --camera CAMERA_NAME --rotate 0 --attest-documented-consent
```
The server binds to localhost by default. To reach it from the Pi, bind to the Mac's LAN address on a trusted network only (see ADR-010): traffic is **not encrypted** until NetBird or Tailscale is added, and **the API must not be exposed to public networks**. Add `--pool-consent` only after obtaining separate, documented pool consent.

## Important privacy / security boundary
Current demo has per-device tokens, but **no TLS, no rate limiting, no production consent capture, no withdrawal flow, no real durable queue, and no production-hardening**. Do not use with exhibition visitors or publicly accessible network endpoints. Test only with synthetic images or explicit, documented, purpose-specific consent. Do not store identifiable portraits in Git. See `docs/PRIVACY.md` and `docs/SECURITY.md`.

## Work with Claude Code
From repo root run `claude`. The root `CLAUDE.md` project instructions are included. Start with `docs/MILESTONES.md` and ask Claude to implement M1 then check tests. Use `docs/CLAUDE_PROMPTS.md` for prompts. Don't run `/init`: a curated `CLAUDE.md` already exists.

## Architecture
- `src/photobox_client`: CLI, webcam capture, HTTP client.
- `src/photobox_server`: FastAPI, SQLite, files, matching, mock worker, output.
- `docs`: contracts, architecture decisions, privacy and milestone acceptance tests.
- `tests`: API and image renderer checks.

This ZIP is a scaffold, not a claim that production features are complete.
