"""Shared test fixtures.

The environment is configured BEFORE importing the app, because
app.config reads the environment at import time and the database engine
is built from it. Tests therefore run against a throwaway database and
never touch the developer's real one.
"""
import os
import uuid

import pytest

# A file-based SQLite DB per test session: fast, isolated, and thrown away
# afterwards. Set before any app import.
TEST_DB = f"./test_{uuid.uuid4().hex[:8]}.db"
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", f"sqlite:///{TEST_DB}")
os.environ["SECRET_KEY"] = "test-secret-not-used-anywhere-real-but-long-enough"
os.environ["ENVIRONMENT"] = "test"
# Readable output when a test fails.
os.environ["JSON_LOGS"] = "false"
os.environ["LOG_LEVEL"] = "WARNING"
# Caching would make one test's writes invisible to the next assertion.
os.environ["ANALYTICS_CACHE_SECONDS"] = "0"
# Only set when CI provides a Redis service; otherwise the app exercises
# its degraded path, which is itself worth testing.
if os.environ.get("TEST_REDIS_URL"):
    os.environ["REDIS_URL"] = os.environ["TEST_REDIS_URL"]
# Blank SMTP settings force the email service into console mode, so tests
# can never send a real message even if a .env file exists locally.
os.environ.setdefault("SMTP_HOST", "")
os.environ.setdefault("SMTP_USER", "")
os.environ.setdefault("SMTP_PASS", "")

from fastapi.testclient import TestClient  # noqa: E402

from app.databases.database import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.models.otp import EmailOTP  # noqa: E402
from app.models.password_reset import PasswordReset  # noqa: E402,F401
from app.services import cache, rate_limit  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


@pytest.fixture(autouse=True)
def _reset_limits():
    """Rate limit counters must not leak between tests.

    Without this, the twentieth test to call /login would be rejected
    because the first nineteen already used the window - a real limiter
    doing its job, but it would make the suite order-dependent.
    """
    rate_limit.reset()
    client = cache.get_client()
    if client is not None:
        try:
            client.flushdb()
        except Exception:  # noqa: BLE001
            pass
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def unique_email():
    return f"user_{uuid.uuid4().hex[:10]}@example.test"


def read_otp(email: str) -> str:
    """Reads the verification code straight from the database - the test
    equivalent of opening the inbox."""
    db = SessionLocal()
    try:
        otp = db.query(EmailOTP).filter(EmailOTP.email == email).first()
        return otp.code if otp else None
    finally:
        db.close()


def read_reset_token_hash(email: str) -> str:
    """The stored hash of the newest reset token for an address."""
    db = SessionLocal()
    try:
        row = (
            db.query(PasswordReset)
            .filter(PasswordReset.email == email)
            .order_by(PasswordReset.id.desc())
            .first()
        )
        return row.token_hash if row else None
    finally:
        db.close()


@pytest.fixture
def verified_user(client, unique_email):
    """Registers, verifies and returns a ready-to-use account with its
    auth headers - the starting point for most tests."""
    password = "Secret123!"
    client.post("/register", json={"name": "Test Athlete", "email": unique_email, "password": password})
    resp = client.post("/verify-email", json={"email": unique_email, "code": read_otp(unique_email)})
    body = resp.json()
    token = body["access_token"]
    return {
        "email": unique_email,
        "password": password,
        "token": token,
        "refresh_token": body.get("refresh_token"),
        "headers": {"Authorization": f"Bearer {token}"},
    }
