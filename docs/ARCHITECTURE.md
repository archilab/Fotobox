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

## Known omissions
No TLS, rate limiting, remote safe deployment, robust periodic cleanup, withdrawal mechanism, concurrent job reservation, queue, production quality controls, realtime events, actual AI, output retention policy enforcement. Work through milestones before public use.
