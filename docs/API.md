# API contract v0.5 (demo)

All endpoints start `/api/v1`. JSON responses. **Never expose publicly.**

## Authentication
Every path except `/api/v1/health` requires `Authorization: Bearer <device token>` and returns `401` otherwise. Tokens are created on the server with `python -m photobox_server.admin device add <id>`. A session belongs to the device that created it; other devices get `404`, as for an unknown session. Traffic is not encrypted on a plain LAN (ADR-010).

| Method | Route | Request / Result |
|---|---|---|
| GET | `/health` | `{status,provider,production_ready}` (no token needed) |
| POST | `/sessions` | no body -> `{id,status}`; `box_id` is the authenticated device |
| POST | `/sessions/{id}/consent` | `{processing:true,pool:false,documented_externally:true}` (test-only attestation) |
| POST | `/sessions/{id}/portrait` | multipart `image` JPEG/PNG/WebP, up to 20 megapixels -> `{portrait_id,status}` |
| POST | `/sessions/{id}/process` | no body -> `{status,mode,result_url}`; synchronous mock |
| GET | `/sessions/{id}` | `{id,box_id,status,error,has_result}` |
| GET | `/sessions/{id}/result` | image/png (demo only); `404` once a portrait in the strip has expired |

Device management and on-demand cleanup are local commands, not HTTP endpoints: `python -m photobox_server.admin --help`. The server also runs cleanup automatically.

## Future design (not implemented)
- `POST /sessions/{id}/process`: idempotent enqueue, return 202 + job ID
- `GET /jobs/{id}`, authenticated WebSocket box updates, short-lived scoped credentials
- `DELETE /consents/{withdrawal_token}` / revocation and deletion
- durable job worker with retry, image/object access control, lifecycle management
- multi-box live matching, printer protocol, touchscreen flow

## Example requests
```bash
curl http://127.0.0.1:8000/api/v1/health
curl -X POST http://127.0.0.1:8000/api/v1/sessions -H "Authorization: Bearer $PHOTOBOX_DEVICE_TOKEN"
```
For convenience use `python -m photobox_client.cli` rather than handcrafting upload calls.
