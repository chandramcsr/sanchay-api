"""
Auth business logic. Routers stay thin (parse request, call a service
function, return the result) — everything that decides WHAT happens
(enumeration protection, token lifecycle, cascade-delete ordering)
lives here, not scattered across route handlers.

Services own the transaction boundary: repositories never call
db.commit() themselves, because a service often needs several
repository calls to succeed together as one unit of work before it's
safe to commit (account deletion touches five tables — either all of
it commits or none of it should).

Deliberately raises HTTPException directly rather than a separate
domain-exception hierarchy that routers would just translate back
into HTTPException anyway — at this codebase's size, that extra layer
would be indirection without a matching benefit.
"""

from datetime import datetime, timezone

from fastapi import BackgroundTasks, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.email import email_sender
from app.core.reset_tokens import generate_reset_token, hash_reset_token
from app.core.security import create_access_token, hash_password_async, verify_password_async
from app.models.login_event import LoginEvent
from app.models.user import User
from app.repositories import (
    email_verification_repository,
    login_event_repository,
    password_reset_repository,
    refresh_token_repository,
    user_repository,
)
from app.repositories import encrypted_ledger_repository
from app.services.shared_expense_service import freeze_user_references, reconnect_by_email


async def _issue_token_pair(db: AsyncSession, user: User) -> tuple[str, str]:
    """
    Issues a fresh (access_token, refresh_token) pair — shared by
    signup, login, reset_password, and refresh, so all four stay
    consistent by construction. Commits, because the refresh token is
    a new DB row that must persist to be redeemable later.

    The refresh token reuses generate_reset_token/hash_reset_token:
    architecturally a refresh token is the same kind of thing as a
    password-reset token (random, single-use, SHA-256-hashed, looked
    up by value), so it reuses the same primitive rather than a
    near-identical second implementation.
    """
    access_token = create_access_token(subject=user.id)
    raw_refresh, refresh_hash = generate_reset_token()
    refresh_token_repository.create(db, user_id=user.id, token_hash=refresh_hash)
    await db.commit()
    return access_token, raw_refresh


async def signup(
    db: AsyncSession, background_tasks: BackgroundTasks, *, email: str, password: str, display_name: str
) -> tuple[str, str, User, dict]:
    normalized_email = email.lower()
    if await user_repository.get_by_email(db, normalized_email):
        # Deliberately vague — confirming an email is NOT registered is
        # itself a data leak (account enumeration). Same message either way.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Could not create account with these details")

    hashed = await hash_password_async(password)
    user = user_repository.create(db, email=normalized_email, hashed_password=hashed, display_name=display_name)
    await db.commit()
    await db.refresh(user)

    # If this email has frozen shared-expense history (they deleted a
    # previous account and are signing up again with the same
    # address), reconnect it now — see shared_expense_service for the
    # full email_ref/freeze/reconnect design.
    reconnect_summary = await reconnect_by_email(db, new_user=user)

    # Best-effort — verification is a soft nudge, not a gate. Sent as a
    # background task so the response returns the moment the account
    # exists, not after waiting on Resend's API.
    raw_token, token_hash = generate_reset_token()
    email_verification_repository.create(db, user_id=user.id, token_hash=token_hash)
    await db.commit()
    verify_link = f"{settings.frontend_url.rstrip('/')}/?verify_token={raw_token}"
    background_tasks.add_task(email_sender.send_verification, user.email, verify_link)

    access_token, refresh_token = await _issue_token_pair(db, user)
    return access_token, refresh_token, user, reconnect_summary


async def login(db: AsyncSession, request: Request, *, email: str, password: str) -> tuple[str, str, User]:
    normalized_email = email.lower()
    user = await user_repository.get_by_email(db, normalized_email)
    generic_error = HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")

    success = user is not None and user.is_active and await verify_password_async(password, user.hashed_password)

    # Logged before raising, so a failed attempt is recorded exactly
    # like a successful one — visibility into attempts that DIDN'T
    # work (repeated failures against one email is the brute-force
    # pattern this makes visible to a human) is the whole point.
    login_event_repository.create(
        db,
        user_id=user.id if user else None,
        email=normalized_email,
        success=success,
        ip_address=request.client.host if request.client else None,
    )
    if success:
        user.last_login_at = datetime.now(timezone.utc)
    await db.commit()

    if not success:
        raise generic_error

    access_token, refresh_token = await _issue_token_pair(db, user)
    return access_token, refresh_token, user


async def forgot_password(db: AsyncSession, background_tasks: BackgroundTasks, *, email: str) -> None:
    """
    Always completes "successfully" from the caller's perspective
    regardless of whether the email is registered — the router
    returns the same generic message either way. Confirming an
    email's existence via this endpoint would itself be an
    account-enumeration leak, same principle as login.
    """
    user = await user_repository.get_by_email(db, email.lower())
    if user is None:
        return

    raw_token, token_hash = generate_reset_token()
    password_reset_repository.create(db, user_id=user.id, token_hash=token_hash)
    await db.commit()

    reset_link = f"{settings.frontend_url.rstrip('/')}/?reset_token={raw_token}"
    background_tasks.add_task(email_sender.send_password_reset, user.email, reset_link)


