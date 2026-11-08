"""Database schema as ordered migrations. PRAGMA user_version records how many have run."""
from __future__ import annotations
import sqlite3

MIGRATIONS: tuple[tuple[str, ...], ...] = (
    # 1: the tables as they existed before versioning, so older databases fit in unchanged.
    (
        """CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY, box_id TEXT NOT NULL, status TEXT NOT NULL,
            consent_processing INTEGER NOT NULL DEFAULT 0,
            consent_pool INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, finished_at TEXT, error TEXT,
            portrait_id TEXT, match_portrait_id TEXT, output_path TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS portraits (
            id TEXT PRIMARY KEY, session_id TEXT, path TEXT NOT NULL,
            pool_eligible INTEGER NOT NULL, created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL, use_count INTEGER NOT NULL DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS devices (
            id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL, revoked_at TEXT
        )""",
    ),
    # 2: M2. Idempotency keys, and consent as records instead of two flags on the session.
    (
        "ALTER TABLE sessions ADD COLUMN create_key TEXT",
        "CREATE UNIQUE INDEX sessions_create_key ON sessions(box_id, create_key) WHERE create_key IS NOT NULL",
        "ALTER TABLE portraits ADD COLUMN upload_key TEXT",
        "ALTER TABLE portraits ADD COLUMN upload_sha256 TEXT",
        """CREATE TABLE consent_records (
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
            purpose TEXT NOT NULL CHECK (purpose IN ('processing', 'pool')),
            policy_version TEXT NOT NULL, granted_at TEXT NOT NULL, withdrawn_at TEXT,
            provenance TEXT NOT NULL, evidence_ref TEXT,
            UNIQUE (session_id, purpose)
        )""",
        # The flags never recorded when consent was given, so created_at is the closest known
        # time and the provenance says these records were converted.
        """INSERT INTO consent_records(id, session_id, purpose, policy_version, granted_at, provenance)
           SELECT lower(hex(randomblob(16))), id, 'processing', 'pre-m2', created_at, 'migrated_flag'
           FROM sessions WHERE consent_processing = 1""",
        """INSERT INTO consent_records(id, session_id, purpose, policy_version, granted_at, provenance)
           SELECT lower(hex(randomblob(16))), id, 'pool', 'pre-m2', created_at, 'migrated_flag'
           FROM sessions WHERE consent_pool = 1""",
        "ALTER TABLE sessions DROP COLUMN consent_processing",
        "ALTER TABLE sessions DROP COLUMN consent_pool",
    ),
    # 3: M3. Admin-imported seeds, an audit trail, and private withdrawal codes.
    (
        """CREATE TABLE seeds (
            id TEXT PRIMARY KEY, path TEXT NOT NULL, rights_ref TEXT NOT NULL,
            imported_at TEXT NOT NULL, removed_at TEXT, use_count INTEGER NOT NULL DEFAULT 0
        )""",
        """CREATE TABLE audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT NOT NULL, event TEXT NOT NULL,
            session_id TEXT, portrait_id TEXT, device_id TEXT, detail TEXT
        )""",
        "ALTER TABLE sessions ADD COLUMN withdrawal_hash TEXT",
        "CREATE UNIQUE INDEX sessions_withdrawal_hash ON sessions(withdrawal_hash) WHERE withdrawal_hash IS NOT NULL",
    ),
    # 4: M4. Durable generation jobs.
    (
        """CREATE TABLE generation_jobs (
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL UNIQUE, provider TEXT NOT NULL,
            prompt_version TEXT NOT NULL, status TEXT NOT NULL, run_id TEXT,
            attempts INTEGER NOT NULL DEFAULT 0, seed INTEGER NOT NULL,
            frames_done INTEGER NOT NULL DEFAULT 0, frames_total INTEGER NOT NULL,
            next_attempt_at TEXT NOT NULL, error TEXT, metrics TEXT,
            created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT
        )""",
        "CREATE INDEX generation_jobs_waiting ON generation_jobs(status, next_attempt_at)",
    ),
    # 5: pairing a box with a short code (ADR-021).
    (
        """CREATE TABLE device_pairings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL, code_hash TEXT NOT NULL,
            created_at TEXT NOT NULL, expires_at TEXT NOT NULL, used_at TEXT,
            wrong_guesses INTEGER NOT NULL DEFAULT 0
        )""",
    ),
    # 6: the dashboard (ADR-022): versioned content, box status, and its password.
    (
        """CREATE TABLE prompt_sets (
            version TEXT PRIMARY KEY, content TEXT NOT NULL, created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0
        )""",
        """CREATE TABLE consent_notices (
            version TEXT PRIMARY KEY, text TEXT NOT NULL, created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0
        )""",
        """CREATE TABLE device_status (
            device_id TEXT PRIMARY KEY, state TEXT NOT NULL, detail TEXT, reported_at TEXT NOT NULL
        )""",
        """CREATE TABLE dashboard_password (
            id INTEGER PRIMARY KEY CHECK (id = 1), salt TEXT NOT NULL, hash TEXT NOT NULL,
            iterations INTEGER NOT NULL, set_at TEXT NOT NULL
        )""",
    ),
    # 7: social presence (ADR-023): recorded events and a public name per box.
    (
        """CREATE TABLE presence_events (
            event_id TEXT PRIMARY KEY, type TEXT NOT NULL, box_id TEXT NOT NULL, session_id TEXT,
            correlation_id TEXT NOT NULL, occurred_at TEXT NOT NULL, simulated INTEGER NOT NULL DEFAULT 0
        )""",
        "CREATE INDEX presence_events_time ON presence_events(occurred_at)",
        "ALTER TABLE devices ADD COLUMN public_label TEXT",
    ),
)


def schema_version(db: sqlite3.Connection) -> int:
    return db.execute("PRAGMA user_version").fetchone()[0]


def migrate(db: sqlite3.Connection) -> None:
    """Bring the database up to date in one transaction, so a failure changes nothing."""
    if schema_version(db) == len(MIGRATIONS):
        return
    db.execute("BEGIN IMMEDIATE")
    try:
        # Read again now that the write lock is held: another connection may have migrated.
        version = schema_version(db)
        if version > len(MIGRATIONS):
            raise RuntimeError(f"database schema version {version} is newer than this server supports")
        for statements in MIGRATIONS[version:]:
            for statement in statements:
                db.execute(statement)
        db.execute(f"PRAGMA user_version = {len(MIGRATIONS)}")
        db.commit()
    except BaseException:
        db.rollback()
        raise
