# Architektur V0.5

## Deployment
- Pi OS 64-bit: USB UVC camera + CLI (OpenCV uses V4L2); no display dependency.
- Mac Studio M3 Ultra (target): FastAPI API, SQLite, local file storage, later AI provider worker. Development currently runs on a MacBook Pro M3 (see ADR-008).
- HTTP polling is sufficient for MVP. Social presence (ADR-023) is polled too; pushed updates come with milestone S2. Live matching across boxes is planned (ADR-024).
- Connection in controlled local test network with per-device tokens (ADR-010); secure remote deployment needs encryption through NetBird, Tailscale or TLS, plus hardening.

## Boundary diagram
`USB webcam -> Pi CLI -> HTTP POST -> API -> SQLite/files -> job queue -> worker -> provider (frames) -> strip renderer -> PNG GET -> Pi`. Matching happens when a job starts and is re-checked when it ends.

## Social presence
`photobox_server/presence/` is separate from matching and generation. The API, the worker and withdrawal record typed events (`presence/events.py`) in the same transaction as their action; `presence/projection.py` turns events and each box's latest status report into states without touching the database or clock; `presence/messages.py` holds the message catalog and its priority rules; `presence.snapshot()` puts them together for `GET /api/v1/presence/snapshot`. The kiosk shows the message on its start screen and refreshes it every 10 s.

## Feature flags
`PHOTOBOX_PROVIDER` selects the provider: `mflux-flux2` (FLUX.2 klein 4B, ADR-014) or `mock`, which is not AI, needs `PHOTOBOX_DEVELOPER_MODE=true`, and labels every frame. Any other value stops the server at startup, and so does a missing mflux. Providers live in `photobox_server/providers/`; route handlers never name a model. Strip footers state the origin. Prompts and the 100 micro-stories live in `photobox_server/prompts.py` and `stories.py` (ADR-019). Composition and output profiles live in `photobox_server/strip.py`, which knows nothing about how frames were made (ADR-018).

## Design invariants
- Session ID generated server-side; never derive file paths from user input.
- Portrait files separate from metadata; only explicit pool authorization makes them eligible, and matching re-checks it at the moment of use.
- Session images (`portraits`) and reusable pool copies (`pool_portraits`) are separate files with separate lifetimes (ADR-016).
- Never use an image from the same session as matched counterpart.
- Anonymous temporary portrait pool; avoid face embeddings or identification features.
- Rendered artifact PNG and single request; print only in a later phase.
- Persistence: SQLite metadata and a durable job queue (ADR-017).
- Seed images must be authorized and curated; they enter only through `admin seed add`.

## Session states
| From | To | Trigger |
|---|---|---|
| `awaiting_consent` | `awaiting_portrait` | `POST /consent` with attestation |
| `awaiting_portrait` | `uploaded` | `POST /portrait` |
| `uploaded`, `failed` | `queued` | `POST /process` |
| `queued` | `processing` | worker starts the job |
| `processing` | `complete` | strip saved |
| `processing` | `queued` | retry, or the server restarted during processing |
| `processing` | `failed` | attempts used up, or no image available |
| `queued`, `processing` | `uploaded` | `POST /cancel` |
| any | `withdrawn` | `admin withdraw`; final |

No other move is possible. The table lives in `photobox_server/sessions.py`.

## Concurrent access
- Run **one server process**: restart recovery, the worker and the upload limit all assume it.
- A state change is a conditional `UPDATE ... WHERE status=<expected>`. Of several concurrent requests exactly one succeeds, and the others get `409` or, if it is the same request repeated, its earlier answer.
- That `UPDATE` also takes SQLite's write lock, so a portrait's state, record and file commit together. Other writers wait up to 15 s. Reads continue under WAL.
- Images appear under their final name only when complete. If the server dies between writing a file and committing its record, cleanup later removes the file as untracked.

## Known omissions
No TLS, general rate limiting, remote safe deployment, self-service withdrawal, production quality controls, realtime events, an approved retention schedule. Work through milestones before public use.
