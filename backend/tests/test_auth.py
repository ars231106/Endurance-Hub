"""Registration, email verification and login."""
from conftest import read_otp


def test_health(client):
    assert client.get("/health").json() == {"status": "healthy"}


def test_register_creates_unverified_account(client, unique_email):
    r = client.post("/register", json={"name": "A", "email": unique_email, "password": "secret123"})
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == unique_email
    # The response schema must never expose a password or its hash.
    assert "password" not in body and "password_hash" not in body


def test_duplicate_email_is_rejected(client, unique_email):
    payload = {"name": "A", "email": unique_email, "password": "secret123"}
    assert client.post("/register", json=payload).status_code == 201
    assert client.post("/register", json=payload).status_code == 409


def test_short_password_is_rejected(client, unique_email):
    r = client.post("/register", json={"name": "A", "email": unique_email, "password": "abc"})
    assert r.status_code == 422


def test_login_blocked_until_email_verified(client, unique_email):
    client.post("/register", json={"name": "A", "email": unique_email, "password": "secret123"})
    r = client.post("/login", json={"email": unique_email, "password": "secret123"})
    assert r.status_code == 403


def test_wrong_otp_is_rejected_then_correct_one_works(client, unique_email):
    client.post("/register", json={"name": "A", "email": unique_email, "password": "secret123"})
    assert client.post("/verify-email", json={"email": unique_email, "code": "000000"}).status_code == 400

    r = client.post("/verify-email", json={"email": unique_email, "code": read_otp(unique_email)})
    assert r.status_code == 200
    assert r.json()["access_token"]


def test_otp_must_be_six_digits(client, unique_email):
    client.post("/register", json={"name": "A", "email": unique_email, "password": "secret123"})
    assert client.post("/verify-email", json={"email": unique_email, "code": "12ab56"}).status_code == 422


def test_otp_is_single_use(client, unique_email):
    client.post("/register", json={"name": "A", "email": unique_email, "password": "secret123"})
    code = read_otp(unique_email)
    assert client.post("/verify-email", json={"email": unique_email, "code": code}).status_code == 200
    # Already verified, and the code was consumed.
    assert client.post("/verify-email", json={"email": unique_email, "code": code}).status_code == 400


def test_resend_is_rate_limited(client, unique_email):
    client.post("/register", json={"name": "A", "email": unique_email, "password": "secret123"})
    assert client.post("/resend-otp", json={"email": unique_email}).status_code == 429


def test_login_and_me(client, verified_user):
    r = client.post("/login", json={"email": verified_user["email"], "password": "secret123"})
    assert r.status_code == 200

    me = client.get("/me", headers=verified_user["headers"])
    assert me.status_code == 200
    assert me.json()["email"] == verified_user["email"]


def test_wrong_password_rejected(client, verified_user):
    r = client.post("/login", json={"email": verified_user["email"], "password": "wrong"})
    assert r.status_code == 401
    # Unknown email must fail identically, so accounts can't be enumerated.
    r2 = client.post("/login", json={"email": "nobody@example.test", "password": "wrong"})
    assert r2.status_code == 401
    assert r.json()["detail"] == r2.json()["detail"]


def test_protected_route_requires_a_token(client):
    assert client.get("/me").status_code == 401
    assert client.get("/me", headers={"Authorization": "Bearer not-a-real-token"}).status_code == 401


def test_profile_update_and_validation(client, verified_user):
    r = client.put("/me/profile", json={"birth_year": 2004, "resting_hr": 55},
                   headers=verified_user["headers"])
    assert r.status_code == 200
    assert r.json()["birth_year"] == 2004 and r.json()["resting_hr"] == 55

    # Physiologically impossible values are rejected.
    assert client.put("/me/profile", json={"resting_hr": 400},
                      headers=verified_user["headers"]).status_code == 422


def test_account_deletion_lifecycle(client, verified_user):
    h = verified_user["headers"]
    assert client.post("/me/delete", json={"password": "wrong"}, headers=h).status_code == 401
    assert client.post("/me/cancel-deletion", headers=h).status_code == 400  # nothing scheduled

    r = client.post("/me/delete", json={"password": "secret123"}, headers=h)
    assert r.status_code == 200 and r.json()["delete_requested_at"]
    assert client.post("/me/delete", json={"password": "secret123"}, headers=h).status_code == 400

    assert client.post("/me/cancel-deletion", headers=h).json()["delete_requested_at"] is None
