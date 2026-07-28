import secrets
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.otp import EmailOTP

OTP_LIFETIME_MINUTES = 10
MAX_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 60


def issue_otp(db: Session, email: str) -> str:
    # One active code per email: delete old ones so a resend invalidates
    # any previous (possibly intercepted) code.
    db.query(EmailOTP).filter(EmailOTP.email == email).delete()

    # secrets (not random) - cryptographically unpredictable digits.
    code = f"{secrets.randbelow(1_000_000):06d}"
    db.add(EmailOTP(
        email=email,
        code=code,
        expires_at=datetime.utcnow() + timedelta(minutes=OTP_LIFETIME_MINUTES),
    ))
    db.commit()
    return code


def seconds_until_resend_allowed(db: Session, email: str) -> int:
    otp = db.query(EmailOTP).filter(EmailOTP.email == email).first()
    if otp is None:
        return 0
    elapsed = (datetime.utcnow() - otp.created_at).total_seconds()
    return max(0, int(RESEND_COOLDOWN_SECONDS - elapsed))


def check_otp(db: Session, email: str, code: str) -> str:
    """Returns 'ok', 'expired', 'locked', or 'wrong'."""
    otp = db.query(EmailOTP).filter(EmailOTP.email == email).first()
    if otp is None or datetime.utcnow() > otp.expires_at:
        return "expired"
    if otp.attempts >= MAX_ATTEMPTS:
        return "locked"

    if otp.code != code:
        # Count the failure BEFORE returning, so guesses are always limited.
        otp.attempts += 1
        db.commit()
        return "wrong"

    # Success: the code is single-use, so remove it immediately.
    db.query(EmailOTP).filter(EmailOTP.email == email).delete()
    db.commit()
    return "ok"
