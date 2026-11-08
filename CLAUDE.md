# Connected Photobox — Claude Code project instructions

**Scope:** Work on V0.5 phase 1 only: ONE Raspberry Pi + generic UVC USB webcam, CLI capture, upload to Mac Studio API, temporary portrait pool, matching, *eventually* local AI 3–4 scene generation, vertical PNG artifact. NO touchscreen, NO printer, NO digital user retrieval feature. Social presence and live matching across boxes follow `docs/INTERACTION_EXTENSION.md` as adapted by ADR-023/ADR-024; until a second Pi exists, a second box is a kiosk on the Mac or the simulator. Camera-based person detection before consent is allowed only under the limits in ADR-029.

1. Read `README.md`, `docs/ARCHITECTURE.md`, `docs/MILESTONES.md`, `docs/API.md`, `docs/PRIVACY.md`, `docs/SECURITY.md` before major changes.
2. Implement milestones in order. Avoid speculative features or unapproved dependencies. Maintain small testable increments; run `pytest -q` after each.
3. Preserve boundaries: client hardware vs API vs matching vs AI provider vs strip renderer; never embed a specific AI model into FastAPI route handlers.
4. Deliver complete working code, migrations/config/docs/tests with every feature. Do not leave silent placeholders. Mark nonimplemented features explicitly and do not claim they work.
5. Treat portraits as sensitive personal data. Never commit image data, credentials or `.env`; minimize metadata; do not log image bytes or identifying content. Pool use requires its own recorded authorization, not merely taking a photo; at the kiosk it is given with the same key press as the photo, on a screen that says so (ADR-030).
6. Test scenarios must use synthetic images or expressly authorized test images. Never bypass permission gates. Seed images must be rights-cleared.
7. Current `MOCK` image generator is not AI; replace via provider adapter only after benchmark. Output must carry a mock watermark until replaced.
8. Local demo endpoints are NOT safe for public exposure. Do not enable 0.0.0.0 remote/public access without authentication/TLS and review.
9. Environment: Python 3.11+, FastAPI, SQLite for metadata, local files for images, OpenCV on Pi; prefer simple APIs, type hints, pytest, httpx.
10. Record architectural changes in `docs/DECISIONS.md`, update API/tests/docs. Progress one M milestone at a time. Ask for hardware-specific printer specs only when later phases start.

Commands: `pip install -e '.[dev]'`, `pytest -q`, `python -m uvicorn photobox_server.app:app`, `python -m photobox_client.cli --help`.
