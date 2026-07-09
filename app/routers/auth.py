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
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.auth import (
    DeleteAccountRequest,
    ForgotPasswordRequest,
    LoginEventOut,
    LoginRequest,
    RefreshRequest,
    ResetPasswordRequest,
    SignupRequest,
    TokenResponse,
    UserOut,
    VerifyEmailRequest,
)

router = APIRouter(prefix="/auth", tags=["auth"])


async def _issue_tokens(db: AsyncSession, user: User) -> TokenResponse:
    """
    Issues a fresh access+refresh pair for a user — shared by signup,
    login, reset-password, and /refresh itself, so all four stay
    consistent by construction rather than by four separate call
    sites remembering to do the same thing the same way.

    The refresh token is generated and hashed with the exact same
    primitives as password-reset/email-verification tokens
    (generate_reset_token/hash_reset_token) — architecturally it's the
    same kind of thing: a random, single-use, SHA-256-hashed value
    looked up by its hash. Reusing the name is a slight misnomer (this
    isn't literally a "reset" token) but reusing the actual crypto
    primitive is the right call rather than writing a near-identical
    second implementation.
    """
    access_token = create_access_token(subject=user.id)
    raw_refresh, refresh_hash = generate_reset_token()
    db.add(RefreshToken(user_id=user.id, token_hash=refresh_hash))
    await db.commit()
    return TokenResponse(access_token=access_token, refresh_token=raw_refresh, user=UserOut.model_validate(user))


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
    # is created, not after waiting on Resend's API.
    raw_token, token_hash = generate_reset_token()
    db.add(EmailVerificationToken(user_id=user.id, token_hash=token_hash))
    await db.commit()
    verify_link = f"{settings.frontend_url.rstrip('/')}/?verify_token={raw_token}"
    background_tasks.add_task(email_sender.send_verification, user.email, verify_link)

    return await _issue_tokens(db, user)


@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")
async def login(request: Request, payload: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    email = payload.email.lower()
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    generic_error = HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")

    success = user is not None and user.is_active and await verify_password_async(payload.password, user.hashed_password)

    # Logged before raising, so a failed attempt is recorded exactly
    # like a successful one.
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

    return await _issue_tokens(db, user)


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("30/hour")
async def refresh(request: Request, payload: RefreshRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    """
    Trades a valid refresh token for a fresh access+refresh pair — no
    password needed, since the refresh token itself is the proof of a
    previously-authenticated session.

    ROTATING: the presented refresh token is revoked here regardless
    of outcome (a used or expired token is never valid again), and a
    brand new one is issued alongside the new access token. This means
    a refresh token is single-use — replaying an old one (e.g. one an
    attacker captured) fails immediately once the legitimate device
    has refreshed even once, rather than remaining silently valid
    forever alongside the real device's session.
    """
    invalid_error = HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token.")

    token_hash = hash_reset_token(payload.refresh_token)
    stored = (await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))).scalar_one_or_none()

    if stored is None or stored.revoked_at is not None:
        raise invalid_error

    # Same SQLite-vs-Postgres timezone-naive gotcha as password reset
    # tokens.
    expires_at = stored.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise invalid_error

    user = await db.get(User, stored.user_id)
    if user is None or not user.is_active:
        raise invalid_error

    stored.revoked_at = datetime.now(timezone.utc)
    await db.commit()

    return await _issue_tokens(db, user)


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

    expires_at = reset.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise invalid_token_error

    user.hashed_password = await hash_password_async(payload.new_password)
    reset.used_at = datetime.now(timezone.utc)
    await db.commit()

    return await _issue_tokens(db, user)


@router.get("/login-history", response_model=list[LoginEventOut])
async def login_history(
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[LoginEvent]:
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
    soft deactivate. No grace period; the confirmation step
    (re-entering the password) is the safety net against an
    accidental call, not an undo window afterward.
    """
    if not await verify_password_async(payload.password, current_user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Incorrect password")

    await db.execute(delete(EncryptedLedger).where(EncryptedLedger.user_id == current_user.id))
    await db.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == current_user.id))
    await db.execute(delete(EmailVerificationToken).where(EmailVerificationToken.user_id == current_user.id))
    await db.execute(delete(LoginEvent).where(LoginEvent.user_id == current_user.id))
    await db.execute(delete(RefreshToken).where(RefreshToken.user_id == current_user.id))
    await db.delete(current_user)
    await db.commit()


@router.post("/verify-email", response_model=UserOut)
async def verify_email(payload: VerifyEmailRequest, db: AsyncSession = Depends(get_db)) -> User:
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
