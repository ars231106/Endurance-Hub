import os
from datetime import datetime, timedelta

from jose import JWTError, jwt

# The secret key signs every token; anyone holding it could forge identities.
# Overridable via environment variable so production never uses the default.
SECRET_KEY = os.getenv("SECRET_KEY", "supersecretkey")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 12


def create_access_token(data: dict):
    # Copy so we never mutate the caller's dict, then stamp an expiry time.
    to_encode = data.copy()
    expiration_time = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode.update({"exp": expiration_time})

    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def verify_access_token(token: str):
    # jwt.decode checks both the signature (tamper-proof) and the expiry.
    # Any failure -> None, so callers have a single "invalid" signal.
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
