from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.databases.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.user_schemas import (
    AccountDelete,
    OTPResend,
    OTPVerify,
    ProfileUpdate,
    Token,
    UserCreate,
    UserLogin,
    UserOut,
)
from app.services.account_service import purge_expired_accounts
from app.services.auth_services import create_access_token
from app.services.email_service import send_otp_email
from app.services.otp_service import check_otp, issue_otp, seconds_until_resend_allowed
from app.services.security import hash_password, verify_password

router = APIRouter(tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(user: UserCreate, db: Session = Depends(get_db)):
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

    # Send the verification code (prints to console in dev mode).
    send_otp_email(new_user.email, issue_otp(db, new_user.email))
    return new_user


@router.post("/verify-email", response_model=Token)
def verify_email(payload: OTPVerify, db: Session = Depends(get_db)):
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
    token = create_access_token({"sub": user.email})
    return {"access_token": token, "token_type": "bearer"}


@router.post("/resend-otp")
def resend_otp(payload: OTPResend, db: Session = Depends(get_db)):
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
def login(credentials: UserLogin, db: Session = Depends(get_db)):
    # Opportunistic cleanup: accounts past their 5-day grace period are
    # permanently removed here (and at startup) - no cron job needed.
    purge_expired_accounts(db)

    user = db.query(User).filter(User.email == credentials.email).first()

    # Same vague 401 for "no such user" and "wrong password", so an
    # attacker can't probe which emails have accounts.
    if not user or not verify_password(credentials.password, user.password_hash):
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

    token = create_access_token({"sub": user.email})
    return {"access_token": token, "token_type": "bearer"}


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
