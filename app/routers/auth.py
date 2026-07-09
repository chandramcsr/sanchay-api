from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.email import email_sender
from app.core.limiter import limiter
from app.core.reset_tokens import generate_reset_token, hash_reset_token
from app.core.security import create_access_token, hash_password_async, verify_password_async
from app.models.email_verification_token import EmailVerificationToken
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
    VerifyEmailRequest,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def signup(
    request: Request, payload: SignupRequest, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)
) -> TokenResponse:
    existing = (await db.execute(select(User).where(User.email == payload.email.lower()))).scalar_one_or_none()
    if existing:
        # Deliberately vague — confirming an email is NOT registered is
        # itself a data leak (account enumeration). Same message either way.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Could not create account with these details")

    user = User(
        email=payload.email.lower(),
        hashed_password=await hash_password_async(payload.password),
        display_name=payload.display_name,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # Best-effort — verification is a soft nudge, not a gate. Sent as a
    # background task: the response goes back the moment the account
    # is created, not after waiting on Resend's API. A slow or down
    # email provider no longer holds this request (and its DB
    # connection) open any longer than necessary — and since it's
    # fire-and-forget, a failure here still doesn't block signup; the
    # user can always request a fresh link later via
    # /auth/resend-verification either way.
    raw_token, token_hash = generate_reset_token()
    db.add(EmailVerificationToken(user_id=user.id, token_hash=token_hash))
    await db.commit()
    verify_link = f"{settings.frontend_url.rstrip('/')}/?verify_token={raw_token}"
    background_tasks.add_task(email_sender.send_verification, user.email, verify_link)

    token = create_access_token(subject=user.id)
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")
async def login(request: Request, payload: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    email = payload.email.lower()
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    generic_error = HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")

    success = user is not None and user.is_active and await verify_password_async(payload.password, user.hashed_password)

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
    await db.commit()

    if not success:
        raise generic_error

    token = create_access_token(subject=user.id)
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
async def read_current_user(current_user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(current_user)


@router.post("/forgot-password", status_code=status.HTTP_200_OK)
@limiter.limit("3/hour")
async def forgot_password(
    request: Request, payload: ForgotPasswordRequest, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    """
    Always returns the same generic response whether or not the email
    is registered — confirming an email's existence via this endpoint
    is itself an account-enumeration leak, same principle as login.
    """
    generic_response = {"message": "If that email is registered, a reset link has been sent."}

    user = (await db.execute(select(User).where(User.email == payload.email.lower()))).scalar_one_or_none()
    if user is None:
        return generic_response

    raw_token, token_hash = generate_reset_token()
    reset = PasswordResetToken(user_id=user.id, token_hash=token_hash)
    db.add(reset)
    await db.commit()

    reset_link = f"{settings.frontend_url.rstrip('/')}/?reset_token={raw_token}"
    background_tasks.add_task(email_sender.send_password_reset, user.email, reset_link)

    return generic_response


@router.post("/reset-password", response_model=TokenResponse)
@limiter.limit("5/hour")
async def reset_password(request: Request, payload: ResetPasswordRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    token_hash = hash_reset_token(payload.token)
    reset = (
        await db.execute(select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash))
    ).scalar_one_or_none()

    invalid_token_error = HTTPException(status.HTTP_400_BAD_REQUEST, detail="This reset link is invalid or has expired.")

    if reset is None:
        raise invalid_token_error

    user = await db.get(User, reset.user_id)
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

    user.hashed_password = await hash_password_async(payload.new_password)
    reset.used_at = datetime.now(timezone.utc)
    await db.commit()

    # Signing the user in immediately after a successful reset avoids
    # making them go through login again with the password they just set.
    token = create_access_token(subject=user.id)
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


@router.get("/login-history", response_model=list[LoginEventOut])
async def login_history(
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[LoginEvent]:
    """
    The current user's own recent login activity — the practical
    "did someone try to sign into my account" view. Matched by email
    rather than just user_id, so failed attempts against this email
    before an account existed (or from a typo'd password that still
    resolved to the right user) are visible too, not just successes.
    """
    result = await db.execute(
        select(LoginEvent)
        .where(LoginEvent.email == current_user.email)
        .order_by(LoginEvent.created_at.desc())
        .limit(min(limit, 100))
    )
    return list(result.scalars().all())


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("3/hour")
async def delete_account(
    request: Request,
    payload: DeleteAccountRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
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
    if not await verify_password_async(payload.password, current_user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Incorrect password")

    await db.execute(delete(EncryptedLedger).where(EncryptedLedger.user_id == current_user.id))
    await db.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == current_user.id))
    await db.execute(delete(EmailVerificationToken).where(EmailVerificationToken.user_id == current_user.id))
    await db.execute(delete(LoginEvent).where(LoginEvent.user_id == current_user.id))
    await db.delete(current_user)
    await db.commit()


@router.post("/verify-email", response_model=UserOut)
async def verify_email(payload: VerifyEmailRequest, db: AsyncSession = Depends(get_db)) -> User:
    """
    No authentication required — the token itself, proving control of
    the email inbox, is the credential. Works whether or not the
    visitor currently has a session on this device (e.g. verifying
    from a different browser than the one they signed up in).
    """
    token_hash = hash_reset_token(payload.token)
    verification = (
        await db.execute(select(EmailVerificationToken).where(EmailVerificationToken.token_hash == token_hash))
    ).scalar_one_or_none()

    invalid_token_error = HTTPException(status.HTTP_400_BAD_REQUEST, detail="This verification link is invalid or has expired.")

    if verification is None or verification.used_at is not None:
        raise invalid_token_error

    user = await db.get(User, verification.user_id)
    if user is None:
        raise invalid_token_error

    # Same SQLite-vs-Postgres timezone-naive gotcha as password reset
    # tokens — see the identical comment there for why this is correct,
    # not just a workaround.
    expires_at = verification.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise invalid_token_error

    user.is_verified = True
    verification.used_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/resend-verification", status_code=status.HTTP_200_OK)
@limiter.limit("3/hour")
async def resend_verification(
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    if current_user.is_verified:
        return {"message": "Your email is already verified."}

    raw_token, token_hash = generate_reset_token()
    db.add(EmailVerificationToken(user_id=current_user.id, token_hash=token_hash))
    await db.commit()
    verify_link = f"{settings.frontend_url.rstrip('/')}/?verify_token={raw_token}"
    background_tasks.add_task(email_sender.send_verification, current_user.email, verify_link)
    return {"message": "Verification email sent."}