async def reset_password(db: AsyncSession, *, token: str, new_password: str) -> tuple[str, str, User]:
    token_hash = hash_reset_token(token)
    reset = await password_reset_repository.get_by_token_hash(db, token_hash)

    invalid_token_error = HTTPException(status.HTTP_400_BAD_REQUEST, detail="This reset link is invalid or has expired.")

    if reset is None:
        raise invalid_token_error

    user = await user_repository.get_by_id(db, reset.user_id)
    if user is None or not user.is_active:
        raise invalid_token_error
    if reset.used_at is not None:
        raise invalid_token_error

    # SQLite doesn't preserve timezone info on DateTime columns the way
    # Postgres does — a value written as UTC-aware comes back naive on
    # SQLite. Since default_expiry() always generates UTC, a naive
    # value here can only mean "UTC with the tzinfo stripped," so it's
    # correct (not just a workaround) to assume that on read.
    expires_at = reset.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise invalid_token_error

    user.hashed_password = await hash_password_async(new_password)
    reset.used_at = datetime.now(timezone.utc)
    await db.commit()

    # Signing the user in immediately after a successful reset avoids
    # making them go through login again with the password they just set.
    access_token, refresh_token = await _issue_token_pair(db, user)
    return access_token, refresh_token, user


async def refresh(db: AsyncSession, *, refresh_token: str) -> tuple[str, str, User]:
    """
    Trades a valid refresh token for a fresh (access, refresh) pair —
    no password, since the refresh token itself is proof of a
    previously-authenticated session.

    ROTATING: the presented token is revoked here on use, and a brand
    new one issued alongside the new access token. A refresh token is
    single-use — replaying an old one (e.g. one an attacker captured)
    fails immediately once the legitimate device has refreshed even
    once, rather than remaining silently valid forever.
    """
    invalid_error = HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token.")

    token_hash = hash_reset_token(refresh_token)
    stored = await refresh_token_repository.get_by_token_hash(db, token_hash)

    if stored is None or stored.revoked_at is not None:
        raise invalid_error

    # Same SQLite-vs-Postgres timezone-naive gotcha as reset tokens.
    expires_at = stored.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise invalid_error

    user = await user_repository.get_by_id(db, stored.user_id)
    if user is None or not user.is_active:
        raise invalid_error

    refresh_token_repository.revoke(stored)
    await db.commit()

    access_token, new_refresh = await _issue_token_pair(db, user)
    return access_token, new_refresh, user


async def get_login_history(db: AsyncSession, *, current_user: User, limit: int) -> list[LoginEvent]:
    """
    Matched by email rather than just user_id, so failed attempts
    against this email before an account existed (or from a typo'd
    password that still resolved to the right user) are visible too.
    """
    return await login_event_repository.list_by_email(db, current_user.email, min(limit, 100))


async def delete_account(db: AsyncSession, *, current_user: User, password: str) -> None:
    """
    Permanently deletes the account and everything tied to it — not a
    soft deactivate. Matches the privacy policy's explicit promise.
    No grace period; the confirmation step (re-entering the password)
    is the safety net against an accidental call, not an undo window
    afterward.

    ONE exception to "everything tied to it is deleted": shared
    expenses with other people. freeze_user_references() is the
    single, narrow integration point this function has with the
    shared-expenses module — it snapshots this user's display name
    onto every group/expense/split they're part of and nulls the
    user_id reference, so a real bilateral debt survives as history
    ("Name (account deleted)") even though this account, and
    everything else about it, is genuinely gone. Called BEFORE the
    user row is deleted, since the snapshot needs the still-live
    display_name to copy from.
    """
    if not await verify_password_async(password, current_user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Incorrect password")

    await freeze_user_references(db, user_id=current_user.id)

    await encrypted_ledger_repository.delete_by_user_id(db, current_user.id)
    await password_reset_repository.delete_by_user_id(db, current_user.id)
    await email_verification_repository.delete_by_user_id(db, current_user.id)
    await login_event_repository.delete_by_user_id(db, current_user.id)
    await refresh_token_repository.delete_by_user_id(db, current_user.id)
    await user_repository.delete(db, current_user)
    await db.commit()


async def verify_email(db: AsyncSession, *, token: str) -> User:
    """
    No authentication required — the token itself, proving control of
    the email inbox, is the credential.
    """
    token_hash = hash_reset_token(token)
    verification = await email_verification_repository.get_by_token_hash(db, token_hash)

    invalid_token_error = HTTPException(status.HTTP_400_BAD_REQUEST, detail="This verification link is invalid or has expired.")

    if verification is None or verification.used_at is not None:
        raise invalid_token_error

    user = await user_repository.get_by_id(db, verification.user_id)
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


async def resend_verification(db: AsyncSession, background_tasks: BackgroundTasks, *, current_user: User) -> str:
    if current_user.is_verified:
        return "Your email is already verified."

    raw_token, token_hash = generate_reset_token()
    email_verification_repository.create(db, user_id=current_user.id, token_hash=token_hash)
    await db.commit()
    verify_link = f"{settings.frontend_url.rstrip('/')}/?verify_token={raw_token}"
    background_tasks.add_task(email_sender.send_verification, current_user.email, verify_link)
    return "Verification email sent."
