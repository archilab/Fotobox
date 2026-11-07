# Security boundary and hardening backlog

**Demo is not production ready.** Every endpoint except `health` requires a per-device token, and sessions are only visible to the device that created them. Cleanup and device management are local commands with no HTTP endpoint. Never expose port 8000 publicly.

**Plain LAN risk (ADR-010):** until NetBird or Tailscale is added, portraits, strips and tokens cross the LAN unencrypted, so anyone on that network may be able to read them or reuse a captured token. Use only synthetic or expressly authorized images on a trusted network. Bind to the Mac's LAN address rather than `0.0.0.0`. If a token may have been exposed, revoke it with `python -m photobox_server.admin device revoke <id>`.

Before any deployment beyond the trusted test LAN:
- encryption through NetBird, Tailscale or TLS; scoped short-lived credentials
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
