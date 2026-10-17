from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings

# Already normalised by app.config: the scheme and the DBAPI driver are
# pinned there so every consumer sees the same URL.
DATABASE_URL = settings.database_url

_is_sqlite = DATABASE_URL.startswith("sqlite")


def _connect_args(url: str) -> dict:
    """libpq connection options.

    statement_timeout kills runaway queries so one of them cannot hold a
    connection and starve the pool. It is sent as a libpq *startup*
    parameter - and PgBouncer-style poolers reject those outright:

        ERROR: unsupported startup parameter in options: statement_timeout

    Neon's pooled endpoint (the "-pooler" host) is exactly that, so the
    option is only sent on a direct connection. Plain Postgres accepts it,
    which is why CI never saw this - the failure needs a real pooler.
    """
    args = {"connect_timeout": 10}
    if "-pooler." not in url:
        args["options"] = "-c statement_timeout=15000"
    return args

# SQLite (used in automated tests) is single-threaded by default and has no
# connection pool to size.
if _is_sqlite:
    engine_kwargs = {"connect_args": {"check_same_thread": False}}
else:
    engine_kwargs = {
        # pool_pre_ping tests a connection before handing it out. Serverless
        # Postgres (Neon) sleeps when idle and drops pooled connections, so
        # without this the first request after a quiet spell fails.
        "pool_pre_ping": True,
        "pool_recycle": 300,
        # Sized against the database's connection ceiling, not the app's
        # appetite: workers x (pool_size + max_overflow) must stay under it.
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        # Fail fast rather than queueing forever when the pool is drained -
        # a request that waits 30s has already failed from the user's view.
        "pool_timeout": 10,
        "connect_args": _connect_args(DATABASE_URL),
    }

# One engine per application: it owns the pool of reusable DB connections.
engine = create_engine(DATABASE_URL, **engine_kwargs)

# Factory that produces one short-lived Session ("cart") per request.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    # FastAPI dependency: opens a session, hands it to the endpoint,
    # and guarantees it is closed afterwards - even if the request fails.
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
