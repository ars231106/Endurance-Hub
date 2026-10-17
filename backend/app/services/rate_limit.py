"""Fixed-window rate limiting, Redis-backed with an in-process fallback.

Why this exists: bcrypt is deliberately slow, which protects passwords but
also means every login attempt costs the SERVER real CPU. Without a limit,
an attacker gets both a brute-force channel and a cheap denial of service
from the same requests.

Keyed by client IP *and* by the submitted email where one exists, so a
single attacker cannot lock out a victim by hammering their address from
elsewhere - the per-email bucket is deliberately looser than the per-IP one.
"""

import logging
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from fastapi import HTTPException, Request, status

from app.services import cache

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Limit:
    requests: int
    window_seconds: int

    @property
    def label(self) -> str:
        return f"{self.requests}/{self.window_seconds}s"


# Tuned per endpoint by how expensive and how sensitive each one is.
LIMITS = {
    "login": Limit(10, 300),         # bcrypt is costly; 10 tries per 5 min
    "register": Limit(5, 3600),      # account creation is rarely bulk
    "otp_verify": Limit(10, 600),    # the OTP row has its own attempt cap
    "otp_resend": Limit(5, 3600),    # sending mail costs money
    "password_reset": Limit(5, 3600),
    "write": Limit(120, 60),         # ordinary authenticated writes
}


# --- in-process fallback --------------------------------------------------
# Correct for a single worker, approximate across several. That is an
# acceptable degradation: a limiter that is slightly wrong beats no limiter.
_local: Dict[str, Tuple[int, float]] = {}
_local_lock = threading.Lock()
_LOCAL_MAX_KEYS = 10_000


def _local_incr(key: str, window: int) -> int:
    now = time.monotonic()
    with _local_lock:
        # Opportunistic cleanup so the dict cannot grow without bound.
        if len(_local) > _LOCAL_MAX_KEYS:
            for k, (_, expiry) in list(_local.items()):
                if expiry <= now:
                    _local.pop(k, None)

        count, expiry = _local.get(key, (0, 0.0))
        if expiry <= now:
            count, expiry = 0, now + window
        count += 1
        _local[key] = (count, expiry)
        return count


def client_ip(request: Request) -> str:
    """The caller's address, honouring the proxy header Render sets.

    X-Forwarded-For is a comma-separated chain; the first entry is the
    original client. It is spoofable in general, which is why it is only
    trusted here because a known proxy sits in front of this app.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check(bucket: str, identifier: str, limit: Limit) -> Tuple[bool, int]:
    key = f"rl:{bucket}:{identifier}"
    count = cache.incr_with_ttl(key, limit.window_seconds)
    if count is None:
        count = _local_incr(key, limit.window_seconds)
    remaining = max(0, limit.requests - count)
    return count <= limit.requests, remaining


def enforce(
    request: Request,
    bucket: str,
    subject: Optional[str] = None,
) -> None:
    """Raises 429 when the caller has exceeded the bucket's limit.

    The response carries Retry-After and the standard RateLimit-* headers
    so a well-behaved client can back off instead of retrying blindly.
    """
    limit = LIMITS[bucket]

    ok, remaining = _check(bucket, client_ip(request), limit)
    # A second, looser bucket per account. Catches distributed attempts
    # against one victim that the per-IP limit would miss entirely.
    if ok and subject:
        account_limit = Limit(limit.requests * 3, limit.window_seconds)
        ok, _ = _check(f"{bucket}:acct", subject.lower(), account_limit)

    if not ok:
        log.warning("rate limit hit: bucket=%s ip=%s", bucket, client_ip(request))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please wait and try again.",
            headers={
                "Retry-After": str(limit.window_seconds),
                "RateLimit-Limit": str(limit.requests),
                "RateLimit-Remaining": "0",
                "RateLimit-Reset": str(limit.window_seconds),
            },
        )


def reset() -> None:
    """Clears the in-process store. Used between tests."""
    with _local_lock:
        _local.clear()
