"""SQLite ledger: schema, connection factory, and migrations.

No ORM (PROJECT_PLAN.md section 7). The ledger is the single source of truth for
run authorization and every reservation/transaction. Concurrency correctness is
the priority, so connections are configured to serialize writers cleanly:

- ``journal_mode=WAL``: readers never block the single writer.
- ``busy_timeout``: a writer waits for the lock instead of failing immediately
  with "database is locked", which is what makes 100 concurrent reservers work.
- ``isolation_level=None``: autocommit mode so the engine can issue explicit
  ``BEGIN IMMEDIATE`` transactions and take the write lock up front.
- ``foreign_keys=ON``: reservations/transactions cannot dangle off a missing run.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# Milestone 1 uses three tables. `sessions` (PROJECT_PLAN.md section 7) arrives
# with the process wrapper in Milestone 2; it is intentionally omitted until then
# to avoid unused schema.
SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id                        TEXT PRIMARY KEY,
    created_at                TEXT NOT NULL,
    ended_at                  TEXT,
    command                   TEXT NOT NULL,
    authorized_microdollars   INTEGER NOT NULL,
    status                    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reservations (
    id                        TEXT PRIMARY KEY,
    run_id                    TEXT NOT NULL REFERENCES runs(id),
    provider                  TEXT NOT NULL,
    request_hash              TEXT,
    reserved_microdollars     INTEGER NOT NULL,
    settled_microdollars      INTEGER,
    status                    TEXT NOT NULL,
    created_at                TEXT NOT NULL,
    settled_at                TEXT
);

CREATE TABLE IF NOT EXISTS transactions (
    id                        TEXT PRIMARY KEY,
    run_id                    TEXT NOT NULL REFERENCES runs(id),
    reservation_id            TEXT REFERENCES reservations(id),
    provider                  TEXT NOT NULL,
    service                   TEXT,
    kind                      TEXT NOT NULL,
    amount_microdollars       INTEGER NOT NULL,
    currency                  TEXT NOT NULL,
    external_id               TEXT,
    payment_id                TEXT,
    metadata_json             TEXT,
    created_at                TEXT NOT NULL
);

-- Reservation lookups during reserve() aggregate by run + status, so index it.
CREATE INDEX IF NOT EXISTS idx_reservations_run_status
    ON reservations(run_id, status);
CREATE INDEX IF NOT EXISTS idx_transactions_run
    ON transactions(run_id);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a ledger connection configured for correct concurrent writes.

    A fresh connection is cheap and keeps each operation thread-safe (sqlite3
    connections are not meant to be shared across threads), which is how the
    engine stays correct under the concurrency tests.
    """
    conn = sqlite3.connect(
        db_path,
        # Autocommit mode: we manage BEGIN IMMEDIATE / COMMIT ourselves so the
        # write lock is taken at the start of the read-modify-write in reserve().
        isolation_level=None,
        # Block up to 30s for the write lock rather than erroring instantly.
        timeout=30.0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db(db_path: Path) -> None:
    """Create the ledger file and schema if they do not already exist.

    Idempotent: safe to call from ``paygo init`` repeatedly and from every engine
    construction, because all DDL uses ``IF NOT EXISTS``.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()
