import uuid
from datetime import date as date_type
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import SanchayAppBase


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SavingsGoal(SanchayAppBase):
    """
    A goal's progress is its linked account's current balance -- no
    separate contribution ledger to keep in sync, no double-bookkeeping.
    This assumes the account is used for that goal alone (matches
    ledger-app's own savingsGoals.ts exactly, including that same
    assumption) -- fine for the common case (a dedicated savings
    account per goal), and more honest than trying to tag individual
    transactions as "goal contributions" when the money is fungible
    anyway.
    """

    __tablename__ = "savings_goals"
    __table_args__ = (
        Index("ix_savings_goals_clerk_user_id", "clerk_user_id"),
        Index("ix_savings_goals_account_id", "account_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    clerk_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    target_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    target_date: Mapped[date_type | None] = mapped_column(Date)
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=_now)
