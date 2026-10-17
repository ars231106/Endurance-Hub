"""HTTP hardening, rate limiting and operational endpoints."""
import pytest

from app.services import rate_limit


def test_security_headers_are_set(client):
    resp = client.get("/health")
    h = resp.headers
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    # Reset tokens travel in the query string, so no referrer may leak.
    assert h["referrer-policy"] == "no-referrer"
    assert "content-security-policy" in h
    assert "frame-ancestors 'none'" in h["content-security-policy"]


def test_every_response_carries_a_request_id(client):
    resp = client.get("/health")
    assert resp.headers["x-request-id"]
    assert "server-timing" in resp.headers


def test_supplied_request_id_is_echoed(client):
    """Lets a trace be followed across services rather than restarting."""
    resp = client.get("/health", headers={"X-Request-ID": "trace-me-123"})
    assert resp.headers["x-request-id"] == "trace-me-123"


def test_oversized_body_is_rejected_before_it_is_read(client):
    """A request declaring a huge body must be refused on its header,
    not after it has been buffered into memory."""
    from app.config import settings

    oversized = b"x" * (settings.max_request_bytes + 1024)
    resp = client.post(
        "/login",
        content=oversized,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 413


def test_health_is_liveness_only(client):
    body = client.get("/health").json()
    assert body["status"] == "healthy"
    # Must NOT report on dependencies: a liveness probe that fails when
    # the database blips causes a restart loop that fixes nothing.
    assert "checks" not in body


def test_ready_checks_dependencies(client):
    body = client.get("/ready").json()
    assert body["ready"] is True
    assert body["checks"]["database"] == "ok"


def test_metrics_counts_requests(client):
    before = client.get("/metrics").json()["requests_total"]
    client.get("/health")
    after = client.get("/metrics").json()["requests_total"]
    assert after > before


class TestRateLimiting:
    def test_repeated_failed_logins_are_throttled(self, client, verified_user):
        payload = {"email": verified_user["email"], "password": "WrongPassword1!"}
        limit = rate_limit.LIMITS["login"].requests

        statuses = [client.post("/login", json=payload).status_code for _ in range(limit + 3)]

        assert 401 in statuses, "early attempts should be ordinary auth failures"
        assert 429 in statuses, "the limiter must eventually refuse"
        # Once throttled it stays throttled for the window.
        assert statuses[-1] == 429

    def test_throttled_response_tells_the_client_how_to_behave(self, client, verified_user):
        payload = {"email": verified_user["email"], "password": "WrongPassword1!"}
        for _ in range(rate_limit.LIMITS["login"].requests + 2):
            resp = client.post("/login", json=payload)

        assert resp.status_code == 429
        assert resp.headers["retry-after"]
        assert resp.headers["ratelimit-limit"]

    def test_the_limiter_runs_before_the_password_check(self, client, verified_user):
        """bcrypt is expensive, so a throttled request must not reach it -
        otherwise the limiter protects the account but not the CPU."""
        payload = {"email": verified_user["email"], "password": "WrongPassword1!"}
        for _ in range(rate_limit.LIMITS["login"].requests + 1):
            client.post("/login", json=payload)

        # Even the CORRECT password is refused while throttled, which only
        # happens if the limit is enforced before verification.
        correct = client.post(
            "/login", json={"email": verified_user["email"], "password": verified_user["password"]}
        )
        assert correct.status_code == 429

    def test_limits_reset_between_buckets(self, client):
        """Exhausting one endpoint must not lock the others."""
        for _ in range(rate_limit.LIMITS["login"].requests + 2):
            client.post("/login", json={"email": "a@b.test", "password": "Wrong123!"})

        # A different bucket entirely, so this still works.
        assert client.get("/health").status_code == 200


@pytest.mark.parametrize("path", ["/me", "/activities", "/analytics/summary"])
def test_protected_routes_reject_missing_credentials(client, path):
    assert client.get(path).status_code == 401
