from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.email import email_sender
from app.core.limiter import limiter
from app.core.reset_tokens import generate_reset_token, hash_reset_token
from app.core.security import create_access_token, hash_password, verify_password
from app.core.config import settings
from app.models.encrypted_ledger import EncryptedLedger
from app.models.login_event import LoginEvent
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User
from app.schemas.auth import (
    DeleteAccountRequest,
    ForgotPasswordRequest,
    LoginEventOut,
    LoginRequest,
    ResetPasswordRequest,
    SignupRequest,
    TokenResponse,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def signup(request: Request, payload: SignupRequest, db: Session = Depends(get_db)) -> TokenResponse:
    existing = db.query(User).filter(User.email == payload.email.lower()).first()
    if existing:
        # Deliberately vague — confirming an email is NOT registered is
        # itself a data leak (account enumeration). Same message either way.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Could not create account with these details")

    user = User(
        email=payload.email.lower(),
        hashed_password=hash_password(payload.password),
        display_name=payload.display_name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(subject=user.id)
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")
def login(request: Request, payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    email = payload.email.lower()
    user = db.query(User).filter(User.email == email).first()
    generic_error = HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")

    success = user is not None and user.is_active and verify_password(payload.password, user.hashed_password)

    # Logged before raising, so a failed attempt is recorded exactly
    # like a successful one — the whole point is visibility into
    # attempts that DIDN'T work (repeated failures against one email
    # is the brute-force pattern this makes visible to a human, not
    # just silently caught by rate limiting in the moment).
    db.add(LoginEvent(
        user_id=user.id if user else None,
        email=email,
        success=success,
        ip_address=request.client.host if request.client else None,
    ))
    if success:
        user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    if not success:
        raise generic_error

    token = create_access_token(subject=user.id)
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def read_current_user(current_user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(current_user)


@router.post("/forgot-password", status_code=status.HTTP_200_OK)
@limiter.limit("3/hour")
def forgot_password(request: Request, payload: ForgotPasswordRequest, db: Session = Depends(get_db)) -> dict[str, str]:
    """
    Always returns the same generic response whether or not the email
    is registered — confirming an email's existence via this endpoint
    is itself an account-enumeration leak, same principle as login.
    """
    generic_response = {"message": "If that email is registered, a reset link has been sent."}

    user = db.query(User).filter(User.email == payload.email.lower()).first()
    if user is None:
        return generic_response

    raw_token, token_hash = generate_reset_token()
    reset = PasswordResetToken(user_id=user.id, token_hash=token_hash)
    db.add(reset)
    db.commit()

    reset_link = f"{settings.frontend_url.rstrip('/')}/?reset_token={raw_token}"
    email_sender.send_password_reset(user.email, reset_link)

    return generic_response


@router.post("/reset-password", response_model=TokenResponse)
@limiter.limit("5/hour")
def reset_password(request: Request, payload: ResetPasswordRequest, db: Session = Depends(get_db)) -> TokenResponse:
    token_hash = hash_reset_token(payload.token)
    reset = db.query(PasswordResetToken).filter(PasswordResetToken.token_hash == token_hash).first()

    invalid_token_error = HTTPException(status.HTTP_400_BAD_REQUEST, detail="This reset link is invalid or has expired.")

    if reset is None:
        raise invalid_token_error

    user = db.get(User, reset.user_id)
    if user is None or not user.is_active:
        raise invalid_token_error
    if reset.used_at is not None:
        raise invalid_token_error

    # SQLite doesn't preserve timezone info on DateTime columns the way
    # Postgres does — a value written as UTC-aware comes back naive on
    # SQLite. Since default_expiry() always generates UTC, a naive
    # value here can only mean "UTC with the tzinfo stripped by the
    # backend," so it's correct (not just a workaround) to assume that
    # on read. Without this, comparing it against an aware "now" raises
    # TypeError on SQLite while working fine on Postgres — the kind of
    # backend-specific bug that's invisible until it isn't.
    expires_at = reset.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise invalid_token_error

    user.hashed_password = hash_password(payload.new_password)
    reset.used_at = datetime.now(timezone.utc)
    db.commit()

    # Signing the user in immediately after a successful reset avoids
    # making them go through login again with the password they just set.
    token = create_access_token(subject=user.id)
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


@router.get("/login-history", response_model=list[LoginEventOut])
def login_history(
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[LoginEvent]:
    """
    The current user's own recent login activity — the practical
    "did someone try to sign into my account" view. Matched by email
    rather than just user_id, so failed attempts against this email
    before an account existed (or from a typo'd password that still
    resolved to the right user) are visible too, not just successes.
    """
    return (
        db.query(LoginEvent)
        .filter(LoginEvent.email == current_user.email)
        .order_by(LoginEvent.created_at.desc())
        .limit(min(limit, 100))
        .all()
    )


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("3/hour")
def delete_account(
    request: Request,
    payload: DeleteAccountRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """
    Permanently deletes the account and everything tied to it — not a
    soft deactivate. Matches the privacy policy's explicit promise:
    "permanently removes... all associated server-side data." No
    grace period, no recovery; the confirmation step (re-entering
    your password) is the safety net against an accidental call, not
    an undo window afterward.

    Deletes, in order: any encrypted Sync backup, password reset
    tokens, login history, then the user record itself. The user's
    local ledger (on-device) is completely unaffected — this only
    touches identity/server-side data. If Sync was enabled, that
    encrypted backup is now permanently gone too, same as forgetting
    the sync passphrase.
    """
    if not verify_password(payload.password, current_user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Incorrect password")

    db.query(EncryptedLedger).filter(EncryptedLedger.user_id == current_user.id).delete()
    db.query(PasswordResetToken).filter(PasswordResetToken.user_id == current_user.id).delete()
    db.query(LoginEvent).filter(LoginEvent.user_id == current_user.id).delete()
    db.delete(current_user)
    db.commit()
