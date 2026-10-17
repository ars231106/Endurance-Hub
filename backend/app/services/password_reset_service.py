"""Issuing and spending password-reset tokens.

Design decisions worth defending:

1. The token is random and long (32 bytes), not a 6-digit code. A code is
   fine for OTP because it is tied to an email the user already proved they
   control AND it has an attempt counter. A reset token travels in a URL and
   grants full account access, so it is sized to be unguessable outright.

2. Only a hash is stored, so the database alone cannot be used to reset
   anyone's password.

3. Comparison is constant-time. Comparing secrets with `==` leaks how many
   leading characters matched through timing, which is enough to recover a
   token one character at a time.

4. Issuing a token invalidates previous ones for that email, so a user who
   clicks "forgot password" three times does not leave three live keys.
"""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.models.password_reset import PasswordReset

log = logging.getLogger(__name__)

TOKEN_BYTES = 32
RESET_LIFETIME_MINUTES = 30


def _hash(token: str) -> str:
    """Plain SHA-256, deliberately not bcrypt.

    bcrypt's slowness defends short, low-entropy human passwords against
    brute force. This token has 256 bits of entropy from a CSPRNG, so brute
    force is already impossible and a slow hash would only add latency to
    every lookup.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def issue_reset_token(db: Session, email: str, ip: Optional[str] = None) -> str:
    """Creates a single-use token and returns the PLAIN value to email."""
    # Supersede any outstanding request for this address.
    db.query(PasswordReset).filter(
        PasswordReset.email == email,
        PasswordReset.used_at.is_(None),
    ).delete()

    token = secrets.token_urlsafe(TOKEN_BYTES)
    db.add(PasswordReset(
        email=email,
        token_hash=_hash(token),
        expires_at=datetime.utcnow() + timedelta(minutes=RESET_LIFETIME_MINUTES),
        requested_ip=ip,
    ))
    db.commit()
    return token


def consume_reset_token(db: Session, token: str) -> Tuple[Optional[str], str]:
    """Spends a token. Returns (email, 'ok') or (None, reason).

    Reasons: 'invalid', 'expired', 'used'.
    """
    token_hash = _hash(token)

    # Fetch by hash, then verify in constant time. The database lookup is
    # already an exact match, so this guards the in-memory comparison.
    record = (
        db.query(PasswordReset)
        .filter(PasswordReset.token_hash == token_hash)
        .first()
    )
    if record is None or not secrets.compare_digest(record.token_hash, token_hash):
        return None, "invalid"
    if record.used_at is not None:
        return None, "used"
    if datetime.utcnow() > record.expires_at:
        return None, "expired"

    # Mark spent before the password is changed. If anything downstream
    # fails, the token is still burnt - failing closed, not open.
    record.used_at = datetime.utcnow()
    db.commit()
    return record.email, "ok"


def purge_expired_resets(db: Session) -> int:
    """Housekeeping: drop tokens that can no longer be used."""
    cutoff = datetime.utcnow() - timedelta(days=1)
    removed = (
        db.query(PasswordReset)
        .filter(PasswordReset.expires_at < cutoff)
        .delete()
    )
    db.commit()
    return removed
