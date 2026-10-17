from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String

from app.models.base import Base


class PasswordReset(Base):
    """A pending password-reset request.

    The token is stored HASHED, never in plain text. A reset token is a
    temporary password - anyone holding one can take over the account - so
    a leaked database must not hand over working tokens. Same reasoning as
    password_hash on the user table.
    """

    __tablename__ = "password_resets"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, nullable=False, index=True)
    # SHA-256 of the token. Indexed because lookup is always by this value.
    token_hash = Column(String, nullable=False, unique=True, index=True)
    expires_at = Column(DateTime, nullable=False)
    # Set the moment the token is spent, so a replayed link is rejected
    # even inside the expiry window.
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    # Recorded for audit: which address requested the reset.
    requested_ip = Column(String, nullable=True)
