import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SharedExpenseSplit(Base):
    """
    One participant's share of one SharedExpense. This is where the
    "freeze, don't cascade" account-deletion policy actually lives:

    user_id is nullable, with no cascading foreign-key behavior
    enforced. When someone deletes their Sanchay account, their
    personal data (users row, transactions, everything else) is
    genuinely gone — but a debt that was real and bilateral doesn't
    stop existing just because one side left. The delete_account flow
    calls freeze_user_references() (shared_expense_service.py) BEFORE
    removing the user row: it copies the person's current display
    name into name_snapshot on every split/membership row they're
    part of, then sets user_id to NULL. The historical record — "Bob
    (account deleted): owed $40" — survives; Bob's actual account and
    everything else about him does not.

    This is disclosed to the user before they delete their account
    (see the updated delete-account confirmation copy) — not a silent
    surprise.
    """

    __tablename__ = "shared_expense_splits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    shared_expense_id: Mapped[str] = mapped_column(String(36), ForeignKey("shared_expenses.id"), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    share_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
