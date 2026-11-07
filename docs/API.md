# API contract v0.5 (demo)

All endpoints start `/api/v1`. JSON responses. **Never expose publicly.**

## Authentication
Every path except `/api/v1/health` and `/api/v1/pair` requires `Authorization: Bearer <device token>` and returns `401` otherwise. Tokens are created on the server with `python -m photobox_server.admin device add <id>`. A session belongs to the device that created it; other devices get `404`, as for an unknown session. Traffic is not encrypted on a plain LAN (ADR-010).

| Method | Route | Request / Result |
|---|---|---|
| GET | `/health` | `{status,provider,production_ready}` (no token needed) |
| POST | `/pair` | `{code}` -> `{device_id,token}`; **no token needed**; `403` for a wrong, used or expired code, `429` after 10 attempts per minute (ADR-021) |
| GET | `/consent-notice` | `{version,text}` for the box's screen; `503` if no completed notice is configured (ADR-020) |
| POST | `/sessions` | no body -> `{id,status}`; `box_id` is the authenticated device; `201`, or `200` when repeated with the same key |
| POST | `/sessions/{id}/consent` | `{processing:true,pool:false,documented_externally:true,evidence_ref?}` (test-only attestation); `evidence_ref` is at most 64 characters of letters, digits and `._:-`, and must not contain personal data. Returns `{status,pool_consent,withdrawal_code}`; the code appears once and is `null` on a repeat. With `method: "participant_screen"` and the shown `notice_version`, the participant's on-screen confirmation replaces the operator attestation; pool use is refused, and an outdated version returns `409` |
| POST | `/sessions/{id}/portrait` | multipart `image` -> `{portrait_id,status}`; limits below |
| POST | `/sessions/{id}/process` | no body -> `202 {status,job_id,mode,status_url,result_url}` queues generation, for every provider; can be requested again after `failed`; `200` if already complete |
| POST | `/sessions/{id}/cancel` | no body -> `{status,cancelled}`; stops waiting or running generation and keeps the portrait |
| GET | `/sessions/{id}` | `{id,box_id,status,error,has_result,job}`; `job` is `null` or `{status,frames_done,frames_total,attempts,provider}`. Poll this for progress |
| GET | `/sessions/{id}/result?profile=` | image/png (demo only); `profile` is `color` (default, RGB), `grayscale` (8-bit) or `thermal` (1-bit dithered), and other values return `422`; `404` once an image in the strip has expired, been withdrawn or been removed |

Device and seed management, withdrawal, the audit trail and on-demand cleanup are local commands, not HTTP endpoints: `python -m photobox_server.admin --help`. The server also runs cleanup automatically.

## Repeating requests
Requests can be repeated safely after a lost connection. Each one does its work only once:
- `POST /sessions` and `POST /portrait` accept an `Idempotency-Key` header of 1-128 letters, digits, `-` or `_`; an invalid one returns `400`. Session keys are scoped to the device, upload keys to the session.
- A repeated upload with the same key and image returns the original answer. The same key with a different image returns `409`, as does any second upload without the original key. Uploads count toward the limit even when repeated.
- A repeated `consent` with identical values succeeds. A repeated `process` returns the same job while it is queued or running, and success once it is complete. Anything that would change consent returns `409`.

## Upload limits
| Limit | Default | Otherwise |
|---|---|---|
| Size, `PHOTOBOX_MAX_UPLOAD_BYTES` | 10 MB | `413` |
| Format: JPEG, PNG or WebP, judged by content; not animated | | `422` |
| Pixels | 20 megapixels | `422` |
| Each side | 64-8000 pixels | `422` |
| Uploads per device, `PHOTOBOX_MAX_UPLOADS_PER_MINUTE` | 30 | `429` with `Retry-After` |

## Future design (not implemented)
- authenticated WebSocket box updates, short-lived scoped credentials
- self-service withdrawal
- image/object access control
- multi-box live matching, printer protocol, touchscreen flow

## Example requests
```bash
curl http://127.0.0.1:8000/api/v1/health
curl -X POST http://127.0.0.1:8000/api/v1/sessions -H "Authorization: Bearer $PHOTOBOX_DEVICE_TOKEN"
```
For convenience use `python -m photobox_client.cli` rather than handcrafting upload calls.
