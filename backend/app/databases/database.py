from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings

DATABASE_URL = settings.database_url

# Several hosts (Render, Heroku) hand out URLs starting with "postgres://",
# a scheme SQLAlchemy 2 no longer recognises. Normalising it here avoids a
# deploy that fails on startup with an obscure dialect error.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

_is_sqlite = DATABASE_URL.startswith("sqlite")

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
        "connect_args": {
            # Kill queries that run away instead of letting them hold a
            # connection and starve everything else.
            "options": "-c statement_timeout=15000",
            "connect_timeout": 10,
        },
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
