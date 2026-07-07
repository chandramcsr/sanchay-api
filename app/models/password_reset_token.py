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
    return _now() + timedelta(minutes=30)


class PasswordResetToken(Base):
    """
    A single-use, time-limited token for resetting a forgotten password.

    We store a SHA-256 hash of the token, not the token itself — same
    principle as password hashing (a DB leak shouldn't hand out usable
    tokens), but SHA-256 rather than bcrypt here on purpose: reset
    tokens are looked up BY VALUE (find the row matching this exact
    token), which needs a fast, deterministic hash for an indexed
    lookup. Bcrypt is deliberately slow and salted differently every
    time, which is right for passwords (never looked up by value, only
    verified against one known user) and wrong for this.
    """

    __tablename__ = "password_reset_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=default_expiry, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
