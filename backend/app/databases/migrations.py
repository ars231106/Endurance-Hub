"""Running Alembic migrations at startup.

Migrating on boot suits a single-service deployment: there is no separate
release step on Render, so the alternative is remembering to run them by
hand, which eventually nobody does.

The subtlety is the existing production database. It was built by
create_all and has no alembic_version table, so running the baseline
migration would try to create tables that already exist. The fix is to
detect that case and *stamp* the database at the baseline instead -
recording "you are already at revision 0001" without executing it.

The caveat worth knowing: with several instances booting at once, each
would try to migrate. Alembic takes a lock on its version table so they
serialise rather than corrupt anything, but at larger scale migrations
belong in a release command that runs exactly once.
"""

import logging
from pathlib import Path

from sqlalchemy import inspect

from app.config import settings
from app.databases.database import engine

log = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"

BASELINE_REVISION = "0001"


def _config():
    from alembic.config import Config

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    # Escaped: ConfigParser reads a bare % as interpolation syntax, and a
    # URL-encoded password can contain one.
    cfg.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    return cfg


def run_migrations() -> bool:
    """Upgrades the database to head. True if Alembic handled it."""
    if not ALEMBIC_INI.exists():
        return False

    try:
        from alembic import command
    except ImportError:
        log.info("alembic not installed; falling back to create_all")
        return False

    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())

        cfg = _config()

        # A pre-Alembic database: tables exist but no version table. Record
        # where it already is instead of replaying creation.
        if "alembic_version" not in tables and "users" in tables:
            log.info("stamping existing database at baseline")
            command.stamp(cfg, BASELINE_REVISION)

        command.upgrade(cfg, "head")
        log.info("migrations applied")
        return True
    except Exception as exc:  # noqa: BLE001
        # A failed migration must be loud, but it should not stop the app
        # from starting with a schema that may already be correct - the
        # fallback path re-checks and the readiness probe will catch a
        # genuinely broken database.
        log.error("migration failed, falling back: %s", exc)
        return False
