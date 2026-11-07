# Security boundary and hardening backlog

**Demo is not production ready.** All endpoints (including result downloads and cleanup) are unauthenticated. Bind to loopback for development and tunnel only through controlled authenticated private infrastructure. Never expose port 8000 publicly.

Before Pi-to-Mac deployment:
- device authentication and scoped short-lived credentials, TLS / private network, authenticated admin endpoints
- image upload size limit and decoding protection (present), MIME sniffing, pixel limit and content validation
- app-level idempotency; ensure retries cannot duplicate processing or corrupt records
- rate limiting, request concurrency limits, timeouts and per-device quotas
- secure OS file permissions and encryption-at-rest evaluation
- retention of original, generated, preview, thumbnail, logs, backups; verified lifecycle deletion
- revocation token and prompt stop of using revoked portraits
- server-push only via authenticated channels
- never log images, EXIF, external IPs unless clearly needed and documented
- random file identifiers and storage isolation (present, review as implementation evolves)

Risk: demo consent endpoint has developer-supplied boolean `documented_externally`, set by the CLI flag `--attest-documented-consent`, which is NOT proof of consent. Do not claim this protects real participants.
