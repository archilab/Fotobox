# API contract v0.5 (demo)

All endpoints start `/api/v1`. JSON responses. **Never expose publicly.**

## Authentication
Every path except `/api/v1/health` and `/api/v1/pair` requires `Authorization: Bearer <device token>` and returns `401` otherwise. Tokens are created on the server with `python -m photobox_server.admin device add <id>`. A session belongs to the device that created it; other devices get `404`, as for an unknown session. Traffic is not encrypted on a plain LAN (ADR-010).

| Method | Route | Request / Result |
|---|---|---|
| GET | `/health` | `{status,provider,production_ready}` (no token needed) |
| POST | `/devices/status` | `{state,detail?}` -> `204`; `state` is `idle`, `consent`, `capturing`, `generating`, `showing`, `setup` or `not ready`; `detail` at most 120 characters, operator-facing only (ADR-022) |
| GET | `/presence/snapshot` | What is happening at the other boxes and the message to show (ADR-023): `{type,generated_at,detail,self:{box_id,label,state},locations:[{box_id,label,device_state,activity_state,activity_age_seconds,match_available,simulated,status:{de,en}}],more_locations,earlier_portrait_available,message:{priority,key,texts:{de:{hero,support},en:{hero,support}}}}`. `device_state` is `online` or `offline`; `activity_state` is `session_active`, `person_nearby` (a camera seems to see someone, ADR-029), `recent_photo` or `none`; `activity_age_seconds` is `null` unless `PHOTOBOX_PUBLIC_ACTIVITY_DETAIL=detailed`; `match_available` is `true` while a visitor there is waiting for a live partner (ADR-024). At most 5 locations, ordered live, recent, online, offline. Never contains images, paths, addresses or session ids |
| GET | `/messaging/events` | `{events:[...]}`, each event handed over once: `messaging.window_opened {match_id,expires_at,partners}`, `messaging.window_closed {match_id,reason}`, `messaging.message_received {message_id,match_id,sender_label,text,expires_at}`. `404` while messaging is off (ADR-027) |
| POST | `/sessions/{id}/messages` | `{message_id,match_id,text}` -> `messaging.message_ack {message_id,status}` or `messaging.message_rejected {message_id,reason}`, where `reason` is `not_in_match`, `window_closed`, `too_long`, `empty`, `blocked`, `limit_reached`, `cooldown` or `invalid`. Accepted is not the same as displayed or read. The server resolves the recipient; `404` while messaging is off |
| POST | `/devices/person` | `{present}` -> `204`: whether this box's camera seems to see someone (ADR-029). Only yes or no; it creates no session and no consent |
| GET | `/presence/stream` | Server-sent events: `event: snapshot` with the same content as `/presence/snapshot`, each time it changes. The server ends a stream after 55 s, and the box reconnects |
| POST | `/pair` | `{code}` -> `{device_id,token}`; **no token needed**; `403` for a wrong, used or expired code, `429` after 10 attempts per minute (ADR-021) |
| GET | `/consent-notice` | `{version,text,live_matching,pool}`; `live_matching` says the notice covers live pairing, and the box then sends `live:true` with the consent (ADR-024). for the box's screen; `503` if no completed notice is configured (ADR-020). `pool` is `null` when pool use is not offered, or `{version,text,until,until_text}`: the separate pool question with its end time filled in (ADR-024) |
| POST | `/sessions` | no body -> `{id,status}`; `box_id` is the authenticated device; `201`, or `200` when repeated with the same key |
| POST | `/sessions/{id}/consent` | `{processing:true,pool:false,documented_externally:true,evidence_ref?}` (test-only attestation); `evidence_ref` is at most 64 characters of letters, digits and `._:-`, and must not contain personal data. Returns `{status,pool_consent,withdrawal_code}`; the code appears once and is `null` on a repeat. With `method: "participant_screen"` and the shown `notice_version`, the participant's on-screen confirmation replaces the operator attestation, and an outdated version returns `409`. `live:true` records a separate `live` consent and returns `403` while live matching is off. Pool use on the screen needs `pool:true`, the shown `pool_notice_version` and `pool_until` exactly as served: `403` if no pool question is offered, `409` for an outdated pool notice, `422` for an end time that is missing, without time zone, past, or beyond `PHOTOBOX_POOL_HOURS`. The pool copy then expires at that time at the latest |
| POST | `/sessions/{id}/portrait` | multipart `image` -> `{portrait_id,status}`; limits below |
| POST | `/sessions/{id}/process` | no body -> `202 {status,job_id,mode,status_url,result_url}` queues generation, for every provider; with live matching it first pairs with a visitor waiting at another box, or waits up to 20 s for one, up to 60 s while a visitor at another box has already agreed (ADR-024); can be requested again after `failed`; `200` if already complete |
| POST | `/sessions/{id}/cancel` | no body -> `{status,cancelled}`; stops waiting or running generation and keeps the portrait |
| GET | `/sessions/{id}` | `{id,box_id,status,error,has_result,job,match}`; `job` is `null` or `{status,frames_done,frames_total,attempts,provider,waiting_for_partner,wait_seconds_left,partner_coming,partner_coming_label}`, where `wait_seconds_left` is the server's real deadline and `partner_coming` means a visitor at another box has already agreed; `match` is `{kind,partner_label}` with `kind` `live`, `pool`, `seed` or `null`. A live follower reports its leader's job. Poll this for progress |
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
- pushed presence updates to boxes, short-lived scoped credentials (the dashboard preview already receives server-sent events; the presence simulator is in the dashboard, not in this API, ADR-023)
- self-service withdrawal
- image/object access control
- multi-box live matching, printer protocol, touchscreen flow

## Example requests
```bash
curl http://127.0.0.1:8000/api/v1/health
curl -X POST http://127.0.0.1:8000/api/v1/sessions -H "Authorization: Bearer $PHOTOBOX_DEVICE_TOKEN"
```
For convenience use `python -m photobox_client.cli` rather than handcrafting upload calls.
