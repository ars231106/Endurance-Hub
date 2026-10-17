"""Token issuing, verification and revocation.

Two token types with different jobs:

  access  - short-lived (30 min), sent with every request. Cannot be
            revoked cheaply, which is exactly why it expires quickly.
  refresh - long-lived (30 days), sent only to /refresh to obtain a new
            access token. Revocable, and rotated on every use.

Rotation matters: each refresh returns a NEW refresh token and revokes the
one just used. If a refresh token is stolen and replayed, the legitimate
client's next refresh fails - turning silent theft into a visible logout.

Revocation has two granularities:
  - one token, by its `jti`, for a single logout
  - every token for a user, by an epoch timestamp, for "log out everywhere"
    and for password changes. The epoch is one key per user rather than one
    key per token, so invalidating a thousand sessions costs a single write.
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from jose import JWTError, jwt

from app.config import settings
from app.services import cache

log = logging.getLogger(__name__)

ALGORITHM = "HS256"

ACCESS = "access"
REFRESH = "refresh"


def _now() -> datetime:
    return datetime.utcnow()


def _encode(subject: str, token_type: str, lifetime: timedelta) -> str:
    issued = _now()
    payload = {
        "sub": subject,
        "type": token_type,
        # Unique id, so a single token can be revoked by name.
        "jti": uuid.uuid4().hex,
        # Issued-at, compared against the user's revocation epoch.
        "iat": issued,
        "exp": issued + lifetime,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def create_access_token(subject: str) -> str:
    return _encode(subject, ACCESS, timedelta(minutes=settings.access_token_minutes))


def create_refresh_token(subject: str) -> str:
    return _encode(subject, REFRESH, timedelta(days=settings.refresh_token_days))


def create_token_pair(subject: str) -> dict:
    return {
        "access_token": create_access_token(subject),
        "refresh_token": create_refresh_token(subject),
        "token_type": "bearer",
        "expires_in": settings.access_token_minutes * 60,
    }


def decode_token(token: str, expected_type: str = ACCESS) -> Optional[dict]:
    """Verifies signature, expiry, type and revocation. None on any failure.

    Pinning `algorithms` is not optional: letting the token declare its own
    algorithm allows an attacker to set it to "none" and present an unsigned
    token as valid.
    """
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        return None

    # A refresh token must not be accepted as an access token. Without this
    # check, a 30-day token would work on every endpoint.
    if payload.get("type") != expected_type:
        return None

    subject = payload.get("sub")
    if not subject:
        return None

    if is_revoked(payload):
        return None

    return payload


# --- revocation -----------------------------------------------------------

def _jti_key(jti: str) -> str:
    return f"revoked:jti:{jti}"


def _epoch_key(subject: str) -> str:
    return f"revoked:user:{subject.lower()}"


def is_revoked(payload: dict) -> bool:
    """True when this specific token, or all of the user's tokens, are dead.

    Without Redis this always returns False and the app falls back to token
    expiry alone - which is why access tokens are short.
    """
    if not cache.available():
        return False

    jti = payload.get("jti")
    if jti and cache.exists(_jti_key(jti)):
        return True

    subject = payload.get("sub")
    issued_at = payload.get("iat")
    if subject and issued_at:
        raw = cache.cache_get(_epoch_key(subject))
        if raw is not None and float(issued_at) < float(raw):
            return True

    return False


def revoke_token(payload: dict) -> bool:
    """Revokes one token until it would have expired anyway.

    The denylist entry expires with the token, so the list stays bounded by
    the number of *live* sessions rather than growing forever.
    """
    jti = payload.get("jti")
    expiry = payload.get("exp")
    if not jti or not expiry:
        return False

    seconds_left = int(float(expiry) - _now().timestamp())
    if seconds_left <= 0:
        return True  # already expired; nothing to revoke
    return cache.set_add(_jti_key(jti), seconds_left)


def revoke_all_for_user(subject: str) -> bool:
    """Invalidates every token issued to this user before right now.

    Used on password reset and "log out all devices". One key, regardless
    of how many sessions exist.
    """
    ttl = settings.refresh_token_days * 86_400
    cache.cache_set(_epoch_key(subject), _now().timestamp(), ttl)
    return cache.available()


# Kept so older callers and tests keep working after the rewrite.
def verify_access_token(token: str) -> Optional[dict]:
    return decode_token(token, ACCESS)
