import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LoginEvent(Base):
    """
    A record of every login attempt — success and failure alike.

    Deliberately scoped to identity events only: when someone tried to
    sign in, whether it worked, from roughly where. Nothing about what
    they did once signed in, and certainly nothing about the ledger —
    same boundary as everything else in this service.

    user_id is nullable: a failed attempt against an email that isn't
    registered has no real user to attach to, but the attempt itself
    is still worth recording (repeated failures against one email,
    real or not, is exactly the brute-force pattern rate limiting
    exists to catch — this table is what lets a human actually see
    that pattern later, not just be silently throttled in the moment).
    """

    __tablename__ = "login_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)  # 45 = max IPv6 length
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False, index=True)
