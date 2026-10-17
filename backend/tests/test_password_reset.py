"""Password reset: the flow, and the ways it must refuse to be abused."""
from datetime import datetime, timedelta

import pytest

from app.databases.database import SessionLocal
from app.models.password_reset import PasswordReset
from conftest import read_reset_token_hash


@pytest.fixture
def capture_reset_link(monkeypatch):
    """Intercepts the outgoing email so a test can read the token.

    Patched where it is USED (app.api.auth) rather than where it is
    defined - the router imported the name at module load, so patching
    the email module itself would have no effect.
    """
    sent = {}

    def fake_send(to_email, link):
        sent["to"] = to_email
        sent["link"] = link
        return True

    monkeypatch.setattr("app.api.auth.send_password_reset_email", fake_send)
    return sent


def _token_from(link: str) -> str:
    return link.split("reset=")[1]


def test_full_reset_flow(client, verified_user, capture_reset_link):
    email = verified_user["email"]

    assert client.post("/forgot-password", json={"email": email}).status_code == 202
    token = _token_from(capture_reset_link["link"])

    resp = client.post("/reset-password", json={"token": token, "new_password": "BrandNew9!"})
    assert resp.status_code == 200
    # Resetting logs you straight in, so the user never sees a login screen.
    assert resp.json()["access_token"]

    # The old password is genuinely gone, the new one genuinely works.
    assert client.post("/login", json={"email": email, "password": verified_user["password"]}).status_code == 401
    assert client.post("/login", json={"email": email, "password": "BrandNew9!"}).status_code == 200


def test_unknown_email_is_indistinguishable(client, capture_reset_link):
    """A 404 here would be a free account-enumeration oracle."""
    resp = client.post("/forgot-password", json={"email": "nobody@example.test"})
    assert resp.status_code == 202
    assert "if that email has an account" in resp.json()["message"].lower()
    # No mail was actually sent, which is the only difference.
    assert "link" not in capture_reset_link


def test_token_is_single_use(client, verified_user, capture_reset_link):
    client.post("/forgot-password", json={"email": verified_user["email"]})
    token = _token_from(capture_reset_link["link"])

    assert client.post("/reset-password", json={"token": token, "new_password": "FirstGo9!"}).status_code == 200
    second = client.post("/reset-password", json={"token": token, "new_password": "SecondGo9!"})
    assert second.status_code == 400
    assert "already been used" in second.json()["detail"]


def test_requesting_again_invalidates_the_previous_token(client, verified_user, capture_reset_link):
    client.post("/forgot-password", json={"email": verified_user["email"]})
    first = _token_from(capture_reset_link["link"])

    client.post("/forgot-password", json={"email": verified_user["email"]})
    second = _token_from(capture_reset_link["link"])
    assert first != second

    # Clicking the older email must not work.
    assert client.post("/reset-password", json={"token": first, "new_password": "Whatever9!"}).status_code == 400
    assert client.post("/reset-password", json={"token": second, "new_password": "Whatever9!"}).status_code == 200


def test_expired_token_is_rejected(client, verified_user, capture_reset_link):
    client.post("/forgot-password", json={"email": verified_user["email"]})
    token = _token_from(capture_reset_link["link"])

    # Age the record rather than waiting 30 minutes.
    db = SessionLocal()
    try:
        row = db.query(PasswordReset).filter(
            PasswordReset.token_hash == read_reset_token_hash(verified_user["email"])
        ).first()
        row.expires_at = datetime.utcnow() - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()

    resp = client.post("/reset-password", json={"token": token, "new_password": "TooLate9!"})
    assert resp.status_code == 400
    assert "expired" in resp.json()["detail"]


def test_garbage_token_is_rejected(client):
    resp = client.post("/reset-password", json={"token": "x" * 40, "new_password": "Nope1234!"})
    assert resp.status_code == 400


def test_reset_enforces_the_same_password_rules_as_registration(client, verified_user, capture_reset_link):
    """A weaker rule here would quietly bypass the whole password policy."""
    client.post("/forgot-password", json={"email": verified_user["email"]})
    token = _token_from(capture_reset_link["link"])

    resp = client.post("/reset-password", json={"token": token, "new_password": "weak"})
    assert resp.status_code == 422


def test_only_the_hash_is_stored(client, verified_user, capture_reset_link):
    """A leaked database must not hand over working reset tokens."""
    client.post("/forgot-password", json={"email": verified_user["email"]})
    token = _token_from(capture_reset_link["link"])

    stored = read_reset_token_hash(verified_user["email"])
    assert stored is not None
    assert stored != token
    assert len(stored) == 64  # sha256 hex


def test_change_password_requires_the_current_one(client, verified_user):
    headers = verified_user["headers"]

    wrong = client.post(
        "/me/change-password",
        json={"current_password": "NotIt123!", "new_password": "Fresh9876!"},
        headers=headers,
    )
    assert wrong.status_code == 401

    right = client.post(
        "/me/change-password",
        json={"current_password": verified_user["password"], "new_password": "Fresh9876!"},
        headers=headers,
    )
    assert right.status_code == 200
    assert right.json()["access_token"]
    assert client.post(
        "/login", json={"email": verified_user["email"], "password": "Fresh9876!"}
    ).status_code == 200
