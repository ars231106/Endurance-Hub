import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Connection settings come from the environment when available (production),
# and fall back to the local Postgres instance for development.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:ARS231106@localhost:5432/endurancehub",
)

# Several hosts (Render, Heroku) hand out URLs starting with "postgres://",
# a scheme SQLAlchemy 2 no longer recognises. Normalising it here avoids a
# deploy that fails on startup with an obscure dialect error.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# SQLite (used in automated tests) needs this flag because it is
# single-threaded by default; Postgres ignores it.
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

# One engine per application: it owns a pool of reusable DB connections.
engine = create_engine(DATABASE_URL, connect_args=connect_args)

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
