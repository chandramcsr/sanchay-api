import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Settlement(Base):
    """
    A real payment between two people that reduces their outstanding
    balance — "Bob paid Alice $40 back." Deliberately NOT tied to a
    specific SharedExpense/split: balances are computed as a running
    net between two people across everything they've shared, the same
    way Splitwise itself settles (one net number per friend, not
    expense-by-expense reconciliation), so a settlement just records
    the payment and the balance calculation nets it against the total.
    """

    __tablename__ = "settlements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    from_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    from_email_ref: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    from_name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    to_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    to_email_ref: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    to_name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    settled_date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
