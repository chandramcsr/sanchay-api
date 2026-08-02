import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import SanchayAppBase


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Account(SanchayAppBase):
    """
    Server-authoritative account. clerk_user_id, not a foreign key to a
    local users table — there isn't one. Clerk is the identity source
    of truth for this part of the app; a users table here would be a
    second, syncable-out-of-date copy of something Clerk already owns.
    """

    __tablename__ = "accounts"
    __table_args__ = (Index("ix_accounts_clerk_user_id", "clerk_user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    clerk_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    # checking / savings / credit_card / loan / investment. Plain string
    # rather than a Postgres enum type on purpose: adding a new account
    # type later is a one-line change here vs. an ALTER TYPE migration
    # (SQLite in dev/test doesn't support altering enum types at all).
    # Validated at the Pydantic schema layer instead.
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    # Numeric, not Float — money math on floating point accumulates real
    # rounding error. precision 14, scale 2 comfortably covers
    # real-world balances in cents.
    starting_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=_now)
