# Data model V0.5

## Current tables
`sessions(id UUID PK, box_id, status, created_at, finished_at, error, portrait_id, match_portrait_id, output_path, create_key)`: unique on `(box_id, create_key)`
`portraits(id UUID PK, session_id, path, pool_eligible, created_at, expires_at, use_count, upload_key, upload_sha256)`
`devices(id PK, token_hash UNIQUE, created_at, revoked_at)`: `sessions.box_id` holds the authenticated device id
`consent_records(id PK, session_id, purpose, policy_version, granted_at, withdrawn_at, provenance, evidence_ref)`: one record per session and purpose (`processing`, `pool`)

Consent provenance is `operator_attestation`, or `migrated_flag` for records converted from the old flags, whose `granted_at` is the session's creation time because the real time was never stored. A record is an operator's statement, not proof of consent. `upload_sha256` identifies repeated uploads, is not a face template, and is deleted with the portrait.

The schema is versioned with `PRAGMA user_version`. `photobox_server/schema.py` applies pending migrations in one transaction on connection, and a database from a newer version is refused.

## Planned normalized schema
- `photoboxes(id, name, location_code, last_seen, capabilities)`
- `matches(id, portrait_a_id, portrait_b_id, type, created_at)`
- `generation_jobs(id, match_id, provider, prompt_version, status, retries, result_key)`
- `print_jobs` not until phase 3

All timestamps UTC, IDs opaque, no person-name or biometric template fields.
