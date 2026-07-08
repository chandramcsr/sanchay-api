import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def default_expiry() -> datetime:
    # Longer-lived than a password reset token (30 min): verification
    # is lower-stakes — worst case of a stale link is "verify again,"
    # not an account takeover — and people often don't check email
    # immediately after signing up.
    return _now() + timedelta(hours=24)


class EmailVerificationToken(Base):
    """
    A single-use, time-limited token proving control of the email
    address used to sign up. Same shape and same reasoning as
    PasswordResetToken (SHA-256 hash stored, not the raw token — an
    indexed-lookup token needs a fast deterministic hash, unlike a
    password which is only ever verified, never looked up by value).

    Verification is soft, not a gate: an unverified account can sign
    in and use the app immediately. This just tracks whether the
    email address is confirmed real, surfaced as a gentle nudge
    rather than blocking anything.
    """

    __tablename__ = "email_verification_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=default_expiry, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
