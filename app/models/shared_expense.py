import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SharedExpense(Base):
    """
    One shared expense within a group — "Dinner, $120, Alice paid."
    The split itself lives in SharedExpenseSplit, one row per
    participant; this row is just the expense's own facts.

    paid_by is nullable + has no ON DELETE behavior enforced at the
    ORM level on purpose — see SharedExpenseSplit for the full
    reasoning on why deleted users are frozen, not cascaded away.
    """

    __tablename__ = "shared_expenses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("groups.id"), nullable=False, index=True)
    paid_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    paid_by_email_ref: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    paid_by_name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    # server_default, not just a Python-level default — this table may
    # already have rows once real usage exists by the time this
    # migrates, and a NOT NULL column with no server_default on a
    # non-empty table is exactly the migration class that broke a live
    # deploy once before in this project. Not repeating that.
    category: Mapped[str] = mapped_column(String(50), nullable=False, server_default="Other")
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    expense_date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
