# Data model V0.5

## Current tables
`sessions(id UUID PK, box_id, status, consent_processing, consent_pool, created_at, finished_at, error, portrait_id, match_portrait_id, output_path)`
`portraits(id UUID PK, session_id, path, pool_eligible, created_at, expires_at, use_count)`

## Planned normalized schema
- `photoboxes(id, name, location_code, last_seen, capabilities)`
- `sessions(id, box_id, state, started_at, finished_at, consent_record_id)`
- `consent_records(id, session_id, purpose, policy_version, granted_at, withdrawal_at, provenance)`
- `portraits(id, session_id, storage_key, pool_eligible, expires_at, revoked_at, use_count)`
- `matches(id, portrait_a_id, portrait_b_id, type, created_at)`
- `generation_jobs(id, match_id, provider, prompt_version, status, retries, result_key)`
- `print_jobs` not until phase 3

All timestamps UTC, IDs opaque, no person-name or biometric template fields.
