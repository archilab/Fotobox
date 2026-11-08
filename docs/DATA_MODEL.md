# Data model V0.5

## Current tables
`sessions(id UUID PK, box_id, status, created_at, finished_at, error, portrait_id, match_portrait_id, output_path, create_key, withdrawal_hash)`: unique on `(box_id, create_key)`; `match_portrait_id` is a pool portrait id or `seed:<seed id>`
`portraits(id UUID PK, session_id, path, pool_eligible, created_at, expires_at, use_count, upload_key, upload_sha256)`: one row per file; pool consent adds a second row with `pool_eligible=1`
`seeds(id UUID PK, path, rights_ref, imported_at, removed_at, use_count)`
`generation_jobs(id UUID PK, session_id UNIQUE, provider, prompt_version, status, run_id, attempts, seed, frames_done, frames_total, next_attempt_at, error, metrics, created_at, started_at, finished_at)`: `metrics` holds seconds and peak memory per frame, never image data; a session has one job, reused if requested again
`audit_events(id PK, occurred_at, event, session_id, portrait_id, device_id, detail)`: opaque ids only, never images, names, codes or paths
`prompt_sets(version PK, content, created_at, active)` and `consent_notices(version PK, text, created_at, active)`: every saved version, one active each (ADR-022); `generation_jobs.prompt_version` names the set a job used
`device_status(device_id PK, state, detail, reported_at)`: the latest report from each box
`dashboard_password(id=1, salt, hash, iterations, set_at)` and `device_pairings(id, device_id, code_hash, created_at, expires_at, used_at, wrong_guesses)`
`devices(id PK, token_hash UNIQUE, created_at, revoked_at, public_label)`: `sessions.box_id` holds the authenticated device id; `public_label` is the name other boxes show (ADR-023)
`presence_events(event_id PK, type, box_id, session_id, correlation_id, occurred_at, simulated)`: social presence events (ADR-023), opaque ids and times only, deleted after an hour; a repeated `event_id` is ignored
`consent_records(id PK, session_id, purpose, policy_version, granted_at, withdrawn_at, provenance, evidence_ref)`: one record per session and purpose (`processing`, `pool`)

Consent provenance is `operator_attestation`, or `migrated_flag` for records converted from the old flags, whose `granted_at` is the session's creation time because the real time was never stored. A record is an operator's statement, not proof of consent. `upload_sha256` identifies repeated uploads, is not a face template, and is deleted with the portrait.

The schema is versioned with `PRAGMA user_version`. `photobox_server/schema.py` applies pending migrations in one transaction on connection, and a database from a newer version is refused.

## Planned normalized schema
- `photoboxes(id, name, location_code, last_seen, capabilities)`
- `matches(id, portrait_a_id, portrait_b_id, type, created_at)`
- `print_jobs` not until phase 3

All timestamps UTC, IDs opaque, no person-name or biometric template fields.
