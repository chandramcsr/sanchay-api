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


class RecurringRule(SanchayAppBase):
    """
    A schedule, not a transaction -- materialize_due_transactions (see
    recurring_service.py) turns due occurrences into real Transaction
    rows. last_materialized tracks catch-up state so re-running
    materialization is a no-op for anything already generated, and so
    a schedule that hasn't been checked in months still generates
    every occurrence it missed with the correct historical dates
    (matching ledger-app's own recurring.ts semantics, and the
    already-existing shared-expense recurring rules on this same
    backend) rather than only the most recent one.
    """

    __tablename__ = "recurring_rules"
    __table_args__ = (
        Index("ix_recurring_rules_clerk_user_id", "clerk_user_id"),
        Index("ix_recurring_rules_account_id", "account_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    clerk_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    # Signed, same convention as Transaction.amount -- positive =
    # income/credit, negative = expense/debit.
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False)
    category: Mapped[str | None] = mapped_column(String(128))
    # weekly / biweekly / monthly / quarterly / yearly -- matches
    # recurring_date_math.py's Frequency values exactly; validated at
    # the Pydantic schema layer, not a DB enum (see Account.type for
    # the same reasoning: adding a frequency later is a one-line
    # change here, not an ALTER TYPE migration).
    frequency: Mapped[str] = mapped_column(String(16), nullable=False)
    start_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    end_date: Mapped[date_type | None] = mapped_column(Date)
    last_materialized: Mapped[date_type | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=_now)
