"""Applies additive schema changes at startup.

SQLAlchemy's create_all() only creates missing TABLES - it never adds a
column to a table that already exists. So every new field used to mean
running a migration script by hand, and forgetting meant the app crashed
with "column ... does not exist".

These statements are all additive and idempotent, so running them on
every boot is safe and cheap. A production project would use Alembic for
proper versioned migrations (including destructive ones); this is the
lightweight equivalent for a single-database app.
"""
import logging

from sqlalchemy import text

# Every column added since the original schema, newest last.
MIGRATIONS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_verified BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS delete_requested_at TIMESTAMP NULL",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS birth_year INTEGER NULL",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS resting_hr INTEGER NULL",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS max_hr INTEGER NULL",
    "ALTER TABLE activities ADD COLUMN IF NOT EXISTS date DATE",
    "ALTER TABLE activities ADD COLUMN IF NOT EXISTS notes VARCHAR NULL",
    "ALTER TABLE activities ADD COLUMN IF NOT EXISTS avg_hr INTEGER NULL",
    # Duration-only sports (strength, football, yoga) store no distance.
    # Dropping an already-dropped NOT NULL is a no-op, so this is safe to
    # repeat.
    "ALTER TABLE activities ALTER COLUMN distance_km DROP NOT NULL",
]


def sync_schema(engine) -> int:
    """Runs each statement in its own transaction so one failure - say an
    unsupported dialect - can't roll back the rest. Returns how many
    succeeded."""
    if engine.dialect.name != "postgresql":
        # SQLite (used by tests) is created fresh by create_all(), and
        # doesn't support IF NOT EXISTS on ADD COLUMN anyway.
        return 0

    applied = 0
    for sql in MIGRATIONS:
        try:
            with engine.begin() as conn:
                conn.execute(text(sql))
            applied += 1
        except Exception as exc:  # noqa: BLE001 - log and keep going
            logging.warning("Schema sync skipped: %s (%s)", sql, exc)
    return applied
