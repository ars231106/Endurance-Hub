from sqlalchemy import Boolean, Column, DateTime, Integer, String

from app.models.base import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    # False until the user proves they own the email via OTP;
    # login is blocked while this is False.
    is_verified = Column(Boolean, nullable=False, default=False)
    # Soft-delete timestamp: set when the user requests deletion,
    # purged permanently once the 5-day grace period passes.
    delete_requested_at = Column(DateTime, nullable=True)

    # Optional physiology, used to turn an average heart rate into an
    # effort score. All nullable: the app works fine without a wearable.
    birth_year = Column(Integer, nullable=True)   # max HR is estimated from age
    resting_hr = Column(Integer, nullable=True)   # enables the Karvonen method
    max_hr = Column(Integer, nullable=True)       # overrides the age estimate
