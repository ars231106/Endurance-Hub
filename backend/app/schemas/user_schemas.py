import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

# Each rule is a pattern plus the wording used in the error message, so the
# API can tell the user exactly what's missing instead of a vague "invalid
# password". The frontend shows the same list as a live checklist.
PASSWORD_RULES = [
    (r"[a-z]", "a lowercase letter"),
    (r"[A-Z]", "an uppercase letter"),
    (r"\d", "a number"),
    (r"[^A-Za-z0-9]", "a special character"),
]
PASSWORD_MIN_LENGTH = 8


class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=128)

    @field_validator("password")
    @classmethod
    def password_is_strong(cls, value: str) -> str:
        missing = [label for pattern, label in PASSWORD_RULES if not re.search(pattern, value)]
        if missing:
            raise ValueError("Password must contain " + ", ".join(missing))
        return value


class UserLogin(BaseModel):
    email: str
    password: str


class ProfileUpdate(BaseModel):
    # Ranges are deliberately wide but physiologically sane, so a typo
    # like a resting HR of 400 is rejected before it skews any maths.
    birth_year: Optional[int] = Field(default=None, ge=1900, le=2100)
    resting_hr: Optional[int] = Field(default=None, ge=25, le=120)
    max_hr: Optional[int] = Field(default=None, ge=100, le=230)


class UserOut(BaseModel):
    # Response shape for a user: note there is NO password field here,
    # so a hash can never leak into an API response by accident.
    id: int
    name: str
    email: str
    # Non-null when the account is scheduled for deletion; the frontend
    # uses it to show the "cancel deletion" banner.
    delete_requested_at: Optional[datetime] = None

    # Optional physiology for heart-rate based effort estimates.
    birth_year: Optional[int] = None
    resting_hr: Optional[int] = None
    max_hr: Optional[int] = None

    # Lets FastAPI build this schema directly from a SQLAlchemy User object.
    model_config = {"from_attributes": True}


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class OTPVerify(BaseModel):
    email: str
    # Exactly 6 digits - anything else is rejected before our code runs.
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class OTPResend(BaseModel):
    email: str


class AccountDelete(BaseModel):
    # Deleting an account requires re-entering the password, so a stolen
    # (still-valid) token alone can't destroy someone's data.
    password: str
