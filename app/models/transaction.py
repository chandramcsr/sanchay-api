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


class Transaction(SanchayAppBase):
    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_clerk_user_id", "clerk_user_id"),
        Index("ix_transactions_account_id", "account_id"),
        # Almost every real query is "this user's transactions in a date
        # range" (a statement, a month view, a budget calculation) — a
        # composite index matches that access pattern directly instead
        # of making Postgres intersect two separate single-column
        # indexes on every query.
        Index("ix_transactions_clerk_user_id_date", "clerk_user_id", "date"),
        Index("ix_transactions_transfer_group_id", "transfer_group_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    clerk_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    # Signed: positive = income/credit, negative = expense/debit. One
    # column, one sign convention, rather than separate amount+type
    # columns that can drift out of sync with each other.
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    description: Mapped[str | None] = mapped_column(String(512))
    category: Mapped[str | None] = mapped_column(String(128))
    date: Mapped[date_type] = mapped_column(Date, nullable=False)
    # Links the two legs of a transfer (an expense leg on the source
    # account, an income leg on the destination) -- matches
    # ledger-app's own model exactly (transferGroupId in
    # src/types.ts/lib/accounts.ts). NULL for every ordinary
    # transaction. Balances are unaffected by this (both legs are real
    # money movement, counted normally); spend/income statistics
    # exclude anything with a non-null value here -- moving your own
    # money between accounts is neither earning nor spending, which is
    # exactly why a credit card payment is a transfer from checking,
    # not an expense.
    transfer_group_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=_now)
