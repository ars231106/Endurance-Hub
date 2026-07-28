from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.activity import Activity
from app.models.otp import EmailOTP
from app.models.user import User

# Requested deletions become permanent after this many days.
GRACE_PERIOD_DAYS = 5


def purge_expired_accounts(db: Session) -> int:
    """Permanently deletes every account whose grace period has passed,
    along with all its data. Runs at startup and on each login."""
    cutoff = datetime.utcnow() - timedelta(days=GRACE_PERIOD_DAYS)
    expired = (
        db.query(User)
        .filter(User.delete_requested_at.isnot(None), User.delete_requested_at <= cutoff)
        .all()
    )
    for user in expired:
        # Children first (activities, OTPs), then the user row itself,
        # so foreign-key constraints are never violated.
        db.query(Activity).filter(Activity.user_id == user.id).delete()
        db.query(EmailOTP).filter(EmailOTP.email == user.email).delete()
        db.delete(user)
    db.commit()
    return len(expired)
