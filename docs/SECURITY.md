# Security boundary and hardening backlog

**Demo is not production ready.** Every endpoint except `health` requires a per-device token, and sessions are only visible to the device that created them. Cleanup and device management are local commands with no HTTP endpoint. Never expose port 8000 publicly.

**Plain LAN risk (ADR-010):** until NetBird or Tailscale is added, portraits, strips and tokens cross the LAN unencrypted, so anyone on that network may be able to read them or reuse a captured token. Use only synthetic or expressly authorized images on a trusted network. Bind to the Mac's LAN address rather than `0.0.0.0`. If a token may have been exposed, revoke it with `python -m photobox_server.admin device revoke <id>`.

## ADR-010 review (2026-11-07)

Scope: the server, device auth and client token handling at commit `dc396a8`, checked by reading the code and probing it with synthetic images. The Pi's operating system, the network itself and a penetration test were out of scope.

**Result:** the setup is acceptable for development on a trusted LAN. Findings 1–4 are now fixed (ADR-013; the table describes them as found), so expressly authorized photos of real people are within ADR-010. Finding 5 stays open as the accepted risk.

**Limits of deletion:** the server deletes files normally rather than wiping them, and it cannot reach copies outside its storage folder. Exclude that folder from Time Machine and other backups on the server machine before storing real portraits. A deleted strip may also remain wherever a client saved it.

No way around authentication was found: missing, wrong and non-bearer tokens, `/docs`, `/openapi.json`, `/redoc` and variants of the health path all return 401. Another device's session returns 404. Corrupt, empty and oversized uploads return 422. Uploads are re-encoded without EXIF. The token does not appear in the server log or database.

| # | Finding | Measured | Where |
|---|---|---|---|
| 1 | Strips outlive retention. `cleanup` leaves them on disk and the result stays downloadable, although each contains the portraits. Nothing runs `cleanup` automatically, so expired portraits also stay on disk until it is run by hand. | After `cleanup`, the strip remained and the result returned 200 | `core.py` `cleanup_expired` |
| 2 | A failed upload can leave an untracked portrait. The file is saved before the retention setting is checked, and `cleanup` cannot find a file with no database record. | Invalid `PHOTOBOX_POOL_HOURS`: 500, one extra file, no record | `app.py` `upload_portrait` |
| 3 | Other accounts on the Mac can read the stored data. | Folders 755, database and portraits 644 | `core.py` `storage_root`, `save_portrait` |
| 4 | The pixel limit is not enforced as written. Pillow only warns up to twice the limit, so images up to 40 megapixels pass, not 20. | 30MP accepted, 49MP rejected | `core.py` `validate_image` |
| 5 | Portraits and tokens cross the LAN unencrypted. This is the accepted ADR-010 risk. | Plain HTTP | Replace with NetBird or Tailscale |

## Before any deployment beyond the trusted test LAN

- encryption through NetBird, Tailscale or TLS; scoped short-lived credentials
- a size limit on uploads sent without `Content-Length`: the server only refuses those after the framework has spooled them to disk, so a device with a valid token could fill the disk
- rate limiting beyond uploads, request concurrency limits, timeouts and quotas (per-device upload limit present, in memory)
- secure OS file permissions and encryption-at-rest evaluation
- retention of original, generated, preview, thumbnail, logs, backups; verified lifecycle deletion
- a self-service withdrawal path if participants should not depend on the operator (a code and prompt stop of revoked portraits are present); a retention limit for the audit trail
- server-push only via authenticated channels
- never log images, EXIF, external IPs unless clearly needed and documented
- random file identifiers and storage isolation (present, review as implementation evolves)

Risk: demo consent endpoint has developer-supplied boolean `documented_externally`, set by the CLI flag `--attest-documented-consent`, which is NOT proof of consent. Do not claim this protects real participants.
