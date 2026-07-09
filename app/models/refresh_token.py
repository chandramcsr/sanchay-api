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
    return _now() + timedelta(days=30)


class RefreshToken(Base):
    """
    Same shape and reasoning as PasswordResetToken/
    EmailVerificationToken: a single-use, opaque, SHA-256-hashed
    token — not a JWT itself, since a refresh token needs to be
    revocable (a JWT can't be un-issued once signed; a database row
    can be deleted or marked revoked). Looked up by value, which is
    why it's hashed fast (SHA-256) rather than slow-and-salted
    (bcrypt) like a password.

    ROTATING: each successful /auth/refresh call revokes this row and
    issues a brand new one. A refresh token is never reused — this
    means a stolen refresh token can be used at most once before the
    legitimate device's next refresh invalidates it, at which point
    both the attacker's and the real device's copies stop working and
    the user notices (forced to log in again) rather than an attacker
    silently holding a permanently-valid credential alongside the
    real device indefinitely.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=default_expiry, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
