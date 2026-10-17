"""Access tokens, refresh rotation and revocation."""
import pytest

from app.services import cache
from app.services.auth_services import ACCESS, REFRESH, create_access_token, decode_token

# Revocation needs a shared store. Without Redis the app falls back to
# short token lifetimes, which is a documented degradation rather than a
# bug - so these tests skip instead of failing.
needs_redis = pytest.mark.skipif(
    not cache.available(), reason="revocation requires Redis"
)


def test_login_returns_both_tokens(client, verified_user):
    resp = client.post(
        "/login", json={"email": verified_user["email"], "password": verified_user["password"]}
    )
    body = resp.json()
    assert body["access_token"] and body["refresh_token"]
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0


def test_refresh_token_is_not_accepted_as_an_access_token(client, verified_user):
    """Otherwise a 30-day token would work on every endpoint."""
    refresh = verified_user["refresh_token"]
    resp = client.get("/me", headers={"Authorization": f"Bearer {refresh}"})
    assert resp.status_code == 401


def test_access_token_is_not_accepted_for_refresh(client, verified_user):
    resp = client.post("/refresh", json={"refresh_token": verified_user["token"]})
    assert resp.status_code == 401


def test_refresh_issues_a_working_pair(client, verified_user):
    resp = client.post("/refresh", json={"refresh_token": verified_user["refresh_token"]})
    assert resp.status_code == 200
    new = resp.json()
    assert client.get("/me", headers={"Authorization": f"Bearer {new['access_token']}"}).status_code == 200


def test_token_types_are_tagged(verified_user):
    assert decode_token(verified_user["token"], ACCESS)["type"] == ACCESS
    assert decode_token(verified_user["refresh_token"], REFRESH)["type"] == REFRESH


def test_every_token_has_a_unique_id():
    """The jti is what makes single-token revocation possible."""
    a = decode_token(create_access_token("a@example.test"), ACCESS)
    b = decode_token(create_access_token("a@example.test"), ACCESS)
    assert a["jti"] != b["jti"]


def test_tampered_token_is_rejected(verified_user):
    token = verified_user["token"]
    # Flip a character in the signature.
    tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
    assert decode_token(tampered, ACCESS) is None


@needs_redis
def test_refresh_rotation_burns_the_old_token(client, verified_user):
    """Replaying a stolen refresh token must fail, not grant access."""
    old = verified_user["refresh_token"]
    assert client.post("/refresh", json={"refresh_token": old}).status_code == 200
    assert client.post("/refresh", json={"refresh_token": old}).status_code == 401


@needs_redis
def test_logout_kills_the_current_token(client, verified_user):
    headers = verified_user["headers"]
    assert client.get("/me", headers=headers).status_code == 200

    out = client.post("/logout", headers=headers)
    assert out.status_code == 200 and out.json()["immediate"] is True
    assert client.get("/me", headers=headers).status_code == 401


@needs_redis
def test_logout_all_invalidates_every_session(client, verified_user):
    first = verified_user["headers"]
    second_login = client.post(
        "/login", json={"email": verified_user["email"], "password": verified_user["password"]}
    ).json()
    second = {"Authorization": f"Bearer {second_login['access_token']}"}

    assert client.post("/logout-all", headers=first).status_code == 200

    # Both sessions are gone, not just the one that asked.
    assert client.get("/me", headers=first).status_code == 401
    assert client.get("/me", headers=second).status_code == 401


@needs_redis
def test_password_change_revokes_existing_sessions(client, verified_user):
    stolen = verified_user["headers"]
    client.post(
        "/me/change-password",
        json={"current_password": verified_user["password"], "new_password": "Rotated9!"},
        headers=stolen,
    )
    # The session an attacker might be holding dies with the password.
    assert client.get("/me", headers=stolen).status_code == 401


def test_refresh_rejects_a_token_for_an_unknown_account(client):
    """The database has the final word, not the signature."""
    orphan = create_access_token("ghost@example.test")  # valid signature, no user
    assert client.get("/me", headers={"Authorization": f"Bearer {orphan}"}).status_code == 401
