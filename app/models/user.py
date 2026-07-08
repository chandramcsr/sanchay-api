import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    """
    Identity only. Deliberately does NOT store financial data — no
    transactions, no account balances, nothing from the Sanchay ledger
    itself. This service exists to answer "who is this person" so a
    future sync layer has something to attach encrypted data to; it is
    not where that data lives. Keeping the boundary explicit here is
    what keeps the "no data collected" privacy story true as this
    service grows.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    # Convenience field — the last successful login, without a join to
    # login_events for the common case of "when did I last sign in."
    # The full history (including failed attempts) lives in
    # login_events; this is just the fast path for the one number
    # people actually ask for.
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
