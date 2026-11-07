# Architektur V0.5

## Deployment
- Pi OS 64-bit: USB UVC camera + CLI (OpenCV uses V4L2); no display dependency.
- Mac Studio M3 Ultra (target): FastAPI API, SQLite, local file storage, later AI provider worker. Development currently runs on a MacBook Pro M3 (see ADR-008).
- HTTP polling is sufficient for MVP. WebSockets and multi-Pi pairing are reserved for later.
- Connection in controlled local test network with per-device tokens (ADR-010); secure remote deployment needs encryption through NetBird, Tailscale or TLS, plus hardening.

## Boundary diagram
`USB webcam -> Pi CLI -> HTTP POST -> API -> SQLite/files -> match pool/seed -> MOCK renderer -> PNG GET -> Pi`.

## Feature flags
`PHOTOBOX_PROVIDER=mock` in MVP; 'comfyui'/'mlx' are **not implemented** and any value other than `mock` stops the server at startup. New providers are added with their adapter in milestone M4. Rendering is mock composition, not AI. Do not label art outputs as generative before connecting real provider.

## Design invariants
- Session ID generated server-side; never derive file paths from user input.
- Portrait files separate from metadata; only explicit pool authorization makes them eligible.
- Never use own portrait as matched counterpart.
- Anonymous temporary portrait pool; avoid face embeddings or identification features.
- Rendered artifact PNG and single request; print only in a later phase.
- Persistence: SQLite metadata; job queue is synchronous demo only; plan durable queue for real AI.
- Seed images must be authorized and curated.

## Session states
| From | To | Trigger |
|---|---|---|
| `awaiting_consent` | `awaiting_portrait` | `POST /consent` with attestation |
| `awaiting_portrait` | `uploaded` | `POST /portrait` |
| `uploaded` | `processing` | `POST /process` |
| `processing` | `complete` | strip rendered |
| `processing` | `failed` | render failed, or the server restarted during processing |
| `failed` | `processing` | `POST /process` again |

No other move is possible. The table lives in `photobox_server/sessions.py`.

## Concurrent access
- Run **one server process**: restart recovery and the upload limit both assume it.
- A state change is a conditional `UPDATE ... WHERE status=<expected>`. Of several concurrent requests exactly one succeeds, and the others get `409` or, if it is the same request repeated, its earlier answer.
- That `UPDATE` also takes SQLite's write lock, so a portrait's state, record and file commit together. Other writers wait up to 15 s. Reads continue under WAL.
- Images appear under their final name only when complete. If the server dies between writing a file and committing its record, cleanup later removes the file as untracked.

## Known omissions
No TLS, general rate limiting, remote safe deployment, withdrawal mechanism, concurrent job reservation, queue, production quality controls, realtime events, actual AI, an approved retention schedule. Work through milestones before public use.
