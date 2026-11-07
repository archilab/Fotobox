# Privacy and consent requirements (engineering draft, not legal advice)

1. Current CLI is a developer tool. It **cannot display or obtain user consent**. Operate it only with synthetic media or participants who received notice and explicitly consented via a documented outside process.
2. Two separate purposes: processing image for current strip; reuse in temporary pool. Recording a photo does **not** itself establish consent, and pool reuse should not be forcibly bundled with strip generation.
3. Later touchscreen must use actively selected, understandable consent UI, accessible notice, refusal path, versioned policy, and a demonstrable revocation mechanism.
4. Provisional pool TTL 24h, editable; current demo uses 1h retention for non-pool portrait. These are engineering defaults, not approved privacy schedules. Output files currently lack automated expiry — fix before public use.
5. Expired/revoked items must be excluded from matching immediately, even if file deletion is pending; wipe original, intermediates, result copies, queue payloads and relevant backups according to policy.
6. No public image gallery, digital user retrieval or remote download; API `result` is an **internal client endpoint** in MVP.
7. Minimize logs; do not collect names, face embeddings, GPS or identity assertions. Consent artifacts must not contain photos unless necessary.
8. Seed images require appropriate rights and permission; label mock/test artifacts.
9. Public exhibition requires controller's GDPR assessment, access control and tested deletion and withdrawal paths.
