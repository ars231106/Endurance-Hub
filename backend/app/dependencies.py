from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.databases.database import get_db
from app.models.user import User
from app.services.auth_services import verify_access_token

# Reads the JWT out of the "Authorization: Bearer <token>" header.
# Missing header -> automatic 401 before our code even runs.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    # One deliberately vague error for every failure mode, so an attacker
    # can't learn WHICH check failed (bad token vs. deleted user).
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    payload = verify_access_token(token)
    if payload is None:
        raise credentials_exception

    email = payload.get("sub")
    if email is None:
        raise credentials_exception

    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise credentials_exception

    # Endpoints receive the full SQLAlchemy User object, ready to use.
    return user
