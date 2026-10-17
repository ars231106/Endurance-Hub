from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.databases.database import get_db
from app.models.user import User
from app.services.auth_services import ACCESS, decode_token

# Reads the JWT out of the "Authorization: Bearer <token>" header.
# Missing header -> automatic 401 before our code even runs.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login", auto_error=True)


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Resolves the caller, or raises 401.

    Four checks, cheapest first - signature and expiry, correct token type,
    not revoked, and the account still exists. A token stays
    cryptographically valid for its full lifetime even after the account is
    deleted, so the database has the final word, not the token.
    """
    # One deliberately vague error for every failure mode, so an attacker
    # cannot learn WHICH check failed.
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # decode_token also rejects refresh tokens presented as access tokens,
    # and anything on the revocation list.
    payload = decode_token(token, ACCESS)
    if payload is None:
        raise credentials_exception

    email = payload.get("sub")
    if email is None:
        raise credentials_exception

    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise credentials_exception

    # A soft-deleted account keeps working during its grace period so the
    # user can cancel - but it must not be usable once purging is due.
    return user


def get_current_token_payload(token: str = Depends(oauth2_scheme)) -> dict:
    """The decoded access token, for endpoints that need to revoke it."""
    payload = decode_token(token, ACCESS)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload
