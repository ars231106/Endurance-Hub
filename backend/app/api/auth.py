import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import settings
from app.databases.database import get_db
from app.dependencies import get_current_token_payload, get_current_user
from app.models.user import User
from app.schemas.user_schemas import (
    AccountDelete,
    LogoutRequest,
    OTPResend,
    OTPVerify,
    PasswordChange,
    PasswordResetConfirm,
    PasswordResetRequest,
    ProfileUpdate,
    RefreshRequest,
    Token,
    UserCreate,
    UserLogin,
    UserOut,
)
from app.services import rate_limit
from app.services.account_service import purge_expired_accounts
from app.services.auth_services import (
    REFRESH,
    create_token_pair,
    decode_token,
    revoke_all_for_user,
    revoke_token,
)
from app.services.email_service import send_otp_email, send_password_reset_email
from app.services.otp_service import check_otp, issue_otp, seconds_until_resend_allowed
from app.services.password_reset_service import consume_reset_token, issue_reset_token
from app.services.security import hash_password, verify_password

log = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(user: UserCreate, request: Request, db: Session = Depends(get_db)):
    rate_limit.enforce(request, "register", user.email)

    # Check for duplicates first so the client gets a clear 409
    # instead of a raw database "unique constraint" crash.
    if db.query(User).filter(User.email == user.email).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    # Account starts unverified; only the bcrypt hash is stored.
    new_user = User(
        name=user.name,
        email=user.email,
        password_hash=hash_password(user.password),
        is_verified=False,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # Committed before the email goes out: delivery must never be able to
    # turn a successful registration into a failed request.
    send_otp_email(new_user.email, issue_otp(db, new_user.email))
    log.info("user registered", extra={"user_id": new_user.id})
    return new_user


@router.post("/verify-email", response_model=Token)
def verify_email(payload: OTPVerify, request: Request, db: Session = Depends(get_db)):
    rate_limit.enforce(request, "otp_verify", payload.email)

    user = db.query(User).filter(User.email == payload.email).first()
    if user is None:
        raise HTTPException(status_code=404, detail="No account with that email")
    if user.is_verified:
        raise HTTPException(status_code=400, detail="Email already verified")

    result = check_otp(db, payload.email, payload.code)
    errors = {
        "expired": "Code expired - request a new one",
        "locked": "Too many wrong attempts - request a new code",
        "wrong": "Incorrect code",
    }
    if result != "ok":
        raise HTTPException(status_code=400, detail=errors[result])

    # Verified! Log them straight in so the flow feels seamless.
    user.is_verified = True
    db.commit()
    return create_token_pair(user.email)


@router.post("/resend-otp")
def resend_otp(payload: OTPResend, request: Request, db: Session = Depends(get_db)):
    rate_limit.enforce(request, "otp_resend", payload.email)

    user = db.query(User).filter(User.email == payload.email).first()
    if user is None:
        raise HTTPException(status_code=404, detail="No account with that email")
    if user.is_verified:
        raise HTTPException(status_code=400, detail="Email already verified")

    # 60-second cooldown so the endpoint can't be used to spam inboxes.
    wait = seconds_until_resend_allowed(db, payload.email)
    if wait > 0:
        raise HTTPException(status_code=429, detail=f"Wait {wait}s before resending")

    send_otp_email(user.email, issue_otp(db, user.email))
    return {"message": "Verification code sent"}


@router.post("/login", response_model=Token)
def login(credentials: UserLogin, request: Request, db: Session = Depends(get_db)):
    # Before the password check, so a brute-force attempt is rejected
    # without paying bcrypt's cost - the limiter protects the CPU as much
    # as it protects the account.
    rate_limit.enforce(request, "login", credentials.email)

    # Opportunistic cleanup: accounts past their 5-day grace period are
    # permanently removed here (and at startup) - no cron job needed.
    purge_expired_accounts(db)

    user = db.query(User).filter(User.email == credentials.email).first()

    # Same vague 401 for "no such user" and "wrong password", so an
    # attacker can't probe which emails have accounts.
    if not user or not verify_password(credentials.password, user.password_hash):
        log.info("failed login", extra={"ip": rate_limit.client_ip(request)})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    # Correct password but unverified email -> 403 tells the frontend
    # to switch to the OTP screen.
    if not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email not verified",
        )

    log.info("login", extra={"user_id": user.id})
    return create_token_pair(user.email)


@router.post("/refresh", response_model=Token)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)):
    """Exchanges a refresh token for a new pair, and burns the old one.

    Rotation is the point: if a refresh token is stolen and replayed, the
    legitimate client's next refresh fails, so silent theft turns into a
    visible logout instead of indefinite access.
    """
    claims = decode_token(payload.refresh_token, REFRESH)
    if claims is None:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    # The account must still exist and still be usable.
    user = db.query(User).filter(User.email == claims["sub"]).first()
    if user is None or not user.is_verified:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    revoke_token(claims)
    return create_token_pair(user.email)


@router.post("/logout")
def logout(
    payload: Optional[LogoutRequest] = None,
    claims: dict = Depends(get_current_token_payload),
    current_user: User = Depends(get_current_user),
):
    """Ends this session.

    Revoking the access token alone is not enough: the refresh token would
    still be live, so "logging out" would last thirty minutes and then the
    client could quietly let itself back in. The client sends both.
    """
    revoked = revoke_token(claims)

    if payload is not None and payload.refresh_token:
        refresh_claims = decode_token(payload.refresh_token, REFRESH)
        # Only this user's own refresh token - a caller must not be able to
        # revoke someone else's session by sending their token.
        if refresh_claims and refresh_claims.get("sub") == current_user.email:
            revoke_token(refresh_claims)

    return {
        "message": "Logged out",
        # Honest about the degraded mode rather than silently pretending.
        "immediate": revoked,
    }


@router.post("/logout-all")
def logout_all(current_user: User = Depends(get_current_user)):
    """Invalidates every token issued to this account before now."""
    revoked = revoke_all_for_user(current_user.email)
    log.info("logout all devices", extra={"user_id": current_user.id})
    return {"message": "Logged out on all devices", "immediate": revoked}


# --- password reset -------------------------------------------------------

@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
def forgot_password(
    payload: PasswordResetRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Starts a reset. Always answers the same way.

    Returning 404 for an unknown address would turn this endpoint into a
    free account-enumeration oracle, so the response is identical whether
    or not the email exists - only the side effect differs.
    """
    rate_limit.enforce(request, "password_reset", payload.email)

    user = db.query(User).filter(User.email == payload.email).first()
    if user is not None and user.delete_requested_at is None:
        token = issue_reset_token(db, user.email, rate_limit.client_ip(request))
        link = f"{_frontend_base(request)}/?reset={token}"
        send_password_reset_email(user.email, link)
        log.info("password reset requested", extra={"user_id": user.id})

    return {"message": "If that email has an account, a reset link is on its way."}


@router.post("/reset-password", response_model=Token)
def reset_password(payload: PasswordResetConfirm, request: Request, db: Session = Depends(get_db)):
    rate_limit.enforce(request, "password_reset")

    email, result = consume_reset_token(db, payload.token)
    errors = {
        "invalid": "That reset link is not valid",
        "expired": "That reset link has expired - request a new one",
        "used": "That reset link has already been used",
    }
    if result != "ok":
        raise HTTPException(status_code=400, detail=errors[result])

    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise HTTPException(status_code=400, detail=errors["invalid"])

    user.password_hash = hash_password(payload.new_password)
    # Resetting a password almost always means "someone may have had
    # access". Every existing session dies, including the attacker's.
    user.is_verified = True
    db.commit()
    revoke_all_for_user(user.email)

    log.info("password reset completed", extra={"user_id": user.id})
    return create_token_pair(user.email)


@router.post("/me/change-password", response_model=Token)
def change_password(
    payload: PasswordChange,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rate_limit.enforce(request, "password_reset", current_user.email)

    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    current_user.password_hash = hash_password(payload.new_password)
    db.commit()
    revoke_all_for_user(current_user.email)

    # Old sessions are gone, including this one, so issue a fresh pair.
    return create_token_pair(current_user.email)


# --- profile --------------------------------------------------------------

@router.get("/me", response_model=UserOut)
def read_me(current_user: User = Depends(get_current_user)):
    # get_current_user already verified the token and loaded the user;
    # by the time this body runs, authentication is guaranteed.
    return current_user


@router.put("/me/profile", response_model=UserOut)
def update_profile(
    payload: ProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # exclude_unset so omitting a field leaves it alone, while explicitly
    # sending null clears it (useful for removing a bad max-HR override).
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(current_user, field, value)
    db.commit()
    db.refresh(current_user)
    return current_user


@router.post("/me/delete", response_model=UserOut)
def request_account_deletion(
    payload: AccountDelete,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Password re-entry required: a leaked token alone must not be able
    # to schedule the destruction of someone's account.
    if not verify_password(payload.password, current_user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect password")
    if current_user.delete_requested_at is not None:
        raise HTTPException(status_code=400, detail="Deletion already scheduled")

    # Soft delete: mark now, purge permanently after the 5-day grace
    # period so an accidental click is recoverable.
    current_user.delete_requested_at = datetime.utcnow()
    db.commit()
    db.refresh(current_user)
    log.info("account deletion requested", extra={"user_id": current_user.id})
    return current_user


@router.post("/me/cancel-deletion", response_model=UserOut)
def cancel_account_deletion(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.delete_requested_at is None:
        raise HTTPException(status_code=400, detail="No deletion scheduled")

    current_user.delete_requested_at = None
    db.commit()
    db.refresh(current_user)
    return current_user


def _frontend_base(request: Request) -> str:
    """Where reset links should point.

    Prefers an explicitly configured origin. Falling back to the request's
    own host is safe here only because the frontend is served by this same
    app; if they ever separate, PUBLIC_BASE_URL must be set, otherwise a
    spoofed Host header could redirect reset links to an attacker.
    """
    if settings.cors_origins:
        return settings.cors_origins[0].rstrip("/")
    return str(request.base_url).rstrip("/")
