"""Shared test fixtures.

The environment is configured BEFORE importing the app, because
app.databases.database reads DATABASE_URL at import time and
app.main creates tables on import. Tests therefore run against a
throwaway database and never touch the developer's real one.
"""
import os
import uuid

import pytest

# A file-based SQLite DB per test session: fast, isolated, and thrown away
# afterwards. Set before any app import.
TEST_DB = f"./test_{uuid.uuid4().hex[:8]}.db"
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", f"sqlite:///{TEST_DB}")
os.environ["SECRET_KEY"] = "test-secret-not-used-anywhere-real"
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


@pytest.fixture(scope="session", autouse=True)
def _database():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


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


@pytest.fixture
def verified_user(client, unique_email):
    """Registers, verifies and returns a ready-to-use account with its
    auth headers - the starting point for most tests."""
    password = "Secret123!"
    client.post("/register", json={"name": "Test Athlete", "email": unique_email, "password": password})
    resp = client.post("/verify-email", json={"email": unique_email, "code": read_otp(unique_email)})
    token = resp.json()["access_token"]
    return {
        "email": unique_email,
        "password": password,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
    }
