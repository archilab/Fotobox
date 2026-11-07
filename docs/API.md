# API contract v0.5 (demo)

All endpoints start `/api/v1`. JSON responses. No authentication in demo — **never expose publicly**.

| Method | Route | Request / Result |
|---|---|---|
| GET | `/health` | `{status,provider,production_ready}` |
| POST | `/sessions` | `{box_id}` -> `{id,status}` |
| POST | `/sessions/{id}/consent` | `{processing:true,pool:false,documented_externally:true}` (test-only attestation) |
| POST | `/sessions/{id}/portrait` | multipart `image` JPEG/PNG/WebP -> `{portrait_id,status}` |
| POST | `/sessions/{id}/process` | no body -> `{status,mode,result_url}`; synchronous mock |
| GET | `/sessions/{id}` | `{id,box_id,status,error,has_result}` |
| GET | `/sessions/{id}/result` | image/png (demo only) |
| POST | `/admin/cleanup` | development-only manual cleanup endpoint |

## Future design (not implemented)
- `POST /sessions/{id}/process`: idempotent enqueue, return 202 + job ID
- `GET /jobs/{id}`, authenticated WebSocket box updates, per-device credentials
- `DELETE /consents/{withdrawal_token}` / revocation and deletion
- durable job worker with retry, image/object access control, lifecycle management
- multi-box live matching, printer protocol, touchscreen flow

## Example requests
```bash
curl http://127.0.0.1:8000/api/v1/health
curl -X POST http://127.0.0.1:8000/api/v1/sessions -H 'Content-Type: application/json' -d '{"box_id":"pi-01"}'
```
For convenience use `python -m photobox_client.cli` rather than handcrafting upload calls.
