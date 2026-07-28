from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String

from app.models.base import Base


class EmailOTP(Base):
    __tablename__ = "email_otps"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, nullable=False, index=True)
    # Stored as plain digits here for simplicity; production would store
    # a hash, exactly like passwords, in case the DB leaks.
    code = Column(String, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    # Attempt counter lets us lock the code after 5 wrong guesses,
    # so a 6-digit OTP can't be brute-forced.
    attempts = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
